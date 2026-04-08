"""
Async worker process.

Runs as a completely separate Python process (scripts/run_worker.py).
Loop:  XREADGROUP from Redis Stream → route to task handler → XACK → publish result.

On startup, drains any unacknowledged messages from prior run (id="0") before
consuming new messages (id=">"). This ensures at-least-once delivery across restarts.

Workers NEVER send SMS directly. They publish results and the always-on
FastAPI service (ResponseListener) delivers the final message.
"""
import asyncio
import json
import logging
from typing import Optional

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "worker-group"
CONSUMER_NAME = "worker-1"    # unique per worker replica
_MAX_CONCURRENCY = 5          # D-13: cap parallel job processing


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._redis: Optional[aioredis.Redis] = None
        self._semaphore: Optional[asyncio.Semaphore] = None

    async def start(self) -> None:
        self._redis = await aioredis.from_url(
            self.settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        # Create consumer group; ignore error if it already exists (D-12, Pitfall 4)
        try:
            await self._redis.xgroup_create(
                self.settings.queue_name,
                CONSUMER_GROUP,
                id="0",           # Start from beginning so no messages are skipped
                mkstream=True,    # Create stream if it doesn't exist yet
            )
            logger.info("Consumer group created: %s", CONSUMER_GROUP)
        except Exception as exc:
            if "BUSYGROUP" in str(exc):
                logger.debug("Consumer group already exists (expected on restart): %s", CONSUMER_GROUP)
            else:
                raise

        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
        logger.info(
            "Worker started — stream=%s group=%s consumer=%s concurrency=%d redis=%s",
            self.settings.queue_name,
            CONSUMER_GROUP,
            CONSUMER_NAME,
            _MAX_CONCURRENCY,
            self.settings.redis_url,
        )
        await self._loop()

    async def _loop(self) -> None:
        # Phase 1: drain pending messages from prior run (unacknowledged)
        # Use id="0" to get messages this consumer has claimed but not ACKed
        await self._drain_pending()

        # Phase 2: consume new messages indefinitely
        while True:
            try:
                results = await self._redis.xreadgroup(
                    CONSUMER_GROUP,
                    CONSUMER_NAME,
                    {self.settings.queue_name: ">"},   # ">" = only new, undelivered messages
                    count=1,
                    block=5000,    # block 5s, then yield for signal handling
                )
                if results:
                    _stream, messages = results[0]
                    for entry_id, fields in messages:
                        payload = json.loads(fields["data"])
                        asyncio.create_task(
                            self._process_and_ack(entry_id, payload)
                        )
            except asyncio.CancelledError:
                logger.info("Worker shutting down")
                break
            except Exception as exc:
                logger.error("Worker loop error: %s", exc, exc_info=True)
                await asyncio.sleep(1)

    async def _drain_pending(self) -> None:
        """Process unacknowledged messages from a prior run before consuming new ones."""
        logger.info("Draining pending messages from prior run...")
        drained = 0
        while True:
            results = await self._redis.xreadgroup(
                CONSUMER_GROUP,
                CONSUMER_NAME,
                {self.settings.queue_name: "0"},   # "0" = pending/unacknowledged
                count=10,
                block=0,    # non-blocking for drain
            )
            if not results or not results[0][1]:
                break
            _stream, messages = results[0]
            for entry_id, fields in messages:
                payload = json.loads(fields["data"])
                await self._process_and_ack(entry_id, payload)
                drained += 1
        if drained:
            logger.info("Drained %d pending messages from prior run", drained)

    async def _process_and_ack(self, entry_id: str, payload: dict) -> None:
        """Process one job then XACK. Semaphore caps concurrent execution."""
        async with self._semaphore:
            try:
                await self._process(payload)
            except Exception as exc:
                logger.error("Job processing failed entry_id=%s: %s", entry_id, exc, exc_info=True)
            finally:
                # XACK after processing — remove from PEL so it won't replay on restart.
                # We ACK even on failure to avoid infinite retry loops; failed jobs are
                # logged with exc_info=True and the user receives a fallback error response.
                await self._redis.xack(
                    self.settings.queue_name,
                    CONSUMER_GROUP,
                    entry_id,
                )

    async def _process(self, payload: dict) -> None:
        job_id: str = payload.get("job_id", "unknown")
        phone: str = payload.get("phone", "")
        intent: str = payload.get("intent", "general")

        logger.info("Processing job_id=%s intent=%s phone=%s", job_id, intent, phone)

        # Handle scheduler-sourced proactive jobs (source="scheduler")
        source = payload.get("source")
        if source == "scheduler":
            job_type = payload.get("type", "")
            logger.info("Processing proactive job_id=%s type=%s user=%s", job_id, job_type, payload.get("user_id", "")[:8])
            try:
                from app.tasks.proactive import (
                    handle_morning_briefing, handle_evening_recap,
                    handle_goal_checkin, handle_weekly_digest,
                    handle_task_reminder,
                    handle_profile_nudge, handle_smart_checkin,
                    handle_insight_observation,
                    handle_goal_coaching,
                )
                if job_type == "morning_briefing":
                    result = await handle_morning_briefing(payload)
                elif job_type == "evening_recap":
                    result = await handle_evening_recap(payload)
                elif job_type == "goal_checkin":
                    result = await handle_goal_checkin(payload)
                elif job_type == "weekly_digest":
                    result = await handle_weekly_digest(payload)
                elif job_type == "task_reminder":
                    result = await handle_task_reminder(payload)
                elif job_type == "profile_nudge":
                    result = await handle_profile_nudge(payload)
                elif job_type == "smart_checkin":
                    result = await handle_smart_checkin(payload)
                elif job_type == "insight_observation":
                    result = await handle_insight_observation(payload)
                elif job_type == "goal_coaching":
                    result = await handle_goal_coaching(payload)
                else:
                    logger.warning("Unknown proactive job type=%s", job_type)
                    result = {"job_id": job_id, "phone": phone, "response": ""}
            except Exception as exc:
                logger.error("Proactive job %s failed: %s", job_id, exc, exc_info=True)
                result = {"job_id": job_id, "phone": phone, "response": ""}

            # Ensure identity fields propagate for proactive jobs too
            for key in ("user_id", "address", "channel"):
                if key not in result and payload.get(key):
                    result[key] = payload[key]

            # Only publish if there's a response to send
            if result.get("response"):
                await self._publish_result(job_id, result)
            return

        try:
            from app.tasks.router import route_job
            result = await route_job(payload)
        except Exception as exc:
            logger.error("Job %s failed: %s", job_id, exc, exc_info=True)
            result = {
                "job_id": job_id,
                "phone": phone,
                "address": payload.get("address", phone),
                "channel": payload.get("channel", "sms"),
                "user_id": payload.get("user_id", ""),
                "response": "Sorry, I hit a snag on that one. Could you try again?",
                "error": str(exc),
            }

        # Ensure identity fields propagate to the result so ResponseListener can
        # route delivery and attribute the user correctly (important for Slack
        # where address != phone).
        for key in ("user_id", "address", "channel"):
            if key not in result and payload.get(key):
                result[key] = payload[key]

        await self._publish_result(job_id, result)

    async def _publish_result(self, job_id: str, result: dict) -> None:
        await self._redis.setex(f"result:{job_id}", 300, json.dumps(result))
        await self._redis.publish(self.settings.response_channel, job_id)
        logger.info(
            "Published result job_id=%s preview=%r",
            job_id,
            result.get("response", "")[:60],
        )

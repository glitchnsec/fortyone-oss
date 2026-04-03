#!/usr/bin/env python3
"""
Scheduler process — runs as a separate Docker service.

Loop: poll Redis sorted set for due jobs -> push to Redis Stream -> reschedule.
Frequency: every 30 seconds.

On startup, seeds morning/evening briefing jobs for all active users who have
proactive messaging enabled. Uses idempotency keys to prevent duplicates.

Jobs pushed to the same Redis Stream the worker consumes (per D-03).
Proactive jobs are tagged with source="scheduler" so the pipeline knows not
to re-enter them into the inbound flow (Pitfall 1 prevention).
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

# Make sure the project root is on the path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

POLL_INTERVAL = 30  # seconds
BATCH_SIZE = 50


async def scheduler_loop():
    settings = get_settings()
    r = await aioredis.from_url(
        settings.redis_url, encoding="utf-8", decode_responses=True,
    )
    logger.info("Scheduler started — polling every %ds  redis=%s", POLL_INTERVAL, settings.redis_url)

    poll_count = 0
    while True:
        try:
            now = time.time()
            ready = await r.zrangebyscore(
                "scheduled_jobs", "-inf", now, start=0, num=BATCH_SIZE,
            )
            poll_count += 1
            # Heartbeat every 10 polls (~5 min) so logs show the scheduler is alive
            if poll_count % 10 == 0:
                pending_total = await r.zcard("scheduled_jobs")
                logger.info("SCHEDULER_HEARTBEAT  polls=%d  pending_jobs=%d", poll_count, pending_total)
            if ready:
                logger.info("SCHEDULER_POLL  found=%d due jobs", len(ready))
            for job_data in ready:
                removed = await r.zrem("scheduled_jobs", job_data)
                if removed:  # Atomic claim — prevents duplicate processing
                    payload = json.loads(job_data)
                    payload["source"] = "scheduler"

                    # Rate limit check before dispatching
                    from app.core.throttle import check_rate_limit, check_dead_man_switch
                    user_id = payload.get("user_id", "")
                    if user_id:
                        safe = await check_dead_man_switch(r, user_id)
                        if not safe:
                            logger.warning("SCHEDULER_SKIP dead_man_switch user=%s", user_id[:8])
                            continue
                        allowed = await check_rate_limit(r, user_id)
                        if not allowed:
                            logger.info("SCHEDULER_SKIP rate_limited user=%s", user_id[:8])
                            continue

                    # Idempotency check — key varies by job type:
                    # - task_reminder: per task_id (user can have multiple reminders per day)
                    # - briefing/recap: per user per day (one morning briefing per day)
                    from app.core.throttle import check_idempotency
                    job_type = payload.get("type", "unknown")
                    if job_type == "task_reminder":
                        # Per-task idempotency — each task fires exactly once
                        idem_key = f"proactive:{user_id}:task_reminder:{payload.get('task_id', 'unknown')}"
                    else:
                        # Per-user-per-day for recurring proactive jobs
                        idem_key = f"proactive:{user_id}:{job_type}:{int(now // 86400)}"
                    is_new = await check_idempotency(r, idem_key)
                    if not is_new:
                        logger.info("SCHEDULER_SKIP duplicate idem_key=%s", idem_key)
                        continue

                    await r.xadd(settings.queue_name, {"data": json.dumps(payload)})
                    logger.info(
                        "SCHEDULED_DISPATCH  type=%s  user=%s",
                        payload.get("type"), user_id[:8],
                    )

                    # Reschedule recurring jobs
                    reschedule_at = payload.get("reschedule_at")
                    if reschedule_at:
                        next_payload = {**payload}
                        next_payload.pop("reschedule_at", None)
                        # Compute next occurrence
                        from app.core.scheduler_utils import compute_next_run
                        next_time = compute_next_run(
                            payload.get("cron", "0 8 * * *"),
                            payload.get("timezone", "America/New_York"),
                        )
                        if next_time:
                            next_payload["reschedule_at"] = True
                            await r.zadd(
                                "scheduled_jobs",
                                {json.dumps(next_payload): next_time},
                            )
                            logger.info(
                                "RESCHEDULED  type=%s  user=%s  next=%s",
                                payload.get("type"), user_id[:8],
                                datetime.fromtimestamp(next_time, tz=timezone.utc).isoformat(),
                            )

        except Exception as exc:
            logger.error("Scheduler error: %s", exc, exc_info=True)

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(scheduler_loop())
    except KeyboardInterrupt:
        logger.info("Scheduler stopped")

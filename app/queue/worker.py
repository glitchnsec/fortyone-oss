"""
Async worker process.

Runs as a completely separate Python process (scripts/run_worker.py).
Loop:  BRPOP job from Redis → route to task handler → store result → publish.

Workers NEVER send SMS directly.  They publish results and the always-on
FastAPI service (ResponseListener) delivers the final message.
"""
import asyncio
import json
import logging
from typing import Optional

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._redis: Optional[aioredis.Redis] = None

    async def start(self) -> None:
        self._redis = await aioredis.from_url(
            self.settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        logger.info(
            "Worker started — listening on queue=%s redis=%s",
            self.settings.queue_name,
            self.settings.redis_url,
        )
        await self._loop()

    async def _loop(self) -> None:
        while True:
            try:
                # BRPOP blocks up to 5s, then yields control so we can catch signals
                result = await self._redis.brpop(self.settings.queue_name, timeout=5)
                if result:
                    _, raw = result
                    payload = json.loads(raw)
                    asyncio.create_task(self._process(payload))
            except asyncio.CancelledError:
                logger.info("Worker shutting down")
                break
            except Exception as exc:
                logger.error("Worker loop error: %s", exc, exc_info=True)
                await asyncio.sleep(1)

    async def _process(self, payload: dict) -> None:
        job_id: str = payload.get("job_id", "unknown")
        phone: str = payload.get("phone", "")
        intent: str = payload.get("intent", "general")

        logger.info("Processing job_id=%s intent=%s phone=%s", job_id, intent, phone)

        try:
            from app.tasks.router import route_job
            result = await route_job(payload)
        except Exception as exc:
            logger.error("Job %s failed: %s", job_id, exc, exc_info=True)
            result = {
                "job_id": job_id,
                "phone": phone,
                "response": "Sorry, I hit a snag on that one. Could you try again?",
                "error": str(exc),
            }

        await self._publish_result(job_id, result)

    async def _publish_result(self, job_id: str, result: dict) -> None:
        # Store result with 5-minute TTL so the listener can read it
        await self._redis.setex(f"result:{job_id}", 300, json.dumps(result))
        # Notify the ResponseListener
        await self._redis.publish(self.settings.response_channel, job_id)
        logger.info(
            "Published result job_id=%s preview=%r",
            job_id,
            result.get("response", "")[:60],
        )

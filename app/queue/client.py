"""
Redis-backed queue client (producer side).

The queue is a Redis Stream. Producer: XADD. Consumer: XREADGROUP (in worker.py).
Job results are stored as Redis keys with a TTL and announced via pub/sub.
"""
import json
import logging
import uuid
from typing import Optional

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)


class QueueClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._redis: Optional[aioredis.Redis] = None

    async def connect(self) -> None:
        self._redis = await aioredis.from_url(
            self.settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        logger.info("QueueClient connected to %s", self.settings.redis_url)

    async def disconnect(self) -> None:
        if self._redis:
            await self._redis.aclose()

    async def push_job(self, payload: dict) -> str:
        """Enqueue a job onto the Redis Stream. Returns the generated job_id."""
        job_id = str(uuid.uuid4())
        payload = {**payload, "job_id": job_id}
        # XADD appends to the stream; "*" lets Redis auto-generate the entry ID
        await self._redis.xadd(
            self.settings.queue_name,
            {"data": json.dumps(payload)},
        )
        logger.debug("Pushed job_id=%s intent=%s", job_id, payload.get("intent"))
        return job_id

    async def get_result(self, job_id: str) -> Optional[dict]:
        """Read a stored result (non-blocking)."""
        raw = await self._redis.get(f"result:{job_id}")
        return json.loads(raw) if raw else None


# Singleton used by both the API and the pipeline
queue_client = QueueClient()

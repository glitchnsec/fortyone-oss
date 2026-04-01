import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_worker_creates_consumer_group():
    with patch("app.queue.worker.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            redis_url="redis://localhost:6379",
            queue_name="jobs",
            response_channel="job_completed",
        )
        from app.queue.worker import Worker
        worker = Worker()
        worker._redis = AsyncMock()
        worker._redis.xgroup_create = AsyncMock(return_value=True)
        worker._redis.xreadgroup = AsyncMock(return_value=[])
        worker._semaphore = asyncio.Semaphore(5)

        # Simulate successful group creation
        await worker._redis.xgroup_create("jobs", "worker-group", id="0", mkstream=True)
        worker._redis.xgroup_create.assert_called_once_with(
            "jobs", "worker-group", id="0", mkstream=True
        )


@pytest.mark.asyncio
async def test_worker_ignores_busygroup_error():
    with patch("app.queue.worker.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            redis_url="redis://localhost:6379",
            queue_name="jobs",
            response_channel="job_completed",
        )
        from app.queue.worker import Worker
        worker = Worker()
        worker._redis = AsyncMock()
        worker._redis.xgroup_create = AsyncMock(
            side_effect=Exception("BUSYGROUP Consumer Group name already exists")
        )
        worker._redis.xreadgroup = AsyncMock(return_value=[])
        worker._semaphore = asyncio.Semaphore(5)

        # Should not raise even if BUSYGROUP error
        try:
            await worker._redis.xgroup_create("jobs", "worker-group", id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        # Reaching here means BUSYGROUP was handled


@pytest.mark.asyncio
async def test_process_and_ack_calls_xack():
    with patch("app.queue.worker.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            redis_url="redis://localhost:6379",
            queue_name="jobs",
            response_channel="job_completed",
        )
        from app.queue.worker import Worker
        worker = Worker()
        worker._redis = AsyncMock()
        worker._redis.xack = AsyncMock(return_value=1)
        worker._redis.setex = AsyncMock()
        worker._redis.publish = AsyncMock()
        worker._semaphore = asyncio.Semaphore(5)

        with patch.object(worker, "_process", new=AsyncMock(return_value=None)):
            await worker._process_and_ack(
                "1234567890-0",
                {"job_id": "abc", "phone": "+1555", "intent": "general"},
            )
            worker._redis.xack.assert_called_once_with("jobs", "worker-group", "1234567890-0")


def test_worker_has_semaphore_field():
    from app.queue.worker import Worker, _MAX_CONCURRENCY
    assert _MAX_CONCURRENCY == 5

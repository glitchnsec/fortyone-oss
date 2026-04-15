"""Tests for worker failure modes: timeout, publish failure, and ResponseListener reconnection.

Covers the robustness fixes:
- _process_and_ack publishes fallback on timeout and XACKs
- _process_and_ack publishes fallback on _process exception and XACKs
- _process_and_ack does NOT XACK when both _process and fallback publish fail
- _publish_result logs and re-raises on Redis failure
- ResponseListener reconnects and closes old pubsub on disconnect
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


def _make_worker():
    """Create a Worker with mocked Redis and settings."""
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
        return worker


PAYLOAD = {
    "job_id": "test-job-123",
    "phone": "+15550001234",
    "address": "+15550001234",
    "channel": "sms",
    "user_id": "user-abc",
    "intent": "needs_manager",
}
ENTRY_ID = "1234567890-0"


# ── _process_and_ack timeout ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_timeout_publishes_fallback_and_xacks():
    """When _process exceeds timeout, a fallback response is published and job is XACKed."""
    worker = _make_worker()

    # Only raise TimeoutError on the FIRST wait_for call (_process),
    # let subsequent calls (_publish_result's Redis ops) pass through.
    original_wait_for = asyncio.wait_for
    call_count = 0

    async def selective_wait_for(coro, *, timeout):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            coro.close()
            raise asyncio.TimeoutError()
        return await original_wait_for(coro, timeout=timeout)

    with patch("asyncio.wait_for", side_effect=selective_wait_for):
        await worker._process_and_ack(ENTRY_ID, PAYLOAD)

    # Verify fallback was published
    worker._redis.setex.assert_called_once()
    stored_data = json.loads(worker._redis.setex.call_args[0][2])
    assert stored_data["response"] == "Sorry, that took too long. Could you try again?"
    assert stored_data["job_id"] == "test-job-123"

    # Verify XACK was called (published=True after fallback)
    worker._redis.xack.assert_called_once_with("jobs", "worker-group", ENTRY_ID)


# ── _process_and_ack exception with fallback ──────────────────────────────


@pytest.mark.asyncio
async def test_process_exception_publishes_fallback_and_xacks():
    """When _process raises, a fallback error response is published and job is XACKed."""
    worker = _make_worker()

    with patch.object(worker, "_process", new=AsyncMock(side_effect=RuntimeError("LLM crashed"))):
        await worker._process_and_ack(ENTRY_ID, PAYLOAD)

    # Verify fallback was published
    worker._redis.setex.assert_called_once()
    stored_data = json.loads(worker._redis.setex.call_args[0][2])
    assert "snag" in stored_data["response"]
    assert stored_data["job_id"] == "test-job-123"

    # Verify XACK was called
    worker._redis.xack.assert_called_once_with("jobs", "worker-group", ENTRY_ID)


# ── _process_and_ack: both process and fallback fail → no XACK ───────────


@pytest.mark.asyncio
async def test_process_and_fallback_fail_no_xack():
    """When _process raises AND fallback publish also fails, job is NOT XACKed."""
    worker = _make_worker()

    # _process fails
    with patch.object(worker, "_process", new=AsyncMock(side_effect=RuntimeError("boom"))):
        # _publish_result also fails (Redis down)
        worker._redis.setex = AsyncMock(side_effect=ConnectionError("Redis gone"))

        await worker._process_and_ack(ENTRY_ID, PAYLOAD)

    # Verify XACK was NOT called (job stays pending for retry)
    worker._redis.xack.assert_not_called()


# ── _publish_result raises on Redis failure ───────────────────────────────


@pytest.mark.asyncio
async def test_publish_result_raises_on_redis_failure():
    """_publish_result re-raises when Redis setex fails."""
    worker = _make_worker()
    worker._redis.setex = AsyncMock(side_effect=ConnectionError("Redis down"))

    with pytest.raises(ConnectionError, match="Redis down"):
        await worker._publish_result("job-1", {"response": "hello"})


@pytest.mark.asyncio
async def test_publish_result_raises_on_publish_failure():
    """_publish_result re-raises when Redis publish fails."""
    worker = _make_worker()
    worker._redis.setex = AsyncMock()  # setex succeeds
    worker._redis.publish = AsyncMock(side_effect=ConnectionError("Redis gone"))

    with pytest.raises(ConnectionError, match="Redis gone"):
        await worker._publish_result("job-1", {"response": "hello"})


# ── _publish_result happy path ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publish_result_stores_and_publishes():
    """_publish_result stores result with TTL and publishes job_id."""
    worker = _make_worker()

    await worker._publish_result("job-1", {"response": "hello", "job_id": "job-1"})

    # Verify setex called with key, TTL, and JSON
    worker._redis.setex.assert_called_once()
    args = worker._redis.setex.call_args[0]
    assert args[0] == "result:job-1"
    assert args[1] == 300  # TTL
    assert json.loads(args[2])["response"] == "hello"

    # Verify publish called with channel and job_id
    worker._redis.publish.assert_called_once_with("job_completed", "job-1")


# ── ResponseListener reconnection ────────────────────────────────────────


@pytest.mark.asyncio
async def test_response_listener_closes_pubsub_on_reconnect():
    """ResponseListener closes old pubsub object when reconnecting."""
    from app.core.pipeline import ResponseListener

    listener = ResponseListener(channels={"sms": MagicMock()})

    # Create mock pubsub that ends listen() after one iteration
    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.aclose = AsyncMock()

    # listen() yields nothing then stops (simulating disconnect)
    async def empty_listen():
        return
        yield  # Makes it an async generator that immediately stops

    mock_pubsub.listen = empty_listen

    mock_redis = MagicMock()
    mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

    call_count = 0

    # Patch sleep and make start() exit after 2 iterations
    async def fake_sleep(seconds):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError()  # Exit the reconnect loop

    # get_settings is imported inside start() via "from app.config import get_settings"
    with patch("app.core.pipeline.asyncio.sleep", side_effect=fake_sleep):
        with patch("app.config.get_settings", return_value=MagicMock(response_channel="job_completed")):
            with pytest.raises(asyncio.CancelledError):
                await listener.start(mock_redis)

    # Verify pubsub.aclose() was called on each reconnect
    assert mock_pubsub.aclose.call_count >= 1


# ── Happy path: _process_and_ack success ──────────────────────────────────


@pytest.mark.asyncio
async def test_process_and_ack_success_xacks():
    """Normal path: _process succeeds, job is XACKed."""
    worker = _make_worker()

    with patch.object(worker, "_process", new=AsyncMock(return_value=None)):
        await worker._process_and_ack(ENTRY_ID, PAYLOAD)

    worker._redis.xack.assert_called_once_with("jobs", "worker-group", ENTRY_ID)

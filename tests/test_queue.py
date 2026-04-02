"""Unit tests for Redis Streams queue client (producer side).

Tests push_job serialization, job_id generation, and result retrieval.
Uses mock Redis to avoid needing a real Redis instance.
"""
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.queue.client import QueueClient


@pytest.fixture
def queue():
    """QueueClient with mocked Redis connection."""
    client = QueueClient()
    client._redis = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_push_job_returns_uuid(queue):
    """push_job returns a UUID job_id string."""
    job_id = await queue.push_job({"intent": "reminder", "message": "test"})
    assert job_id
    assert len(job_id) == 36  # UUID format: 8-4-4-4-12


@pytest.mark.asyncio
async def test_push_job_calls_xadd(queue):
    """push_job uses XADD (Redis Streams), not LPUSH."""
    await queue.push_job({"intent": "recall", "message": "test"})
    queue._redis.xadd.assert_called_once()
    call_args = queue._redis.xadd.call_args
    stream_name = call_args[0][0]
    data = call_args[0][1]
    assert stream_name == "jobs"  # default queue_name
    assert "data" in data
    payload = json.loads(data["data"])
    assert payload["intent"] == "recall"
    assert "job_id" in payload


@pytest.mark.asyncio
async def test_push_job_includes_job_id_in_payload(queue):
    """The job_id is included in the serialized payload, not just returned."""
    job_id = await queue.push_job({"phone": "+1555"})
    call_data = queue._redis.xadd.call_args[0][1]["data"]
    payload = json.loads(call_data)
    assert payload["job_id"] == job_id
    assert payload["phone"] == "+1555"


@pytest.mark.asyncio
async def test_get_result_returns_parsed_json(queue):
    """get_result deserializes the stored JSON result."""
    queue._redis.get = AsyncMock(return_value='{"response": "done"}')
    result = await queue.get_result("test-id")
    assert result == {"response": "done"}
    queue._redis.get.assert_called_once_with("result:test-id")


@pytest.mark.asyncio
async def test_get_result_returns_none_for_missing(queue):
    """get_result returns None when no result exists."""
    queue._redis.get = AsyncMock(return_value=None)
    result = await queue.get_result("missing-id")
    assert result is None

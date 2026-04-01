import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_push_job_uses_xadd():
    from app.queue.client import QueueClient
    client = QueueClient()
    client._redis = AsyncMock()
    client._redis.xadd = AsyncMock(return_value=b"1234567890-0")

    job_id = await client.push_job({"intent": "reminder", "phone": "+15551234567", "body": "test"})

    assert client._redis.xadd.called
    assert not hasattr(client._redis, "lpush") or not client._redis.lpush.called
    assert isinstance(job_id, str) and len(job_id) == 36  # UUID4 format


@pytest.mark.asyncio
async def test_push_job_encodes_job_id_in_data():
    from app.queue.client import QueueClient
    client = QueueClient()
    client._redis = AsyncMock()
    client._redis.xadd = AsyncMock(return_value=b"1234567890-0")

    job_id = await client.push_job({"intent": "reminder", "phone": "+15551234567"})

    call_args = client._redis.xadd.call_args
    stream_name, fields = call_args[0]
    data = json.loads(fields["data"])
    assert data["job_id"] == job_id
    assert data["intent"] == "reminder"

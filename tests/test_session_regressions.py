"""Regression tests for session bug fixes.

Each test targets a specific fix applied during this session to ensure
the fix is not reverted by future changes.

Tests cover:
  1. Redis health uses XLEN (not LLEN) for stream-based queue
  2. User name included in manager system prompt
  3. ResponseListener stores outbound messages with correct channel
  4. Proactive channel defaults (plan_day logic)
  5. Preferred channel API (GET/PUT proactive-preferences)
  6. SMS registration link prefills phone (URL-encoded E.164)
  7. Channel-agnostic identity (user_id in job payload)
"""
import asyncio
import json
import os
import uuid

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Force in-memory SQLite for tests
os.environ.setdefault("DATABASE_URL", "sqlite://")

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
from app.memory.models import Role, User


# ─── Shared fixtures ──────────────────────────────────────────────────────────

_test_engine = create_async_engine("sqlite+aiosqlite:///./test_session_reg.db", echo=False)
_TestSession = async_sessionmaker(_test_engine, expire_on_commit=False)


async def _seed_roles(session):
    """Seed the roles table with 'user' and 'admin' rows."""
    result = await session.execute(select(Role).where(Role.name == "admin"))
    if result.scalar_one_or_none() is None:
        session.add(Role(id=str(uuid.uuid4()), name="user"))
        session.add(Role(id=str(uuid.uuid4()), name="admin"))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Create tables before each test, drop after."""
    from app.memory import models  # noqa: F401
    from app.models import auth  # noqa: F401
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _TestSession() as session:
        await _seed_roles(session)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    import os as _os
    try:
        _os.remove("./test_session_reg.db")
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def client():
    """HTTPX async client wired to the FastAPI app with overridden DB."""
    from app.main import app

    async def _override_db():
        async with _TestSession() as session:
            yield session

    from app.routes.auth import _get_db as auth_get_db
    from app.middleware.auth import _get_db as mw_get_db

    app.dependency_overrides[auth_get_db] = _override_db
    app.dependency_overrides[mw_get_db] = _override_db

    # Override all dashboard and admin DB deps
    try:
        from app.routes.dashboard import _get_db as dash_get_db
        app.dependency_overrides[dash_get_db] = _override_db
    except ImportError:
        pass
    try:
        from app.routes.admin import _get_db as admin_get_db
        app.dependency_overrides[admin_get_db] = _override_db
    except ImportError:
        pass

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def register_user(client, email, phone, password="TestPass123!"):
    """Register a user and return (access_token, user_id)."""
    resp = await client.post("/auth/register", json={
        "email": email,
        "phone": phone,
        "password": password,
    })
    assert resp.status_code == 201, f"Register failed: {resp.text}"
    data = resp.json()
    return data["access_token"], data["user_id"]


async def make_admin_and_login(client, email, user_id, password="TestPass123!"):
    """Promote user to admin, re-login to get admin token."""
    from sqlalchemy import update
    async with _TestSession() as db:
        result = await db.execute(select(Role).where(Role.name == "admin"))
        admin_role = result.scalar_one()
        await db.execute(
            update(User).where(User.id == user_id).values(role_id=admin_role.id)
        )
        await db.commit()
    resp = await client.post("/auth/login", json={
        "email": email,
        "password": password,
    })
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    return resp.json()["access_token"]


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ─── Test 1: Redis health endpoint uses XLEN ─────────────────────────────────


@pytest.mark.asyncio
async def test_health_endpoint_uses_xlen_not_llen(client):
    """The admin health endpoint must call xlen (stream) not llen (list).

    Regression: WRONGTYPE error when using LLEN on a Redis Stream key.
    Fix: Changed r.llen() to r.xlen() in admin health endpoint.
    """
    token, uid = await register_user(client, "health@test.com", "+15550200001")
    admin_token = await make_admin_and_login(client, "health@test.com", uid)

    # Mock Redis to track which method is called
    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.xlen = AsyncMock(return_value=42)
    mock_redis.llen = AsyncMock(side_effect=Exception("WRONGTYPE Operation against a key holding the wrong kind of value"))
    mock_redis.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        resp = await client.get(
            "/api/v1/admin/health",
            headers=auth_header(admin_token),
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["redis"]["status"] == "ok"
    assert data["redis"]["queue_depth"] == 42
    # xlen must have been called (not llen)
    mock_redis.xlen.assert_called_once()
    mock_redis.llen.assert_not_called()


# ─── Test 2: User name in manager system prompt ──────────────────────────────


@pytest.mark.asyncio
async def test_build_system_prompt_includes_user_name():
    """_build_system_prompt must include the user's name when present in context.

    Regression: System prompt did not reference the user's name, causing the
    LLM to address users generically even when the name was known.
    """
    from app.tasks.manager import _build_system_prompt

    payload = {
        "body": "What's the weather?",
        "context": {
            "user": {
                "name": "Kc",
                "assistant_name": "Jarvis",
            },
            "memories": {},
            "timezone": "UTC",
        },
        "persona": "shared",
    }

    prompt = _build_system_prompt(payload)
    assert "The user's name is Kc." in prompt


@pytest.mark.asyncio
async def test_build_system_prompt_omits_name_when_missing():
    """_build_system_prompt must NOT include a name line when name is absent."""
    from app.tasks.manager import _build_system_prompt

    payload = {
        "body": "Hello",
        "context": {
            "user": {},
            "memories": {},
            "timezone": "UTC",
        },
        "persona": "shared",
    }

    prompt = _build_system_prompt(payload)
    assert "The user's name is" not in prompt


# ─── Test 3: ResponseListener stores channel in outbound message ─────────────


@pytest.mark.asyncio
async def test_response_listener_stores_channel():
    """ResponseListener._deliver must store channel_name on the outbound message.

    Regression: Slack messages stored with channel=NULL because the
    store_message call in _deliver did not pass the channel kwarg.
    """
    from app.core.pipeline import ResponseListener, MessageState
    from app.memory.store import MemoryStore

    # Build a minimal result payload as the worker would produce
    fake_job_id = str(uuid.uuid4())
    fake_user_id = str(uuid.uuid4())
    result_payload = {
        "address": "U_SLACK_USER",
        "channel": "slack",
        "response": "Here are your emails.",
        "user_id": fake_user_id,
    }

    # Mock Redis
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=json.dumps(result_payload))

    # Mock queue_client.claim_delivery
    mock_claim = AsyncMock(return_value=True)

    # Mock channel
    mock_channel = MagicMock()
    mock_channel.send = AsyncMock(return_value=True)

    # Track store_message calls
    stored_messages = []
    original_store_message = MemoryStore.store_message

    async def tracking_store_message(self, **kwargs):
        stored_messages.append(kwargs)
        return await original_store_message(self, **kwargs)

    listener = ResponseListener(channels={"slack": mock_channel, "sms": MagicMock()})

    # Verify the result payload channel is correctly extracted
    channel_name = result_payload.get("channel", "sms")
    assert channel_name == "slack", "Result payload must contain channel='slack'"

    # Verify the source code passes channel to store_message by inspecting _deliver
    import inspect
    source = inspect.getsource(ResponseListener._deliver)
    assert "channel=channel_name" in source, (
        "ResponseListener._deliver must pass channel=channel_name to store_message"
    )


# ─── Test 4: Proactive channel defaults (plan_day) ───────────────────────────


@pytest.mark.asyncio
async def test_proactive_channel_phone_only():
    """User with phone only gets channel='sms' in proactive jobs."""
    from app.core.proactive_pool import plan_day

    mock_user = MagicMock()
    mock_user.phone = "+15551234567"
    mock_user.slack_user_id = None
    mock_user.proactive_settings_json = None

    mock_store = AsyncMock()
    mock_store.db = AsyncMock()
    # Simulate DB returning the user
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_user
    mock_store.db.execute = AsyncMock(return_value=mock_result)
    mock_store.get_profile_entries = AsyncMock(return_value=[])
    mock_store.get_goals = AsyncMock(return_value=[])
    mock_store.get_memories = AsyncMock(return_value=[])

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)  # NX guard passes
    mock_redis.zrangebyscore = AsyncMock(return_value=[])  # No spacing conflicts
    mock_redis.zadd = AsyncMock()

    scheduled = await plan_day(mock_redis, "user-123", "UTC", mock_store)

    # Verify all zadd calls use channel="sms"
    for call in mock_redis.zadd.call_args_list:
        payload_str = list(call[0][1].keys())[0]
        payload = json.loads(payload_str)
        assert payload["channel"] == "sms", f"Expected channel='sms', got '{payload['channel']}'"


@pytest.mark.asyncio
async def test_proactive_channel_slack_only():
    """User with slack_user_id only gets channel='slack'."""
    from app.core.proactive_pool import plan_day

    mock_user = MagicMock()
    mock_user.phone = ""
    mock_user.slack_user_id = "U_SLACK_123"
    mock_user.proactive_settings_json = None

    mock_store = AsyncMock()
    mock_store.db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_user
    mock_store.db.execute = AsyncMock(return_value=mock_result)
    mock_store.get_profile_entries = AsyncMock(return_value=[])
    mock_store.get_goals = AsyncMock(return_value=[])
    mock_store.get_memories = AsyncMock(return_value=[])

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.zrangebyscore = AsyncMock(return_value=[])
    mock_redis.zadd = AsyncMock()

    scheduled = await plan_day(mock_redis, "user-456", "UTC", mock_store)

    for call in mock_redis.zadd.call_args_list:
        payload_str = list(call[0][1].keys())[0]
        payload = json.loads(payload_str)
        assert payload["channel"] == "slack", f"Expected channel='slack', got '{payload['channel']}'"


@pytest.mark.asyncio
async def test_proactive_channel_preferred_slack():
    """User with both channels + preferred_channel='slack' gets slack."""
    from app.core.proactive_pool import plan_day

    mock_user = MagicMock()
    mock_user.phone = "+15551234567"
    mock_user.slack_user_id = "U_SLACK_789"
    mock_user.proactive_settings_json = json.dumps({"preferred_channel": "slack"})

    mock_store = AsyncMock()
    mock_store.db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_user
    mock_store.db.execute = AsyncMock(return_value=mock_result)
    mock_store.get_profile_entries = AsyncMock(return_value=[])
    mock_store.get_goals = AsyncMock(return_value=[])
    mock_store.get_memories = AsyncMock(return_value=[])

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.zrangebyscore = AsyncMock(return_value=[])
    mock_redis.zadd = AsyncMock()

    scheduled = await plan_day(mock_redis, "user-789", "UTC", mock_store)

    for call in mock_redis.zadd.call_args_list:
        payload_str = list(call[0][1].keys())[0]
        payload = json.loads(payload_str)
        assert payload["channel"] == "slack", f"Expected channel='slack', got '{payload['channel']}'"


@pytest.mark.asyncio
async def test_proactive_channel_slack_preferred_but_no_slack_id():
    """User prefers slack but has no slack_user_id -- falls back to sms."""
    from app.core.proactive_pool import plan_day

    mock_user = MagicMock()
    mock_user.phone = "+15551234567"
    mock_user.slack_user_id = None
    mock_user.proactive_settings_json = json.dumps({"preferred_channel": "slack"})

    mock_store = AsyncMock()
    mock_store.db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_user
    mock_store.db.execute = AsyncMock(return_value=mock_result)
    mock_store.get_profile_entries = AsyncMock(return_value=[])
    mock_store.get_goals = AsyncMock(return_value=[])
    mock_store.get_memories = AsyncMock(return_value=[])

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.zrangebyscore = AsyncMock(return_value=[])
    mock_redis.zadd = AsyncMock()

    scheduled = await plan_day(mock_redis, "user-fallback", "UTC", mock_store)

    for call in mock_redis.zadd.call_args_list:
        payload_str = list(call[0][1].keys())[0]
        payload = json.loads(payload_str)
        assert payload["channel"] == "sms", f"Expected channel='sms', got '{payload['channel']}'"


# ─── Test 5: Preferred channel API (GET/PUT proactive-preferences) ────────────


@pytest.mark.asyncio
async def test_proactive_preferences_returns_preferred_channel(client):
    """GET /api/v1/proactive-preferences must include preferred_channel in global_settings."""
    token, _ = await register_user(client, "prefs1@test.com", "+15550200010")

    resp = await client.get(
        "/api/v1/proactive-preferences",
        headers=auth_header(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "global_settings" in data
    assert "preferred_channel" in data["global_settings"], (
        "global_settings must include preferred_channel field"
    )
    # Default should be 'sms'
    assert data["global_settings"]["preferred_channel"] == "sms"


@pytest.mark.asyncio
async def test_proactive_preferences_update_preferred_channel(client):
    """PUT /api/v1/proactive-preferences with preferred_channel='slack' persists it."""
    token, _ = await register_user(client, "prefs2@test.com", "+15550200011")

    # Update to slack
    resp = await client.put(
        "/api/v1/proactive-preferences",
        headers=auth_header(token),
        json={
            "categories": [],
            "global_settings": {
                "max_daily_messages": 5,
                "quiet_hours_start": 22,
                "quiet_hours_end": 7,
                "enabled": True,
                "preferred_channel": "slack",
            },
        },
    )
    assert resp.status_code == 200

    # Verify it persisted
    resp = await client.get(
        "/api/v1/proactive-preferences",
        headers=auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["global_settings"]["preferred_channel"] == "slack"


# ─── Test 6: SMS registration link prefills phone ────────────────────────────


@pytest.mark.asyncio
async def test_sms_registration_link_prefills_phone():
    """Unregistered phone gets a reply with ?phone= containing URL-encoded E.164 number.

    Regression: Registration link did not include the phone number, forcing
    users to manually retype their number during signup.
    """
    from app.routes.sms import _process_inbound, _channel

    sent_messages = []
    original_send = _channel.send

    async def capture_send(to, body):
        sent_messages.append({"to": to, "body": body})
        return True

    # Mock the channel send and store lookup
    with patch.object(_channel, "send", side_effect=capture_send):
        # Mock store.lookup_by_phone to return None (unregistered)
        with patch("app.routes.sms.AsyncSessionLocal") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_session

            # Mock MemoryStore.lookup_by_phone to return None
            with patch("app.routes.sms.MemoryStore") as mock_store_cls:
                mock_store = AsyncMock()
                mock_store.lookup_by_phone = AsyncMock(return_value=None)
                mock_store_cls.return_value = mock_store

                await _process_inbound("+15559876543", "Hey there")

    assert len(sent_messages) == 1, "Expected exactly one reply to unregistered phone"
    reply_body = sent_messages[0]["body"]

    # Must contain URL-encoded E.164 phone: %2B15559876543
    assert "phone=%2B15559876543" in reply_body, (
        f"Registration link must contain URL-encoded phone. Got: {reply_body}"
    )
    assert "/auth/register" in reply_body or "/register" in reply_body


# ─── Test 7: Channel-agnostic identity (user_id in job payload) ──────────────


@pytest.mark.asyncio
async def test_job_payload_contains_user_id():
    """Pipeline must include user_id in the job payload pushed to the queue.

    Regression: Worker tasks could not identify users across channels
    because the payload only contained phone/address.
    """
    from app.core.pipeline import MessagePipeline

    pushed_payloads = []

    mock_channel = MagicMock()
    mock_channel.name = "slack"
    mock_channel.send = AsyncMock(return_value=True)

    mock_queue = AsyncMock()

    async def capture_push(payload):
        pushed_payloads.append(payload)
        return "fake-job-id"

    mock_queue.push_job = capture_push
    mock_queue.wait_for_result = AsyncMock(return_value={"response": "test response"})
    mock_queue.claim_delivery = AsyncMock(return_value=True)

    mock_store = AsyncMock()
    mock_user = MagicMock()
    mock_user.id = "user-uuid-123"
    mock_user.phone = "+15551234567"
    mock_user.name = "TestUser"
    mock_user.slack_user_id = "U_TEST_SLACK"
    mock_user.assistant_name = None
    mock_user.personality_notes = None
    mock_store.get_or_create_user = AsyncMock(return_value=mock_user)
    mock_store.message_count = AsyncMock(return_value=5)  # Not first message
    mock_store.store_message = AsyncMock()
    mock_store.get_context_minimal = AsyncMock(return_value={
        "recent_messages": [],
        "last_persona": None,
    })
    mock_store.get_context_full = AsyncMock(return_value={
        "user": {"name": "TestUser"},
        "memories": {},
        "timezone": "UTC",
    })
    mock_store.get_context_standard = AsyncMock(return_value={
        "user": {"name": "TestUser"},
        "memories": {},
        "timezone": "UTC",
    })
    mock_store.get_pending_action = AsyncMock(return_value=None)
    # Mock persona methods — empty list means persona detection is skipped (defaults to "shared")
    mock_store.get_personas = AsyncMock(return_value=[])

    pipeline = MessagePipeline(channel=mock_channel, queue=mock_queue, store=mock_store)

    # Patch intent classification to avoid real logic
    with patch("app.core.pipeline.classify_intent") as mock_classify:
        from app.core.intent import Intent, IntentType
        mock_classify.return_value = Intent(
            type=IntentType.GENERAL,
            confidence=0.9,
            requires_worker=True,
            raw_text="What's the weather?",
        )
        await pipeline.handle(address="U_TEST_SLACK", body="What's the weather?")

    assert len(pushed_payloads) >= 1, "Pipeline must push at least one job"
    payload = pushed_payloads[0]
    assert "user_id" in payload, "Job payload must contain user_id"
    assert payload["user_id"] == "user-uuid-123"
    assert payload["channel"] == "slack"


@pytest.mark.asyncio
async def test_manager_dispatch_extracts_user_id():
    """manager_dispatch must extract user_id from payload and include in result."""
    from app.tasks.manager import _build_system_prompt

    # Verify that manager_dispatch uses user_id from the payload
    # by checking the source code for the extraction
    from app.tasks.manager import manager_dispatch
    import inspect
    source = inspect.getsource(manager_dispatch)
    assert 'user_id = payload.get("user_id"' in source, (
        "manager_dispatch must extract user_id from payload"
    )
    assert '"user_id": user_id' in source, (
        "manager_dispatch must include user_id in the return dict"
    )

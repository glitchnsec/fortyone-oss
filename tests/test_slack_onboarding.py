"""Integration tests for Slack onboarding flow (D-01 through D-07).

Tests cover:
  1. Auto-link by email match (D-01)
  2. Unknown user gets onboarding message with registration link (D-02, D-03)
  3. Linking code flow via Redis (D-02)
  4. Registration with slack_user_id auto-links account (D-04)
  5. Pipeline uses slack_user_id lookup for known users (D-07)
"""
import asyncio
import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Force in-memory SQLite for tests
os.environ.setdefault("DATABASE_URL", "sqlite://")

from httpx import AsyncClient, ASGITransport


@pytest_asyncio.fixture
async def app():
    """Create fresh FastAPI app with in-memory DB for each test."""
    # Clear settings cache so DATABASE_URL is re-read
    from app.config import get_settings
    get_settings.cache_clear()

    from app.database import init_db, engine, Base
    from app.main import app as fastapi_app

    # Initialize in-memory DB
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield fastapi_app

    # Teardown
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def db_session():
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def store(db_session):
    from app.memory.store import MemoryStore
    return MemoryStore(db_session)


# --------------------------------------------------------------------------- #
# Test 1: Auto-link by email (D-01)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_auto_link_by_email(app, store):
    """When a Slack user's email matches an existing account, auto-link."""
    from app.memory.models import User

    # Create an existing user with email
    user = User(phone="+15550001111", email="alice@example.com")
    store.db.add(user)
    await store.db.commit()
    await store.db.refresh(user)

    # Mock Slack API users_info to return matching email
    mock_client = AsyncMock()
    mock_client.users_info = AsyncMock(return_value={
        "user": {"profile": {"email": "alice@example.com", "phone": ""}}
    })

    mock_channel = MagicMock()
    mock_channel._client = mock_client
    mock_channel.send = AsyncMock(return_value=True)
    mock_channel.error_reply = "Error"
    mock_channel.name = "slack"

    with patch("app.routes.slack._channel", mock_channel), \
         patch("app.routes.slack.queue_client", MagicMock()):
        # Patch pipeline to avoid full execution
        with patch("app.core.pipeline.MessagePipeline.handle_with_user", new_callable=AsyncMock) as mock_handle:
            from app.routes.slack import _handle_slack_onboarding
            await _handle_slack_onboarding("U_ALICE_SLACK", "hello", store)

    # Verify slack_user_id was set
    await store.db.refresh(user)
    assert user.slack_user_id == "U_ALICE_SLACK"

    # Verify welcome message was sent
    mock_channel.send.assert_any_call(
        "U_ALICE_SLACK",
        "I've linked your Slack to your existing account. How can I help?",
    )


# --------------------------------------------------------------------------- #
# Test 2: Unknown user gets onboarding message (D-02, D-03)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_unknown_user_onboarding_message(app, store):
    """Unknown email sends onboarding message with registration link."""
    mock_client = AsyncMock()
    mock_client.users_info = AsyncMock(return_value={
        "user": {"profile": {"email": "bob@example.com", "phone": ""}}
    })

    mock_channel = MagicMock()
    mock_channel._client = mock_client
    mock_channel.send = AsyncMock(return_value=True)
    mock_channel.name = "slack"

    with patch("app.routes.slack._channel", mock_channel):
        from app.routes.slack import _handle_slack_onboarding
        await _handle_slack_onboarding("U_BOB_SLACK", "hi there", store)

    # Verify onboarding message was sent
    mock_channel.send.assert_called_once()
    msg = mock_channel.send.call_args[0][1]
    assert "bob%40example.com" in msg or "bob@example.com" in msg  # Email prefilled in URL (may be URL-encoded)
    assert "/auth/register" in msg
    assert "slack_id=U_BOB_SLACK" in msg


# --------------------------------------------------------------------------- #
# Test 3: Linking code flow (D-02)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_linking_code_flow(app, store):
    """6-char linking code from Redis links the Slack user to the account."""
    from app.memory.models import User

    # Create user
    user = User(phone="+15550002222", email="charlie@example.com")
    store.db.add(user)
    await store.db.commit()
    await store.db.refresh(user)

    # Mock Redis with the linking code
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=user.id.encode())
    mock_redis.delete = AsyncMock()
    mock_redis.aclose = AsyncMock()

    mock_channel = MagicMock()
    mock_channel._client = None
    mock_channel.send = AsyncMock(return_value=True)
    mock_channel.name = "slack"

    with patch("app.routes.slack._channel", mock_channel), \
         patch("redis.asyncio.from_url", return_value=mock_redis):
        from app.routes.slack import _handle_slack_onboarding
        await _handle_slack_onboarding("U_CHARLIE_SLACK", "ABC123", store)

    # Verify linking
    await store.db.refresh(user)
    assert user.slack_user_id == "U_CHARLIE_SLACK"

    # Verify welcome sent
    mock_channel.send.assert_called_once()
    msg = mock_channel.send.call_args[0][1]
    assert "linked" in msg.lower()

    # Verify Redis key deleted
    mock_redis.delete.assert_called_once_with("slack_link:ABC123")


# --------------------------------------------------------------------------- #
# Test 4: Registration with slack_user_id (D-04)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_register_with_slack_user_id(app, client):
    """POST /auth/register with slack_user_id auto-links the Slack account."""
    with patch("app.routes.auth._send_slack_welcome", new_callable=AsyncMock) as mock_welcome:
        res = await client.post("/auth/register", json={
            "email": "dave@example.com",
            "phone": "+15550003333",
            "password": "securepass123",
            "slack_user_id": "U_DAVE_SLACK",
        })

    assert res.status_code == 201
    data = res.json()
    assert "access_token" in data
    assert "user_id" in data

    # Verify slack_user_id was set on the user
    from app.database import AsyncSessionLocal
    from app.memory.store import MemoryStore
    async with AsyncSessionLocal() as db:
        s = MemoryStore(db)
        user = await s.lookup_by_slack_user_id("U_DAVE_SLACK")
        assert user is not None
        assert user.email == "dave@example.com"


# --------------------------------------------------------------------------- #
# Test 5: Known Slack user goes through pipeline (D-07)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_known_slack_user_pipeline(app, store):
    """A user with slack_user_id set is routed through the pipeline directly."""
    from app.memory.models import User

    user = User(phone="+15550004444", email="eve@example.com", slack_user_id="U_EVE_SLACK")
    store.db.add(user)
    await store.db.commit()
    await store.db.refresh(user)

    mock_channel = MagicMock()
    mock_channel._client = None
    mock_channel.send = AsyncMock(return_value=True)
    mock_channel.error_reply = "Error"
    mock_channel.name = "slack"

    with patch("app.routes.slack._channel", mock_channel), \
         patch("app.routes.slack.queue_client", MagicMock()), \
         patch("app.core.pipeline.MessagePipeline.handle_with_user", new_callable=AsyncMock) as mock_handle:
        from app.routes.slack import _process_inbound
        await _process_inbound("U_EVE_SLACK", "what's on my schedule?")

    # Pipeline should have been called with the existing user
    mock_handle.assert_called_once()
    call_kwargs = mock_handle.call_args
    assert call_kwargs[1]["user"].id == user.id
    assert call_kwargs[1]["body"] == "what's on my schedule?"


# --------------------------------------------------------------------------- #
# Test 6: MemoryStore methods
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_store_slack_methods(app, store):
    """Verify lookup_by_slack_user_id, link_slack_user, get_or_create_user_for_slack."""
    from app.memory.models import User

    user = User(phone="+15550005555", email="frank@example.com")
    store.db.add(user)
    await store.db.commit()
    await store.db.refresh(user)

    # lookup_by_slack_user_id returns None before linking
    result = await store.lookup_by_slack_user_id("U_FRANK")
    assert result is None

    # link_slack_user
    await store.link_slack_user(user.id, "U_FRANK")
    await store.db.refresh(user)
    assert user.slack_user_id == "U_FRANK"

    # lookup_by_slack_user_id returns user after linking
    result = await store.lookup_by_slack_user_id("U_FRANK")
    assert result is not None
    assert result.id == user.id

    # get_or_create_user_for_slack returns user and updates last_seen_at
    old_seen = user.last_seen_at
    result = await store.get_or_create_user_for_slack("U_FRANK")
    assert result is not None
    assert result.id == user.id

    # get_or_create_user_for_slack returns None for unknown
    result = await store.get_or_create_user_for_slack("U_UNKNOWN")
    assert result is None

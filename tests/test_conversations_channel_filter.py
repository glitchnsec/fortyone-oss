"""Integration tests for GET /api/v1/conversations — channel filter.

Verifies:
  - Without channel param: returns all messages (sms + slack)
  - ?channel=sms: returns only SMS messages
  - ?channel=slack: returns only Slack messages
  - Every returned message includes a `channel` field
"""
import os
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base

_DB_PATH = "./test_conversations_channel_filter.db"
_test_engine = create_async_engine(f"sqlite+aiosqlite:///{_DB_PATH}", echo=False)
_TestSession = async_sessionmaker(_test_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    from app.memory import models  # noqa: F401
    from app.models import auth    # noqa: F401
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    try:
        os.remove(_DB_PATH)
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def client():
    from app.main import app
    from app.routes.auth import _get_db as auth_get_db
    from app.routes.dashboard import _get_db as dash_get_db
    from app.middleware.auth import _get_db as mw_get_db

    async def _override_db():
        async with _TestSession() as session:
            yield session

    app.dependency_overrides[auth_get_db] = _override_db
    app.dependency_overrides[dash_get_db] = _override_db
    app.dependency_overrides[mw_get_db]   = _override_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _register_and_login(client: AsyncClient) -> str:
    """Register a user and return Bearer access_token."""
    resp = await client.post("/auth/register", json={
        "email": "channel@example.com",
        "phone": "+15550009999",
        "password": "TestPass123!",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


async def _seed_messages(token: str, client: AsyncClient):
    """Seed messages via direct DB insert (bypass pipeline).

    Creates 2 SMS messages and 1 Slack message for the authenticated user.
    """
    from app.memory.models import Message, User
    # Get user id from /api/v1/me
    me_resp = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    user_id = me_resp.json()["user_id"]

    async with _TestSession() as session:
        import uuid
        from datetime import datetime, timezone
        for i, (ch, body) in enumerate([
            ("sms", "Hello via SMS 1"),
            ("sms", "Hello via SMS 2"),
            ("slack", "Hello via Slack"),
        ]):
            msg = Message(
                id=str(uuid.uuid4()),
                user_id=user_id,
                direction="inbound",
                body=body,
                intent=None,
                channel=ch,
                created_at=datetime(2026, 1, 1, 12, i, 0, tzinfo=timezone.utc),
            )
            session.add(msg)
        await session.commit()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_no_filter_returns_all(client):
    token = await _register_and_login(client)
    await _seed_messages(token, client)

    resp = await client.get("/api/v1/conversations", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["conversations"]) == 3


@pytest.mark.asyncio
async def test_filter_by_channel_sms(client):
    token = await _register_and_login(client)
    await _seed_messages(token, client)

    resp = await client.get("/api/v1/conversations?channel=sms", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert all(m["channel"] == "sms" for m in data["conversations"])


@pytest.mark.asyncio
async def test_filter_by_channel_slack(client):
    token = await _register_and_login(client)
    await _seed_messages(token, client)

    resp = await client.get("/api/v1/conversations?channel=slack", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["conversations"][0]["channel"] == "slack"
    assert "Slack" in data["conversations"][0]["body"]


@pytest.mark.asyncio
async def test_every_message_has_channel_field(client):
    token = await _register_and_login(client)
    await _seed_messages(token, client)

    resp = await client.get("/api/v1/conversations", headers=_auth(token))
    assert resp.status_code == 200
    for msg in resp.json()["conversations"]:
        assert "channel" in msg
        assert msg["channel"] in ("sms", "slack")

"""Integration tests for proactive preferences API endpoints.

Tests GET/PUT /api/v1/proactive-preferences for category overrides and global settings.
"""
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
from app.memory.models import Role, User


_test_engine = create_async_engine("sqlite+aiosqlite:///./test_proactive_prefs.db", echo=False)
_TestSession = async_sessionmaker(_test_engine, expire_on_commit=False)


async def _seed_roles(session):
    """Seed the roles table with 'user' and 'admin' rows."""
    result = await session.execute(select(Role).where(Role.name == "user"))
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
    import os
    try:
        os.remove("./test_proactive_prefs.db")
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
    from app.routes.dashboard import _get_db as dash_get_db

    app.dependency_overrides[auth_get_db] = _override_db
    app.dependency_overrides[mw_get_db] = _override_db
    app.dependency_overrides[dash_get_db] = _override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


# ─── Helpers ─────────────────────────────────────────────────────────────


async def register_user(client, email="prefs@test.com", phone="+15559990001"):
    """Register a user and return (access_token, user_id)."""
    resp = await client.post("/auth/register", json={
        "email": email,
        "phone": phone,
        "password": "TestPass123!",
    })
    assert resp.status_code == 201, f"Register failed: {resp.text}"
    data = resp.json()
    return data["access_token"], data["user_id"]


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ─── Tests ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_all_categories_with_defaults(client):
    """GET /proactive-preferences returns all 9 categories with default values."""
    token, _ = await register_user(client)
    resp = await client.get("/api/v1/proactive-preferences", headers=auth_header(token))
    assert resp.status_code == 200
    data = resp.json()

    assert "categories" in data
    assert "global_settings" in data
    assert len(data["categories"]) == 9  # Phase 4.3: 9 categories (added afternoon_followup + feature_discovery)

    # No overrides for any category
    for cat in data["categories"]:
        assert cat["has_override"] is False
        assert cat["description"] != ""
        assert cat["window_start_hour"] == cat["default_window_start"]
        assert cat["window_end_hour"] == cat["default_window_end"]

    # Most categories default to disabled; only profile_nudge and feature_discovery default to enabled
    enabled_by_default = {c["name"] for c in data["categories"] if c["enabled"] is True}
    assert "profile_nudge" in enabled_by_default
    assert "feature_discovery" in enabled_by_default

    # Global defaults (Phase 4.3: max_daily reduced from 5 to 3 for noise reduction)
    gs = data["global_settings"]
    assert gs["max_daily_messages"] == 3
    assert gs["quiet_hours_start"] == 22
    assert gs["quiet_hours_end"] == 7
    assert gs["enabled"] is True


@pytest.mark.asyncio
async def test_put_saves_overrides_and_get_reflects_them(client):
    """PUT saves category overrides; subsequent GET reflects changes."""
    token, _ = await register_user(client)

    # Save overrides
    put_resp = await client.put(
        "/api/v1/proactive-preferences",
        headers={**auth_header(token), "Content-Type": "application/json"},
        json={
            "categories": [
                {"name": "morning_briefing", "enabled": False, "window_start_hour": 8.0, "window_end_hour": 10.0},
                {"name": "evening_recap", "enabled": True, "window_start_hour": 18.0, "window_end_hour": 20.0},
            ],
            "global_settings": {
                "max_daily_messages": 3,
                "quiet_hours_start": 23,
                "quiet_hours_end": 6,
                "enabled": True,
            },
        },
    )
    assert put_resp.status_code == 200

    # Fetch and verify
    get_resp = await client.get("/api/v1/proactive-preferences", headers=auth_header(token))
    data = get_resp.json()

    # Find morning_briefing
    mb = next(c for c in data["categories"] if c["name"] == "morning_briefing")
    assert mb["enabled"] is False
    assert mb["has_override"] is True
    assert mb["window_start_hour"] == 8.0
    assert mb["window_end_hour"] == 10.0

    # Find evening_recap
    er = next(c for c in data["categories"] if c["name"] == "evening_recap")
    assert er["enabled"] is True
    assert er["has_override"] is True
    assert er["window_start_hour"] == 18.0
    assert er["window_end_hour"] == 20.0

    # Global settings updated
    gs = data["global_settings"]
    assert gs["max_daily_messages"] == 3
    assert gs["quiet_hours_start"] == 23
    assert gs["quiet_hours_end"] == 6


@pytest.mark.asyncio
async def test_put_default_values_resets_override_windows(client):
    """PUT with values matching defaults stores null (reset to default)."""
    token, _ = await register_user(client)

    # First set a custom value
    await client.put(
        "/api/v1/proactive-preferences",
        headers={**auth_header(token), "Content-Type": "application/json"},
        json={
            "categories": [
                {"name": "morning_briefing", "enabled": True, "window_start_hour": 8.0, "window_end_hour": 10.0},
            ],
            "global_settings": {"max_daily_messages": 5, "quiet_hours_start": 22, "quiet_hours_end": 7, "enabled": True},
        },
    )

    # Now reset to defaults (7.5 and 9.0 are the defaults for morning_briefing)
    await client.put(
        "/api/v1/proactive-preferences",
        headers={**auth_header(token), "Content-Type": "application/json"},
        json={
            "categories": [
                {"name": "morning_briefing", "enabled": True, "window_start_hour": 7.5, "window_end_hour": 9.0},
            ],
            "global_settings": {"max_daily_messages": 5, "quiet_hours_start": 22, "quiet_hours_end": 7, "enabled": True},
        },
    )

    # GET should show default values, still has_override since row exists but windows are null
    get_resp = await client.get("/api/v1/proactive-preferences", headers=auth_header(token))
    data = get_resp.json()
    mb = next(c for c in data["categories"] if c["name"] == "morning_briefing")
    assert mb["window_start_hour"] == 7.5  # shows default since stored as null
    assert mb["window_end_hour"] == 9.0


@pytest.mark.asyncio
async def test_auth_required(client):
    """Endpoints return 401 without a valid token."""
    resp = await client.get("/api/v1/proactive-preferences")
    assert resp.status_code in (401, 403)

    resp = await client.put("/api/v1/proactive-preferences", json={
        "categories": [],
        "global_settings": {"max_daily_messages": 5, "quiet_hours_start": 22, "quiet_hours_end": 7, "enabled": True},
    })
    assert resp.status_code in (401, 403)

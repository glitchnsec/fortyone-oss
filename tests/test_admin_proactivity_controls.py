"""Integration tests for admin proactivity settings endpoints.

Tests:
  - GET /api/v1/admin/proactivity/settings returns platform defaults
  - PUT updates values and GET reflects changes
  - PUT with out-of-range values returns 400
  - Non-admin gets 403
  - check_rate_limit respects custom max_per_day
"""
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
from app.memory.models import Role, User


_test_engine = create_async_engine("sqlite+aiosqlite:///./test_proactivity.db", echo=False)
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
    # Reset Settings singleton to defaults between tests
    from app.config import get_settings
    s = get_settings()
    s.proactive_max_daily_messages = 3
    s.proactive_max_per_hour = 10
    s.proactive_max_categories_per_day = 3
    s.proactive_quiet_hours_start = 22
    s.proactive_quiet_hours_end = 7
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    import os
    try:
        os.remove("./test_proactivity.db")
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
    from app.routes.admin import _get_db as admin_get_db

    app.dependency_overrides[auth_get_db] = _override_db
    app.dependency_overrides[mw_get_db] = _override_db
    app.dependency_overrides[admin_get_db] = _override_db

    try:
        from app.routes.dashboard import _get_db as dash_get_db
        app.dependency_overrides[dash_get_db] = _override_db
    except ImportError:
        pass

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


# ─── Helpers ───────────────────────────────────────────────────────────────


async def register_user(client, email, phone, password="TestPass123!"):
    resp = await client.post("/auth/register", json={
        "email": email, "phone": phone, "password": password,
    })
    assert resp.status_code == 201, f"Register failed: {resp.text}"
    data = resp.json()
    return data["access_token"], data["user_id"]


async def make_admin_and_login(client, email, user_id, password="TestPass123!"):
    async with _TestSession() as db:
        result = await db.execute(select(Role).where(Role.name == "admin"))
        admin_role = result.scalar_one()
        await db.execute(
            update(User).where(User.id == user_id).values(role_id=admin_role.id)
        )
        await db.commit()
    resp = await client.post("/auth/login", json={
        "email": email, "password": password,
    })
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    return resp.json()["access_token"]


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ─── Tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_proactivity_settings_defaults(client):
    """GET /api/v1/admin/proactivity/settings returns platform defaults."""
    token, uid = await register_user(client, "admin@test.com", "+15550000100")
    admin_token = await make_admin_and_login(client, "admin@test.com", uid)

    resp = await client.get(
        "/api/v1/admin/proactivity/settings",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["max_daily_messages"] == 3
    assert data["max_per_hour"] == 10
    assert data["max_categories_per_day"] == 3
    assert data["quiet_hours_start"] == 22
    assert data["quiet_hours_end"] == 7


@pytest.mark.asyncio
async def test_put_updates_settings(client):
    """PUT updates values and subsequent GET reflects changes."""
    token, uid = await register_user(client, "admin2@test.com", "+15550000101")
    admin_token = await make_admin_and_login(client, "admin2@test.com", uid)

    resp = await client.put(
        "/api/v1/admin/proactivity/settings",
        headers=auth_header(admin_token),
        json={"max_daily_messages": 10, "max_categories_per_day": 5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["max_daily_messages"] == 10
    assert data["max_categories_per_day"] == 5
    # Unchanged fields keep defaults
    assert data["max_per_hour"] == 10

    # Verify GET also returns updated values
    resp2 = await client.get(
        "/api/v1/admin/proactivity/settings",
        headers=auth_header(admin_token),
    )
    assert resp2.json()["max_daily_messages"] == 10
    assert resp2.json()["max_categories_per_day"] == 5


@pytest.mark.asyncio
async def test_put_validation_rejects_out_of_range(client):
    """PUT with out-of-range values returns 400."""
    token, uid = await register_user(client, "admin3@test.com", "+15550000102")
    admin_token = await make_admin_and_login(client, "admin3@test.com", uid)

    # max_daily_messages too high
    resp = await client.put(
        "/api/v1/admin/proactivity/settings",
        headers=auth_header(admin_token),
        json={"max_daily_messages": 100},
    )
    assert resp.status_code == 400

    # max_daily_messages too low
    resp = await client.put(
        "/api/v1/admin/proactivity/settings",
        headers=auth_header(admin_token),
        json={"max_daily_messages": 0},
    )
    assert resp.status_code == 400

    # max_categories_per_day too high
    resp = await client.put(
        "/api/v1/admin/proactivity/settings",
        headers=auth_header(admin_token),
        json={"max_categories_per_day": 10},
    )
    assert resp.status_code == 400

    # quiet_hours_start out of range
    resp = await client.put(
        "/api/v1/admin/proactivity/settings",
        headers=auth_header(admin_token),
        json={"quiet_hours_start": 25},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_non_admin_rejected(client):
    """Non-admin user gets 403 on proactivity endpoints."""
    token, _ = await register_user(client, "user@test.com", "+15550000103")

    resp = await client.get(
        "/api/v1/admin/proactivity/settings",
        headers=auth_header(token),
    )
    assert resp.status_code == 403

    resp = await client.put(
        "/api/v1/admin/proactivity/settings",
        headers=auth_header(token),
        json={"max_daily_messages": 5},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_check_rate_limit_custom_max():
    """check_rate_limit respects custom max_per_day argument."""
    import fakeredis.aioredis as fakeredis_aio
    from app.core.throttle import check_rate_limit, record_proactive_send

    r = fakeredis_aio.FakeRedis(decode_responses=True)
    user_id = "test-user-rate-limit"

    # Should be allowed with max_per_day=2 and 0 sends
    assert await check_rate_limit(r, user_id, max_per_day=2) is True

    # Record 2 sends
    await record_proactive_send(r, user_id)
    await record_proactive_send(r, user_id)

    # Should be blocked with max_per_day=2
    assert await check_rate_limit(r, user_id, max_per_day=2) is False

    # Should still be allowed with max_per_day=5
    assert await check_rate_limit(r, user_id, max_per_day=5) is True

    await r.aclose()

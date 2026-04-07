"""Integration tests for admin API endpoints.

Tests auth guards (non-admin rejection), user lifecycle (suspend/restore/delete/purge),
analytics response shapes, and system health endpoint.
"""
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
from app.memory.models import Role, User


# File-based SQLite so all connections share the same database
_test_engine = create_async_engine("sqlite+aiosqlite:///./test_admin.db", echo=False)
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
    # Clear analytics cache between tests
    from app.routes.admin import _analytics_cache
    _analytics_cache.clear()
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    import os
    try:
        os.remove("./test_admin.db")
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

    # Also override dashboard _get_db if it exists
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
    """Promote user to admin in DB, then re-login to get a token with admin role claim."""
    async with _TestSession() as db:
        result = await db.execute(select(Role).where(Role.name == "admin"))
        admin_role = result.scalar_one()
        await db.execute(
            update(User).where(User.id == user_id).values(role_id=admin_role.id)
        )
        await db.commit()
    # Re-login to get token with admin role
    resp = await client.post("/auth/login", json={
        "email": email,
        "password": password,
    })
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    return resp.json()["access_token"]


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ─── Tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_admin_rejected(client):
    """Non-admin user GET /api/v1/admin/users returns 403."""
    token, _ = await register_user(client, "user@test.com", "+15550000001")
    resp = await client.get("/api/v1/admin/users", headers=auth_header(token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_analytics_rejected(client):
    """Non-admin user GET /api/v1/admin/analytics/overview returns 403."""
    token, _ = await register_user(client, "user2@test.com", "+15550000002")
    resp = await client.get("/api/v1/admin/analytics/overview", headers=auth_header(token))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_list_users(client):
    """Admin GET /api/v1/admin/users returns 200 with users list and total count."""
    token, uid = await register_user(client, "admin@test.com", "+15550000010")
    admin_token = await make_admin_and_login(client, "admin@test.com", uid)

    resp = await client.get("/api/v1/admin/users", headers=auth_header(admin_token))
    assert resp.status_code == 200
    data = resp.json()
    assert "users" in data
    assert "total" in data
    assert isinstance(data["users"], list)
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_admin_search_users(client):
    """Admin GET /api/v1/admin/users?search=admin returns filtered results."""
    token, uid = await register_user(client, "admin3@test.com", "+15550000011")
    admin_token = await make_admin_and_login(client, "admin3@test.com", uid)

    # Also register a target user
    await register_user(client, "searchable@test.com", "+15550000012")

    resp = await client.get(
        "/api/v1/admin/users?search=searchable",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    emails = [u["email"] for u in data["users"]]
    assert "searchable@test.com" in emails


@pytest.mark.asyncio
async def test_admin_user_detail(client):
    """Admin GET /api/v1/admin/users/{id} returns 200 with user detail including message_count."""
    token, uid = await register_user(client, "admin4@test.com", "+15550000020")
    admin_token = await make_admin_and_login(client, "admin4@test.com", uid)

    # Create a target user
    _, target_id = await register_user(client, "target@test.com", "+15550000021")

    resp = await client.get(
        f"/api/v1/admin/users/{target_id}",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "target@test.com"
    assert "message_count" in data
    assert isinstance(data["message_count"], int)


@pytest.mark.asyncio
async def test_suspend_and_login_blocked(client):
    """Suspend a user, then verify they cannot login (403)."""
    _, admin_uid = await register_user(client, "admin5@test.com", "+15550000030")
    admin_token = await make_admin_and_login(client, "admin5@test.com", admin_uid)

    # Create target user
    _, target_id = await register_user(client, "victim@test.com", "+15550000031", "VictimPass!")

    # Suspend
    resp = await client.post(
        f"/api/v1/admin/users/{target_id}/suspend",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"

    # Target cannot login
    login_resp = await client.post("/auth/login", json={
        "email": "victim@test.com",
        "password": "VictimPass!",
    })
    assert login_resp.status_code == 403


@pytest.mark.asyncio
async def test_restore_user(client):
    """Restore a suspended user and verify they can login again."""
    _, admin_uid = await register_user(client, "admin6@test.com", "+15550000040")
    admin_token = await make_admin_and_login(client, "admin6@test.com", admin_uid)

    _, target_id = await register_user(client, "restore@test.com", "+15550000041", "RestorePass!")

    # Suspend then restore
    await client.post(
        f"/api/v1/admin/users/{target_id}/suspend",
        headers=auth_header(admin_token),
    )
    resp = await client.post(
        f"/api/v1/admin/users/{target_id}/restore",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"

    # Target can login again
    login_resp = await client.post("/auth/login", json={
        "email": "restore@test.com",
        "password": "RestorePass!",
    })
    assert login_resp.status_code == 200


@pytest.mark.asyncio
async def test_soft_delete(client):
    """Admin DELETE /api/v1/admin/users/{id} returns 200 with status 'deleted'."""
    _, admin_uid = await register_user(client, "admin7@test.com", "+15550000050")
    admin_token = await make_admin_and_login(client, "admin7@test.com", admin_uid)

    _, target_id = await register_user(client, "deleteme@test.com", "+15550000051")

    resp = await client.delete(
        f"/api/v1/admin/users/{target_id}",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_purge_requires_soft_delete(client):
    """Purge without soft-delete first returns 409."""
    _, admin_uid = await register_user(client, "admin8@test.com", "+15550000060")
    admin_token = await make_admin_and_login(client, "admin8@test.com", admin_uid)

    _, target_id = await register_user(client, "nodel@test.com", "+15550000061")

    resp = await client.delete(
        f"/api/v1/admin/users/{target_id}/purge",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_purge_after_soft_delete(client):
    """Soft delete then purge returns 200 and user is gone (404 on detail)."""
    _, admin_uid = await register_user(client, "admin9@test.com", "+15550000070")
    admin_token = await make_admin_and_login(client, "admin9@test.com", admin_uid)

    _, target_id = await register_user(client, "purgeme@test.com", "+15550000071")

    # Soft delete first
    await client.delete(
        f"/api/v1/admin/users/{target_id}",
        headers=auth_header(admin_token),
    )

    # Purge
    resp = await client.delete(
        f"/api/v1/admin/users/{target_id}/purge",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "purged"

    # User should be gone
    detail = await client.get(
        f"/api/v1/admin/users/{target_id}",
        headers=auth_header(admin_token),
    )
    assert detail.status_code == 404


@pytest.mark.asyncio
async def test_analytics_overview(client):
    """Admin GET /api/v1/admin/analytics/overview returns expected keys, all integers."""
    _, admin_uid = await register_user(client, "admin10@test.com", "+15550000080")
    admin_token = await make_admin_and_login(client, "admin10@test.com", admin_uid)

    resp = await client.get(
        "/api/v1/admin/analytics/overview?range=30d",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    for key in ("total_users", "active_today", "messages_today", "pending_tasks"):
        assert key in data, f"Missing key: {key}"
        assert isinstance(data[key], int), f"{key} should be int, got {type(data[key])}"


@pytest.mark.asyncio
async def test_analytics_signups(client):
    """Admin GET /api/v1/admin/analytics/signups returns data array."""
    _, admin_uid = await register_user(client, "admin11@test.com", "+15550000090")
    admin_token = await make_admin_and_login(client, "admin11@test.com", admin_uid)

    resp = await client.get(
        "/api/v1/admin/analytics/signups?range=30d",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert isinstance(data["data"], list)


@pytest.mark.asyncio
async def test_health(client):
    """Admin GET /api/v1/admin/health returns redis and database keys."""
    _, admin_uid = await register_user(client, "admin12@test.com", "+15550000100")
    admin_token = await make_admin_and_login(client, "admin12@test.com", admin_uid)

    resp = await client.get(
        "/api/v1/admin/health",
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "redis" in data
    assert "database" in data

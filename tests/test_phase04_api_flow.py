"""
TDD flow-level integration tests for Phase 4 API endpoints.

Written BEFORE execution — these tests define the API contract that
/gsd:execute-phase 4 must satisfy. They will FAIL (RED) until
the phase is implemented, then PASS (GREEN).

Per CLAUDE.md: "Integration tests must exercise the full user flow
(e.g. register -> get token -> call protected endpoint -> verify 200),
not just check file existence."

Tests the full HTTP cycle: register -> login -> CRUD goals/actions/personas/profile.
Verifies status codes, response shapes, and auth enforcement.
"""
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base

_DB_PATH = "./test_phase04_api.db"
_test_engine = create_async_engine(f"sqlite+aiosqlite:///{_DB_PATH}", echo=False)
_TestSession = async_sessionmaker(_test_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Create tables before each test, drop after."""
    from app.memory import models  # noqa: F401
    from app.models import auth  # noqa: F401

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
    """HTTPX async client with DB overrides for auth + dashboard routes."""
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

    # Also override personas route if it has its own _get_db
    try:
        from app.routes.personas import _get_db as personas_get_db
        app.dependency_overrides[personas_get_db] = _override_db
    except ImportError:
        pass

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


async def _register_and_get_token(client: AsyncClient, email="phase4test@example.com") -> str:
    """Register a user and return the access token."""
    resp = await client.post("/auth/register", json={
        "email": email,
        "phone": "+15559994444",
        "password": "SecurePassword123!",
    })
    assert resp.status_code in (200, 201), f"Register failed: {resp.text}"
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ─── F1. POST /api/v1/goals → 201 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_goal_returns_201(client):
    """POST /api/v1/goals creates a goal and returns 201 with id."""
    token = await _register_and_get_token(client)
    resp = await client.post(
        "/api/v1/goals",
        json={
            "title": "Ship v1 by end of April",
            "framework": "smart",
            "description": "Complete all 5 phases and deploy to production",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "id" in data
    assert data["title"] == "Ship v1 by end of April"


# ─── F2. GET /api/v1/goals → 200 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_goals_returns_200(client):
    """GET /api/v1/goals returns 200 with goals list."""
    token = await _register_and_get_token(client)

    # Create a goal first
    await client.post(
        "/api/v1/goals",
        json={"title": "Test goal", "framework": "custom"},
        headers=_auth(token),
    )

    resp = await client.get("/api/v1/goals", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "goals" in data
    assert len(data["goals"]) >= 1
    assert data["goals"][0]["title"] == "Test goal"


# ─── F3. PATCH /api/v1/goals/{id} → 200 ────────────────────────────────────

@pytest.mark.asyncio
async def test_update_goal_returns_200(client):
    """PATCH /api/v1/goals/{id} updates goal and returns 200."""
    token = await _register_and_get_token(client)

    # Create
    create_resp = await client.post(
        "/api/v1/goals",
        json={"title": "Original title", "framework": "okr"},
        headers=_auth(token),
    )
    goal_id = create_resp.json()["id"]

    # Update
    resp = await client.patch(
        f"/api/v1/goals/{goal_id}",
        json={"title": "Updated title", "status": "completed"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Updated title"
    assert data["version"] >= 2, "Version should increment on update"


# ─── F4. DELETE /api/v1/goals/{id} → 204 ───────────────────────────────────

@pytest.mark.asyncio
async def test_delete_goal_returns_204(client):
    """DELETE /api/v1/goals/{id} removes goal and returns 204."""
    token = await _register_and_get_token(client)

    # Create
    create_resp = await client.post(
        "/api/v1/goals",
        json={"title": "To be deleted"},
        headers=_auth(token),
    )
    goal_id = create_resp.json()["id"]

    # Delete
    resp = await client.delete(
        f"/api/v1/goals/{goal_id}",
        headers=_auth(token),
    )
    assert resp.status_code == 204

    # Verify gone
    list_resp = await client.get("/api/v1/goals?status=all", headers=_auth(token))
    goal_ids = [g["id"] for g in list_resp.json()["goals"]]
    assert goal_id not in goal_ids


# ─── F5. GET /api/v1/actions → 200 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_actions_returns_200(client):
    """GET /api/v1/actions returns 200 with actions list (may be empty)."""
    token = await _register_and_get_token(client)
    resp = await client.get("/api/v1/actions", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "actions" in data
    assert isinstance(data["actions"], list)
    assert "page" in data
    assert "limit" in data


# ─── F6. POST /api/v1/personas → 201 ───────────────────────────────────────

@pytest.mark.asyncio
async def test_create_persona_returns_201(client):
    """POST /api/v1/personas creates persona and returns 201."""
    token = await _register_and_get_token(client)
    resp = await client.post(
        "/api/v1/personas",
        json={"name": "work", "description": "Professional context"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "id" in data
    assert data["name"] == "work"


# ─── F7. GET /api/v1/personas → 200 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_personas_returns_200(client):
    """GET /api/v1/personas returns 200 with personas list."""
    token = await _register_and_get_token(client)

    # Create one first
    await client.post(
        "/api/v1/personas",
        json={"name": "personal"},
        headers=_auth(token),
    )

    resp = await client.get("/api/v1/personas", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "personas" in data
    assert len(data["personas"]) >= 1


# ─── F8. POST /api/v1/profile → 201 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_profile_returns_201(client):
    """POST /api/v1/profile creates a TELOS profile entry and returns 201."""
    token = await _register_and_get_token(client)
    resp = await client.post(
        "/api/v1/profile",
        json={
            "section": "preferences",
            "label": "communication_style",
            "content": "Prefers concise, direct messages",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["section"] == "preferences"
    assert data["label"] == "communication_style"


# ─── F9. GET /api/v1/profile → 200 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_profile_returns_200(client):
    """GET /api/v1/profile returns 200 with profile entries."""
    token = await _register_and_get_token(client)

    # Create an entry first
    await client.post(
        "/api/v1/profile",
        json={"section": "goals", "label": "career", "content": "Lead an engineering team"},
        headers=_auth(token),
    )

    resp = await client.get("/api/v1/profile", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data
    assert len(data["entries"]) >= 1
    assert data["entries"][0]["section"] == "goals"


# ─── F10. Unauthenticated → 401 ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unauthenticated_goals_returns_401(client):
    """GET /api/v1/goals without auth token returns 401."""
    resp = await client.get("/api/v1/goals")
    assert resp.status_code in (401, 403), (
        f"Expected 401 or 403 for unauthenticated request, got {resp.status_code}"
    )

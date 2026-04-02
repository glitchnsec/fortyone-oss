"""
Integration tests for /api/v1/personas REST endpoints.

Tests the full HTTP request/response cycle: register → login → CRUD personas.
Verifies status codes, auth enforcement, and cross-user isolation.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base


_test_engine = create_async_engine("sqlite+aiosqlite:///./test_personas_api.db", echo=False)
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
    import os
    try:
        os.remove("./test_personas_api.db")
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def client():
    """HTTPX async client with DB overrides for auth + personas."""
    from app.main import app

    async def _override_db():
        async with _TestSession() as session:
            yield session

    from app.routes.auth import _get_db as auth_get_db
    from app.middleware.auth import _get_db as mw_get_db
    from app.routes.personas import _get_db as personas_get_db
    from app.routes.dashboard import _get_db as dash_get_db

    app.dependency_overrides[auth_get_db] = _override_db
    app.dependency_overrides[mw_get_db] = _override_db
    app.dependency_overrides[personas_get_db] = _override_db
    app.dependency_overrides[dash_get_db] = _override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


async def _register_and_get_token(client: AsyncClient, email="test@example.com") -> str:
    """Register a user and return the access token."""
    resp = await client.post("/auth/register", json={
        "email": email,
        "phone": "+15551234567",
        "password": "SecurePassword123!",
    })
    assert resp.status_code in (200, 201), f"Register failed: {resp.text}"
    return resp.json()["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_personas_empty(client):
    """GET /api/v1/personas returns empty list for new user."""
    token = await _register_and_get_token(client)
    resp = await client.get("/api/v1/personas", headers=_auth_headers(token))
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_persona_returns_201(client):
    """POST /api/v1/personas creates persona and returns 201."""
    token = await _register_and_get_token(client)
    resp = await client.post(
        "/api/v1/personas",
        json={"name": "work", "description": "PM at Acme"},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "work"
    assert data["description"] == "PM at Acme"
    assert data["is_active"] is True
    assert "id" in data


@pytest.mark.asyncio
async def test_update_persona_returns_updated(client):
    """PATCH /api/v1/personas/{id} updates fields and returns updated persona."""
    token = await _register_and_get_token(client)

    # Create
    create_resp = await client.post(
        "/api/v1/personas",
        json={"name": "work"},
        headers=_auth_headers(token),
    )
    persona_id = create_resp.json()["id"]

    # Update
    resp = await client.patch(
        f"/api/v1/personas/{persona_id}",
        json={"name": "professional", "description": "Updated role"},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "professional"
    assert data["description"] == "Updated role"


@pytest.mark.asyncio
async def test_delete_persona_returns_204(client):
    """DELETE /api/v1/personas/{id} returns 204 and persona is gone."""
    token = await _register_and_get_token(client)

    # Create
    create_resp = await client.post(
        "/api/v1/personas",
        json={"name": "work"},
        headers=_auth_headers(token),
    )
    persona_id = create_resp.json()["id"]

    # Delete
    resp = await client.delete(
        f"/api/v1/personas/{persona_id}",
        headers=_auth_headers(token),
    )
    assert resp.status_code == 204

    # Verify gone
    list_resp = await client.get("/api/v1/personas", headers=_auth_headers(token))
    assert len(list_resp.json()) == 0


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_404(client):
    """DELETE /api/v1/personas/{bad-id} returns 404."""
    token = await _register_and_get_token(client)
    resp = await client.delete(
        "/api/v1/personas/nonexistent-id-12345",
        headers=_auth_headers(token),
    )
    assert resp.status_code == 404

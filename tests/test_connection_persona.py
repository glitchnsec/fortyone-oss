"""
Integration tests for connection-persona assignment via the main API proxy.

Tests PATCH /api/v1/connections/{conn_id} with ownership verification.
Connection service tests are in connections/tests/test_connection_persona.py.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from unittest.mock import MagicMock, AsyncMock

from app.database import Base


_main_engine = create_async_engine(
    "sqlite+aiosqlite:///./test_conn_persona_main.db", echo=False,
)
_MainSession = async_sessionmaker(_main_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Create main API tables."""
    from app.memory import models  # noqa: F401
    from app.models import auth  # noqa: F401
    async with _main_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _main_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    import os
    try:
        os.remove("./test_conn_persona_main.db")
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def client():
    """HTTPX async client for the main API with mocked connections service."""
    from app.main import app
    from app.routes.auth import _get_db as auth_get_db
    from app.middleware.auth import _get_db as mw_get_db
    from app.routes.dashboard import _get_db as dash_get_db

    async def _override_db():
        async with _MainSession() as session:
            yield session

    app.dependency_overrides[auth_get_db] = _override_db
    app.dependency_overrides[mw_get_db] = _override_db
    app.dependency_overrides[dash_get_db] = _override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _register_and_login(c):
    """Register a test user and return auth token."""
    await c.post("/auth/register", json={
        "email": "conntest@example.com",
        "password": "TestPass123!",
        "phone": "+15551234567",
    })
    resp = await c.post("/auth/login", json={
        "email": "conntest@example.com",
        "password": "TestPass123!",
    })
    return resp.json()["access_token"]


def _mock_connections_client(owned_ids, patch_response):
    """Build a mock connections client dependency override."""
    mock_response_get = MagicMock()
    mock_response_get.status_code = 200
    mock_response_get.json.return_value = {
        "connections": [{"id": cid, "provider": "google", "status": "connected"} for cid in owned_ids],
    }

    mock_response_patch = MagicMock()
    mock_response_patch.status_code = 200
    mock_response_patch.json.return_value = patch_response
    mock_response_patch.raise_for_status = MagicMock()

    async def override():
        mock_client = AsyncMock()

        async def mock_get(url, **kwargs):
            return mock_response_get

        async def mock_patch(url, **kwargs):
            return mock_response_patch

        mock_client.get = mock_get
        mock_client.patch = mock_patch
        yield mock_client

    return override


@pytest.mark.asyncio
async def test_proxy_patch_forwards_persona_assignment(client):
    """PATCH /api/v1/connections/{id} proxy forwards and returns updated connection."""
    token = await _register_and_login(client)

    from app.routes.dashboard import _connections_client
    from app.main import app

    app.dependency_overrides[_connections_client] = _mock_connections_client(
        owned_ids=["conn-1"],
        patch_response={"id": "conn-1", "provider": "google", "persona_id": "persona-work"},
    )

    resp = await client.patch(
        "/api/v1/connections/conn-1",
        json={"persona_id": "persona-work"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["persona_id"] == "persona-work"

    app.dependency_overrides.pop(_connections_client, None)


@pytest.mark.asyncio
async def test_proxy_patch_rejects_unowned_connection(client):
    """PATCH /api/v1/connections/{id} returns 403 for connections not owned by user."""
    token = await _register_and_login(client)

    from app.routes.dashboard import _connections_client
    from app.main import app

    app.dependency_overrides[_connections_client] = _mock_connections_client(
        owned_ids=["conn-owned"],
        patch_response={},
    )

    resp = await client.patch(
        "/api/v1/connections/conn-not-mine",
        json={"persona_id": "persona-1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403

    app.dependency_overrides.pop(_connections_client, None)


@pytest.mark.asyncio
async def test_proxy_patch_requires_auth(client):
    """PATCH /api/v1/connections/{id} without auth token is rejected."""
    resp = await client.patch(
        "/api/v1/connections/conn-1",
        json={"persona_id": "persona-1"},
    )
    assert resp.status_code in (401, 403)

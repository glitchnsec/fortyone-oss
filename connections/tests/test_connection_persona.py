"""
Integration tests for connection-persona assignment in the connections service.

Tests PATCH /connections/{conn_id} endpoint for persona_id updates and
GET /connections/{user_id} persona_id inclusion.
"""
import uuid
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
from app.models import Connection

_test_engine = create_async_engine(
    "sqlite+aiosqlite:///./test_conn_persona.db", echo=False,
)
_TestSession = async_sessionmaker(_test_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    from app import models  # noqa: F401
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    import os
    try:
        os.remove("./test_conn_persona.db")
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def client():
    from app.main import app
    from app.routes.connections import _get_db as conn_get_db

    async def _override_db():
        async with _TestSession() as session:
            yield session

    app.dependency_overrides[conn_get_db] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _seed_connection(user_id="user-1", provider="google", persona_id=None):
    """Insert a test connection directly via SQLAlchemy."""
    async with _TestSession() as session:
        conn = Connection(
            id=str(uuid.uuid4()),
            user_id=user_id,
            provider=provider,
            status="connected",
            granted_scopes="email calendar",
            persona_id=persona_id,
        )
        session.add(conn)
        await session.commit()
        await session.refresh(conn)
        return conn


@pytest.mark.asyncio
async def test_patch_sets_persona_id(client):
    """PATCH /connections/{id} sets persona_id and returns updated connection."""
    conn = await _seed_connection()
    resp = await client.patch(
        f"/connections/{conn.id}",
        json={"persona_id": "persona-work"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == conn.id
    assert data["persona_id"] == "persona-work"
    assert data["provider"] == "google"


@pytest.mark.asyncio
async def test_patch_sets_shared(client):
    """PATCH with persona_id=null sets connection to shared."""
    conn = await _seed_connection(persona_id="persona-work")
    resp = await client.patch(
        f"/connections/{conn.id}",
        json={"persona_id": None},
    )
    assert resp.status_code == 200
    assert resp.json()["persona_id"] is None


@pytest.mark.asyncio
async def test_patch_nonexistent_returns_404(client):
    """PATCH on a non-existent connection returns 404."""
    resp = await client.patch(
        "/connections/nonexistent-id",
        json={"persona_id": "persona-1"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_includes_persona_id(client):
    """GET /connections/{user_id} includes persona_id in each connection."""
    await _seed_connection(user_id="user-2", persona_id="persona-work")
    resp = await client.get("/connections/user-2")
    assert resp.status_code == 200
    connections = resp.json()["connections"]
    assert len(connections) == 1
    assert connections[0]["persona_id"] == "persona-work"

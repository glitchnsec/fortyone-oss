"""Integration tests for DELETE /api/v1/me — account de-registration.

Verifies: auth gate, 204 response, cascade delete of all user data,
and that the token is invalidated after deletion.
"""
import os
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base

_test_engine = create_async_engine("sqlite+aiosqlite:///./test_deregistration.db", echo=False)
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
        os.remove("./test_deregistration.db")
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


async def _register_and_login(client) -> str:
    """Helper: register a user and return the Bearer access_token."""
    resp = await client.post("/auth/register", json={
        "email": "delete@example.com", "phone": "+15550000001", "password": "TestPass123!"
    })
    assert resp.status_code == 201
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_delete_requires_auth(client):
    # FastAPI HTTPBearer returns 403 for missing credentials (no Authorization header)
    resp = await client.delete("/api/v1/me")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_delete_returns_204(client):
    token = await _register_and_login(client)
    resp = await client.delete("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_cascades_all_data(client):
    from app.memory.models import Memory, Message, Task, User
    from app.models.auth import UserSession

    token = await _register_and_login(client)

    # Seed child rows via the API (use existing endpoints + direct DB for completeness)
    async with _TestSession() as db:
        result = await db.execute(select(User).where(User.email == "delete@example.com"))
        user = result.scalar_one()
        user_id = user.id
        # Add a memory and a task directly
        from app.memory.models import Memory as Mem, Task as T
        db.add(Mem(user_id=user_id, memory_type="long_term", key="name", value="Tester"))
        db.add(T(user_id=user_id, task_type="reminder", title="Test reminder"))
        await db.commit()

    await client.delete("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    async with _TestSession() as db:
        assert (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none() is None
        assert (await db.execute(select(Memory).where(Memory.user_id == user_id))).scalars().all() == []
        assert (await db.execute(select(Task).where(Task.user_id == user_id))).scalars().all() == []
        assert (await db.execute(select(UserSession).where(UserSession.user_id == user_id))).scalars().all() == []


@pytest.mark.asyncio
async def test_token_invalid_after_delete(client):
    token = await _register_and_login(client)
    await client.delete("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    # User is gone — get_current_user should return 401 (user lookup fails)
    resp = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401

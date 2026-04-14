"""Integration tests for the password reset flow — send code, reset, re-login.

Tests the FULL user journey:
  - Request reset code (send-code endpoint)
  - Reset password with code + new password
  - Verify old password no longer works, new password does
  - No user enumeration (unknown phone returns 200)
  - Password length validation
"""
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base


_test_engine = create_async_engine("sqlite+aiosqlite:///./test_password_reset.db", echo=False)
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
        os.remove("./test_password_reset.db")
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


TEST_USER = {
    "email": "resettest@example.com",
    "phone": "+15559876543",
    "password": "OriginalPass123!",
}


@pytest.mark.asyncio
async def test_send_code_returns_200_for_existing_user(client):
    """Send-code returns 200 for a registered user."""
    await client.post("/auth/register", json=TEST_USER)
    resp = await client.post(
        "/auth/forgot-password/send-code",
        json={"phone": TEST_USER["phone"]},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"


@pytest.mark.asyncio
async def test_reset_password_with_valid_code(client):
    """Reset password with a valid 6-digit code (dev mode) succeeds."""
    await client.post("/auth/register", json=TEST_USER)
    # Send code first
    await client.post(
        "/auth/forgot-password/send-code",
        json={"phone": TEST_USER["phone"]},
    )
    # Reset with dev-mode code
    resp = await client.post(
        "/auth/forgot-password/reset",
        json={
            "phone": TEST_USER["phone"],
            "code": "123456",
            "new_password": "BrandNewPass99!",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"reset": True}


@pytest.mark.asyncio
async def test_old_password_fails_new_password_works_after_reset(client):
    """After reset: old password fails (401), new password succeeds (200)."""
    await client.post("/auth/register", json=TEST_USER)
    new_password = "BrandNewPass99!"
    # Reset
    await client.post(
        "/auth/forgot-password/reset",
        json={
            "phone": TEST_USER["phone"],
            "code": "654321",
            "new_password": new_password,
        },
    )
    # Old password should fail
    login_old = await client.post("/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    assert login_old.status_code == 401, "Old password should no longer work"

    # New password should succeed
    login_new = await client.post("/auth/login", json={
        "email": TEST_USER["email"],
        "password": new_password,
    })
    assert login_new.status_code == 200, "New password should work"
    assert "access_token" in login_new.json()


@pytest.mark.asyncio
async def test_send_code_unknown_phone_returns_200(client):
    """Send-code with an unknown phone returns 200 (no user enumeration)."""
    resp = await client.post(
        "/auth/forgot-password/send-code",
        json={"phone": "+10000000000"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "sent"


@pytest.mark.asyncio
async def test_reset_with_short_password_returns_422(client):
    """Reset with a password shorter than 8 characters returns 422."""
    await client.post("/auth/register", json=TEST_USER)
    resp = await client.post(
        "/auth/forgot-password/reset",
        json={
            "phone": TEST_USER["phone"],
            "code": "123456",
            "new_password": "short",
        },
    )
    assert resp.status_code == 422

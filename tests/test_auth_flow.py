"""Integration tests for the auth flow — register, login, refresh, logout.

Tests the FULL user journey, not just individual endpoints.
These tests would have caught:
  - Register not returning access_token (403 after registration)
  - Session not surviving page reload (AUTH-04)
  - bcrypt 72-byte password limit
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base


# Use a file-based SQLite so all connections share the same database
_test_engine = create_async_engine("sqlite+aiosqlite:///./test_auth.db", echo=False)
_TestSession = async_sessionmaker(_test_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Create tables before each test, drop after."""
    from app.memory import models  # noqa: F401 — register models
    from app.models import auth  # noqa: F401
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    import os
    try:
        os.remove("./test_auth.db")
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def client():
    """HTTPX async client wired to the FastAPI app with overridden DB."""
    from app.main import app

    # Override the DB dependency in both auth routes and middleware
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
    "email": "test@example.com",
    "phone": "+15551234567",
    "password": "SecurePassword123!",
}


@pytest.mark.asyncio
async def test_register_returns_access_token(client):
    """Register must return access_token so the user is immediately authenticated."""
    resp = await client.post("/auth/register", json=TEST_USER)
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data, "Register must return access_token for auto-login"
    assert "user_id" in data


@pytest.mark.asyncio
async def test_register_sets_refresh_cookie(client):
    """Register must set httpOnly refresh_token cookie for session persistence."""
    resp = await client.post("/auth/register", json=TEST_USER)
    assert resp.status_code == 201
    cookies = resp.cookies
    assert "refresh_token" in cookies, "Register must set refresh_token cookie"


@pytest.mark.asyncio
async def test_register_then_access_protected_endpoint(client):
    """FLOW TEST: Register → use token → access /api/v1/me → 200.
    This is the exact flow that was broken (403 after registration)."""
    # Register
    resp = await client.post("/auth/register", json=TEST_USER)
    assert resp.status_code == 201
    token = resp.json()["access_token"]

    # Access protected endpoint with the token
    me_resp = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200, f"Expected 200, got {me_resp.status_code}: {me_resp.text}"
    me_data = me_resp.json()
    assert me_data["email"] == TEST_USER["email"]


@pytest.mark.asyncio
async def test_register_duplicate_email_returns_409(client):
    """Registering with an existing email returns 409."""
    await client.post("/auth/register", json=TEST_USER)
    resp = await client.post("/auth/register", json=TEST_USER)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_login_returns_access_token(client):
    """Login returns access_token and sets refresh cookie."""
    await client.post("/auth/register", json=TEST_USER)
    resp = await client.post("/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in resp.cookies


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client):
    """Wrong password returns 401."""
    await client.post("/auth/register", json=TEST_USER)
    resp = await client.post("/auth/login", json={
        "email": TEST_USER["email"],
        "password": "WrongPassword!",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_email_returns_401(client):
    """Non-existent email returns 401 (not 404 — don't leak user existence)."""
    resp = await client.post("/auth/login", json={
        "email": "nobody@example.com",
        "password": "anything",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotates_token(client):
    """Refresh endpoint issues new access token and rotates refresh cookie (AUTH-04)."""
    # Register (auto-login)
    reg = await client.post("/auth/register", json=TEST_USER)
    old_refresh = reg.cookies.get("refresh_token")
    assert old_refresh

    # Refresh
    resp = await client.post(
        "/auth/refresh",
        cookies={"refresh_token": old_refresh},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data

    # New refresh cookie should be different (rotation)
    new_refresh = resp.cookies.get("refresh_token")
    assert new_refresh
    assert new_refresh != old_refresh, "Refresh token must rotate on each use"


@pytest.mark.asyncio
async def test_refresh_then_access_protected(client):
    """FLOW TEST: Register → refresh → use new token → access /api/v1/me → 200.
    Simulates page reload (AUTH-04)."""
    reg = await client.post("/auth/register", json=TEST_USER)
    refresh_cookie = reg.cookies.get("refresh_token")

    # Simulate page reload: use refresh to get new access token
    refresh_resp = await client.post(
        "/auth/refresh",
        cookies={"refresh_token": refresh_cookie},
    )
    assert refresh_resp.status_code == 200
    new_token = refresh_resp.json()["access_token"]

    # Access protected endpoint with refreshed token
    me_resp = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {new_token}"},
    )
    assert me_resp.status_code == 200


@pytest.mark.asyncio
async def test_refresh_with_expired_cookie_returns_401(client):
    """Using an invalid/expired refresh token returns 401."""
    resp = await client.post(
        "/auth/refresh",
        cookies={"refresh_token": "completely-invalid-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_without_cookie_returns_401(client):
    """No refresh cookie returns 401."""
    resp = await client.post("/auth/refresh")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_session(client):
    """Logout clears the refresh cookie and invalidates the session."""
    reg = await client.post("/auth/register", json=TEST_USER)
    refresh_cookie = reg.cookies.get("refresh_token")

    # Logout
    resp = await client.post(
        "/auth/logout",
        cookies={"refresh_token": refresh_cookie},
    )
    assert resp.status_code == 204

    # Old refresh token should no longer work
    resp2 = await client.post(
        "/auth/refresh",
        cookies={"refresh_token": refresh_cookie},
    )
    assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_without_token_returns_403(client):
    """Accessing a protected endpoint without a Bearer token returns 401/403."""
    resp = await client.get("/api/v1/me")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_long_password_works(client):
    """Passwords longer than 72 bytes must work (bcrypt pre-hash fix)."""
    long_password = "A" * 200  # 200 chars, well over bcrypt's 72-byte limit
    user = {**TEST_USER, "password": long_password}
    reg = await client.post("/auth/register", json=user)
    assert reg.status_code == 201

    # Must be able to login with the same long password
    login = await client.post("/auth/login", json={
        "email": TEST_USER["email"],
        "password": long_password,
    })
    assert login.status_code == 200
    assert "access_token" in login.json()


@pytest.mark.asyncio
async def test_register_merges_sms_user(client):
    """FLOW TEST: SMS user exists → web register with same phone → merges into same user.
    This ensures conversation history is preserved when an SMS user creates a web account."""
    from app.database import Base
    from app.memory.models import User, Message

    # Step 1: Simulate SMS pipeline creating a user with messages
    async with _TestSession() as db:
        sms_user = User(phone=TEST_USER["phone"])
        db.add(sms_user)
        await db.commit()
        await db.refresh(sms_user)
        sms_user_id = sms_user.id

        # Add a message as if the SMS pipeline recorded it
        msg = Message(
            user_id=sms_user_id,
            direction="inbound",
            body="hello from SMS",
            intent="general",
        )
        db.add(msg)
        await db.commit()

    # Step 2: Register via web with the same phone number
    resp = await client.post("/auth/register", json=TEST_USER)
    assert resp.status_code == 201
    web_user_id = resp.json()["user_id"]

    # Step 3: The web user should be the SAME user (merged, not new)
    assert web_user_id == sms_user_id, (
        f"Expected merge into SMS user {sms_user_id}, got new user {web_user_id}"
    )

    # Step 4: Conversations should be visible via the web user's token
    token = resp.json()["access_token"]
    conv_resp = await client.get(
        "/api/v1/conversations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert conv_resp.status_code == 200
    conversations = conv_resp.json()["conversations"]
    assert len(conversations) >= 1, "SMS messages should be visible after merge"
    assert conversations[0]["body"] == "hello from SMS"


@pytest.mark.asyncio
async def test_register_rejects_duplicate_phone_with_email(client):
    """If a fully registered user (has email) exists with the same phone, reject with 409."""
    # Register first user
    await client.post("/auth/register", json=TEST_USER)

    # Try to register another user with same phone but different email
    resp = await client.post("/auth/register", json={
        "email": "other@example.com",
        "phone": TEST_USER["phone"],
        "password": "AnotherPass123!",
    })
    assert resp.status_code == 409

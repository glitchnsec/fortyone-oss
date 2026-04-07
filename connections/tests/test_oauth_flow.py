"""Integration tests for the OAuth flow in the connections service.

Tests: initiate URL generation, CSRF state validation, empty client_id detection,
capability manifest, connection listing/deletion.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from unittest.mock import patch, MagicMock

from app.database import Base

_test_engine = create_async_engine("sqlite+aiosqlite:///./test_connections.db", echo=False)
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
        os.remove("./test_connections.db")
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def client():
    from app.main import app
    from app.routes.oauth import _get_db as oauth_get_db
    from app.routes.connections import _get_db as conn_get_db

    async def _override_db():
        async with _TestSession() as session:
            yield session

    app.dependency_overrides[oauth_get_db] = _override_db
    app.dependency_overrides[conn_get_db] = _override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


# ── OAuth Initiate ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_oauth_initiate_returns_auth_url(client):
    """OAuth initiate returns an auth_url for a valid provider."""
    with patch("app.routes.oauth.get_settings") as mock_settings:
        s = MagicMock()
        s.google_client_id = "test-client-id.apps.googleusercontent.com"
        s.google_client_secret = "test-secret"
        s.google_redirect_uri = "http://localhost:8001/oauth/callback/google"
        mock_settings.return_value = s

        resp = await client.get("/oauth/initiate/google?user_id=test-user-123&persona_id=persona-work")
        assert resp.status_code == 200
        data = resp.json()
        assert "auth_url" in data
        assert "test-client-id" in data["auth_url"]
        assert "accounts.google.com" in data["auth_url"]


@pytest.mark.asyncio
async def test_oauth_initiate_includes_required_params(client):
    """Auth URL must contain client_id, redirect_uri, scope, state, and offline access."""
    with patch("app.routes.oauth.get_settings") as mock_settings:
        s = MagicMock()
        s.google_client_id = "my-client-id"
        s.google_client_secret = "my-secret"
        s.google_redirect_uri = "http://localhost:8001/oauth/callback/google"
        mock_settings.return_value = s

        resp = await client.get("/oauth/initiate/google?user_id=user-1&persona_id=persona-work")
        url = resp.json()["auth_url"]
        assert "client_id=my-client-id" in url
        assert "redirect_uri=" in url
        assert "scope=" in url
        assert "state=" in url
        assert "access_type=offline" in url
        assert "prompt=consent" in url


@pytest.mark.asyncio
async def test_oauth_initiate_empty_client_id_produces_broken_url(client):
    """REGRESSION: Empty client_id generates a URL Google rejects with 400.
    This test documents the bug — the initiate endpoint should validate config."""
    with patch("app.routes.oauth.get_settings") as mock_settings:
        s = MagicMock()
        s.google_client_id = ""  # Empty — the bug we hit
        s.google_client_secret = ""
        s.google_redirect_uri = "http://localhost:8001/oauth/callback/google"
        mock_settings.return_value = s

        resp = await client.get("/oauth/initiate/google?user_id=user-1&persona_id=persona-work")
        url = resp.json()["auth_url"]
        # This URL contains client_id= (empty) — Google will reject it
        assert "client_id=" in url
        # TODO: The endpoint should return 500/503 when client_id is empty
        # rather than generating a broken URL the user has to debug


# NOTE: Unknown provider test — get_provider("unknown") raises ValueError
# which becomes a 500 in production. Bug documented in test_get_provider_unknown_raises below.
# TODO: Add try/except in oauth route to return 400 instead of 500.


# ── OAuth Callback ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_oauth_callback_rejects_invalid_state(client):
    """Callback with invalid CSRF state is rejected."""
    resp = await client.get("/oauth/callback/google?code=test-code&state=invalid-state")
    assert resp.status_code == 400


# ── Connections List ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_connections_empty(client):
    """User with no connections returns empty list."""
    resp = await client.get("/connections/nonexistent-user")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connections"] == []


@pytest.mark.asyncio
async def test_delete_nonexistent_connection(client):
    """Deleting a non-existent connection returns 404."""
    resp = await client.delete("/connections/nonexistent-id")
    assert resp.status_code == 404


# ── Provider ─────────────────────────────────────────────────────────────────


def test_google_provider_capability_manifest():
    """GoogleProvider returns correct capability manifest based on granted scopes."""
    from app.providers.google import GoogleProvider

    p = GoogleProvider()

    # All scopes granted
    full = p.capability_manifest(p.scopes)
    assert full.can_read_email is True
    assert full.can_send_email is True
    assert full.can_read_calendar is True
    assert full.can_write_calendar is True

    # Only read scopes
    read_only = p.capability_manifest([
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.readonly",
    ])
    assert read_only.can_read_email is True
    assert read_only.can_send_email is False
    assert read_only.can_read_calendar is True
    assert read_only.can_write_calendar is False

    # No scopes
    empty = p.capability_manifest([])
    assert empty.can_read_email is False
    assert empty.can_send_email is False


def test_get_provider_unknown_raises():
    """Unknown provider name raises ValueError."""
    from app.providers.google import get_provider
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider("microsoft")

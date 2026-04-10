import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.crypto import decrypt, encrypt
from app.database import Base
from app.models import Connection, OAuthState, OAuthToken

_test_engine = create_async_engine("sqlite+aiosqlite:///./test_mcp_oauth.db", echo=False)
_TestSession = async_sessionmaker(_test_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    from app import models  # noqa: F401
    from app import crypto

    old_key = os.environ.get("ENCRYPTION_KEY")
    os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    crypto._fernet.cache_clear()

    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    try:
        os.remove("./test_mcp_oauth.db")
    except FileNotFoundError:
        pass
    if old_key is None:
        os.environ.pop("ENCRYPTION_KEY", None)
    else:
        os.environ["ENCRYPTION_KEY"] = old_key
    crypto._fernet.cache_clear()


@pytest_asyncio.fixture
async def client():
    from app.main import app
    from app.routes.mcp import _get_db as mcp_get_db

    async def _override_db():
        async with _TestSession() as session:
            yield session

    app.dependency_overrides[mcp_get_db] = _override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_mcp_oauth_initiate_returns_auth_url_and_stores_metadata(client):
    with patch("app.routes.mcp.get_settings") as mock_settings, patch(
        "app.routes.mcp.discover_oauth_metadata"
    ) as mock_discover:
        settings = mock_settings.return_value
        settings.mcp_allowlist = ""
        settings.dashboard_url = "http://localhost:8000"
        settings.mcp_oauth_redirect_uri = "http://localhost:8000/connections/callback"
        mock_discover.return_value = {
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
            "registration_endpoint": "https://auth.example.com/register",
            "client_id_metadata_document_supported": False,
            "scopes_supported": ["openid", "profile"],
        }

        with patch("app.routes.mcp.register_oauth_client", return_value={"client_id": "client-123"}):
            resp = await client.post(
                "/mcp/oauth/initiate",
                json={
                    "user_id": "user-1",
                    "persona_id": "persona-work",
                    "server_url": "https://mcp.example.com/mcp",
                    "name": "Work MCP",
                },
            )

    assert resp.status_code == 200
    auth_url = resp.json()["auth_url"]
    assert "https://auth.example.com/authorize" in auth_url
    assert "code_challenge_method=S256" in auth_url
    assert "client_id=client-123" in auth_url
    assert "resource=https%3A%2F%2Fmcp.example.com%2Fmcp" in auth_url

    async with _TestSession() as session:
        state_row = (await session.execute(select(OAuthState))).scalar_one()
        payload = json.loads(state_row.metadata_json)
        assert state_row.user_id == "user-1"
        assert state_row.persona_id == "persona-work"
        assert payload["server_url"] == "https://mcp.example.com/mcp"
        assert payload["client_id"] == "client-123"
        assert payload["code_verifier"]


@pytest.mark.asyncio
async def test_mcp_oauth_callback_creates_connection_and_clears_state(client):
    async with _TestSession() as session:
        session.add(
            OAuthState(
                state="state-123",
                user_id="user-1",
                persona_id="persona-work",
                metadata_json=json.dumps(
                    {
                        "server_url": "https://mcp.example.com/mcp",
                        "resource": "https://mcp.example.com/mcp",
                        "redirect_uri": "http://localhost:8000/connections/callback",
                        "client_id": "client-123",
                        "client_secret": None,
                        "code_verifier": "verifier-123",
                        "oauth_metadata": {
                            "token_endpoint": "https://auth.example.com/token",
                        },
                    }
                ),
            )
        )
        await session.commit()

    with patch("app.routes.mcp.exchange_oauth_code") as mock_exchange, patch(
        "app.routes.mcp.discover_tools"
    ) as mock_tools:
        mock_exchange.return_value = {
            "access_token": "access-123",
            "refresh_token": "refresh-123",
            "expires_in": 3600,
        }
        mock_tools.return_value = [{"name": "weather_lookup", "description": "Get weather"}]
        resp = await client.post(
            "/mcp/oauth/callback",
            json={"code": "code-123", "state": "state-123"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "mcp"
    assert data["persona_id"] == "persona-work"
    assert data["tools"] == ["weather_lookup"]

    async with _TestSession() as session:
        conn = (await session.execute(select(Connection))).scalar_one()
        token = (await session.execute(select(OAuthToken))).scalar_one()
        assert conn.granted_scopes == "weather_lookup"
        refresh_meta = json.loads(decrypt(token.refresh_token_enc))
        assert refresh_meta["refresh_token"] == "refresh-123"
        state_rows = (await session.execute(select(OAuthState))).scalars().all()
        assert state_rows == []


@pytest.mark.asyncio
async def test_get_fresh_mcp_token_refreshes_expired_token():
    from app.routes.mcp import _get_fresh_mcp_token

    async with _TestSession() as session:
        conn = Connection(
            user_id="user-1",
            provider="mcp",
            execution_type="mcp",
            status="connected",
            mcp_server_url="https://mcp.example.com/mcp",
            mcp_tools_json="[]",
            granted_scopes="",
        )
        session.add(conn)
        await session.flush()
        session.add(
            OAuthToken(
                connection_id=conn.id,
                access_token_enc=encrypt("stale-access"),
                refresh_token_enc=encrypt(
                    json.dumps(
                        {
                            "refresh_token": "refresh-123",
                            "token_endpoint": "https://auth.example.com/token",
                            "client_id": "client-123",
                            "client_secret": None,
                            "resource": "https://mcp.example.com/mcp",
                        }
                    )
                ),
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        )
        await session.commit()
        token = (await session.execute(select(OAuthToken))).scalar_one()
        conn = (await session.execute(select(Connection))).scalar_one()

        with patch("app.routes.mcp.refresh_oauth_token") as mock_refresh:
            mock_refresh.return_value = {
                "access_token": "fresh-access",
                "refresh_token": "fresh-refresh",
                "expires_in": 1800,
            }
            fresh = await _get_fresh_mcp_token(conn, token, session)

        assert fresh == "fresh-access"
        assert decrypt(token.access_token_enc) == "fresh-access"
        refresh_meta = json.loads(decrypt(token.refresh_token_enc))
        assert refresh_meta["refresh_token"] == "fresh-refresh"
        assert conn.status == "connected"


@pytest.mark.asyncio
async def test_discover_tools_uses_session_id_across_handshake():
    from app.providers import mcp as mcp_provider

    async def _fake_call(
        url,
        method,
        params=None,
        headers=None,
        timeout=mcp_provider.MCP_TIMEOUT,
        session_id=None,
        return_session_id=False,
    ):
        assert url == "https://mcp.example.com/mcp"
        assert headers == {"Authorization": "Bearer token-123"}
        _ = params
        _ = timeout
        if method == "initialize":
            assert session_id is None
            assert return_session_id is True
            return {}, "session-abc"
        if method == "tools/list":
            assert session_id == "session-abc"
            assert return_session_id is False
            return {"tools": [{"name": "weather_lookup"}]}
        raise AssertionError(f"Unexpected method call: {method}")

    async def _fake_notify(url, method, params=None, headers=None, session_id=None):
        assert url == "https://mcp.example.com/mcp"
        assert method == "notifications/initialized"
        assert headers == {"Authorization": "Bearer token-123"}
        assert session_id == "session-abc"
        assert params is None

    with patch.object(mcp_provider, "mcp_call", side_effect=_fake_call), patch.object(
        mcp_provider, "mcp_notify", side_effect=_fake_notify
    ):
        tools = await mcp_provider.discover_tools(
            "https://mcp.example.com/mcp",
            auth_headers={"Authorization": "Bearer token-123"},
        )

    assert tools == [{"name": "weather_lookup"}]


def test_validate_tool_name_accepts_hyphenated_names():
    from app.providers.mcp import validate_tool_name

    ok, reason = validate_tool_name("notion-search")

    assert ok is True
    assert reason == ""

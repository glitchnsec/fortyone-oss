"""Integration tests for per-persona connection scoping.

Verifies:
- OAuthState model has persona_id attribute
- All tool input models accept persona_id field
- _get_connection in gmail.py and calendar.py filters by persona_id
- list_connections endpoint supports persona_id filter
"""
import uuid
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from unittest.mock import patch, MagicMock

from app.database import Base
from app.models import Connection, OAuthToken, OAuthState

_test_engine = create_async_engine(
    "sqlite+aiosqlite:///./test_persona_oauth.db", echo=False,
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
        os.remove("./test_persona_oauth.db")
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def client():
    from app.main import app
    from app.routes.oauth import _get_db as oauth_get_db
    from app.routes.connections import _get_db as conn_get_db
    from app.routes.tools import _get_db as tools_get_db

    async def _override_db():
        async with _TestSession() as session:
            yield session

    app.dependency_overrides[oauth_get_db] = _override_db
    app.dependency_overrides[conn_get_db] = _override_db
    app.dependency_overrides[tools_get_db] = _override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


async def _seed_connection(user_id="user-1", provider="google", persona_id=None, with_token=False):
    """Insert a test connection (optionally with a dummy token)."""
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
        await session.flush()
        if with_token:
            from app.crypto import encrypt
            tok = OAuthToken(
                id=str(uuid.uuid4()),
                connection_id=conn.id,
                access_token_enc=encrypt("fake-access-token"),
                refresh_token_enc=encrypt("fake-refresh-token"),
            )
            session.add(tok)
        await session.commit()
        await session.refresh(conn)
        return conn


# ── Model tests ──────────────────────────────────────────────────────────────


def test_oauthstate_has_persona_id():
    """OAuthState model exposes persona_id column."""
    assert hasattr(OAuthState, "persona_id"), "OAuthState missing persona_id"


def test_all_tool_input_models_have_persona_id():
    """All four tool Pydantic input models accept persona_id."""
    from app.routes.tools import (
        GmailReadInput, GmailSendInput,
        CalendarListInput, CalendarCreateInput,
    )
    for Model in [GmailReadInput, GmailSendInput, CalendarListInput, CalendarCreateInput]:
        assert "persona_id" in Model.model_fields, f"{Model.__name__} missing persona_id"


# ── OAuth initiate with persona_id ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_oauth_initiate_requires_persona_id(client):
    """OAuth initiate without persona_id returns 422."""
    with patch("app.routes.oauth.get_settings") as mock_settings:
        s = MagicMock()
        s.google_client_id = "test-id"
        s.google_client_secret = "test-secret"
        s.google_redirect_uri = "http://localhost:8001/oauth/callback/google"
        mock_settings.return_value = s

        resp = await client.get("/oauth/initiate/google?user_id=user-1")
        assert resp.status_code == 422, "persona_id should be required"


@pytest.mark.asyncio
async def test_oauth_initiate_accepts_persona_id(client):
    """OAuth initiate with persona_id succeeds."""
    with patch("app.routes.oauth.get_settings") as mock_settings:
        s = MagicMock()
        s.google_client_id = "test-id"
        s.google_client_secret = "test-secret"
        s.google_redirect_uri = "http://localhost:8001/oauth/callback/google"
        mock_settings.return_value = s

        resp = await client.get("/oauth/initiate/google?user_id=user-1&persona_id=persona-work")
        assert resp.status_code == 200
        assert "auth_url" in resp.json()


# ── Connection lookup persona scoping ────────────────────────────────────────


@pytest.mark.asyncio
async def test_gmail_get_connection_filters_by_persona():
    """gmail._get_connection filters by persona_id when provided."""
    from app.tools.gmail import _get_connection
    # Seed two connections: one for persona-work, one for persona-personal
    await _seed_connection(user_id="user-scope", persona_id="persona-work", with_token=True)
    await _seed_connection(user_id="user-scope", persona_id="persona-personal", with_token=True)

    async with _TestSession() as session:
        # With persona_id filter, should get only matching connection
        conn_work, _ = await _get_connection("user-scope", session, persona_id="persona-work")
        assert conn_work.persona_id == "persona-work"

        conn_personal, _ = await _get_connection("user-scope", session, persona_id="persona-personal")
        assert conn_personal.persona_id == "persona-personal"


@pytest.mark.asyncio
async def test_calendar_get_connection_filters_by_persona():
    """calendar._get_connection filters by persona_id when provided."""
    from app.tools.calendar import _get_connection
    await _seed_connection(user_id="user-cal", persona_id="persona-work", with_token=True)
    await _seed_connection(user_id="user-cal", persona_id="persona-personal", with_token=True)

    async with _TestSession() as session:
        conn, _ = await _get_connection("user-cal", session, persona_id="persona-work")
        assert conn.persona_id == "persona-work"


@pytest.mark.asyncio
async def test_get_connection_without_persona_returns_any(setup_db):
    """Without persona_id, _get_connection returns any matching connection."""
    from app.tools.gmail import _get_connection
    await _seed_connection(user_id="user-any", persona_id="persona-work", with_token=True)

    async with _TestSession() as session:
        conn, _ = await _get_connection("user-any", session)
        assert conn is not None


# ── List connections persona filter ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_connections_filters_by_persona(client):
    """GET /connections/{user_id}?persona_id=X filters results."""
    await _seed_connection(user_id="user-list", persona_id="persona-work")
    await _seed_connection(user_id="user-list", persona_id="persona-personal")

    # Without filter — returns both
    resp = await client.get("/connections/user-list")
    assert resp.status_code == 200
    assert len(resp.json()["connections"]) == 2

    # With filter — returns only matching
    resp = await client.get("/connections/user-list?persona_id=persona-work")
    assert resp.status_code == 200
    conns = resp.json()["connections"]
    assert len(conns) == 1
    assert conns[0]["persona_id"] == "persona-work"

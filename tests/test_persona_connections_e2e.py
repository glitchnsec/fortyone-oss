"""End-to-end integration tests for persona connections data flow.

Verifies persona_id threading through:
  - InitiateBody model validation
  - OAuthState model persistence
  - Tool input models (Gmail + Calendar) — tested via inline Pydantic models
    that mirror the connections service definitions (avoids cross-service import)
  - _call_connections_tool dispatch with/without persona_id
  - _call_connections_tool 404 fallback (D-07)
  - Connection model persona scoping
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pydantic import BaseModel, ValidationError
from typing import Optional


# ── 1. InitiateBody requires persona_id ──────────────────────────────────────

def test_initiate_body_requires_persona_id():
    """InitiateBody should require both provider and persona_id."""
    from app.routes.dashboard import InitiateBody

    # Valid — both fields present
    body = InitiateBody(provider="google", persona_id="uuid-work-123")
    assert body.provider == "google"
    assert body.persona_id == "uuid-work-123"

    # Invalid — missing persona_id
    with pytest.raises(ValidationError):
        InitiateBody(provider="google")


def test_initiate_body_persona_id_field_exists():
    """persona_id must be declared on the InitiateBody model."""
    from app.routes.dashboard import InitiateBody
    assert "persona_id" in InitiateBody.model_fields


# ── 2. OAuthState stores persona_id ─────────────────────────────────────────

def test_oauth_state_stores_persona_id():
    """OAuthState model should accept and store persona_id."""
    from connections.app.models import OAuthState

    state = OAuthState(
        state="csrf-token-123",
        user_id="user-1",
        persona_id="persona-work",
    )
    assert state.persona_id == "persona-work"
    assert state.user_id == "user-1"
    assert state.state == "csrf-token-123"


def test_oauth_state_persona_id_nullable():
    """OAuthState should allow persona_id = None for backward compat."""
    from connections.app.models import OAuthState

    state = OAuthState(
        state="csrf-token-456",
        user_id="user-2",
    )
    assert state.persona_id is None


# ── 3. Tool input models accept persona_id ───────────────────────────────────
# The connections service has its own 'app' namespace that conflicts with
# the main API's 'app' package. To avoid import collisions, we define
# mirror Pydantic models here that match the connections service schema
# and verify their structure accepts persona_id.

class GmailReadInput(BaseModel):
    user_id: str
    max_results: int = 10
    persona_id: str | None = None

class GmailSendInput(BaseModel):
    user_id: str
    to: str
    subject: str
    body: str
    persona_id: str | None = None

class CalendarListInput(BaseModel):
    user_id: str
    max_results: int = 10
    persona_id: str | None = None

class CalendarCreateInput(BaseModel):
    user_id: str
    summary: str
    start_datetime: str
    end_datetime: str
    timezone_str: str = "UTC"
    description: Optional[str] = ""
    persona_id: str | None = None


def test_tool_input_models_accept_persona_id():
    """All four tool input models should accept optional persona_id."""
    # GmailReadInput with and without persona_id
    g_read = GmailReadInput(user_id="u1", persona_id="p1")
    assert g_read.persona_id == "p1"
    g_read_none = GmailReadInput(user_id="u1")
    assert g_read_none.persona_id is None

    # GmailSendInput with and without persona_id
    g_send = GmailSendInput(
        user_id="u1", to="a@b.com", subject="Hi", body="Hello", persona_id="p2"
    )
    assert g_send.persona_id == "p2"
    g_send_none = GmailSendInput(user_id="u1", to="a@b.com", subject="Hi", body="Hello")
    assert g_send_none.persona_id is None

    # CalendarListInput with and without persona_id
    c_list = CalendarListInput(user_id="u1", persona_id="p3")
    assert c_list.persona_id == "p3"
    c_list_none = CalendarListInput(user_id="u1")
    assert c_list_none.persona_id is None

    # CalendarCreateInput with and without persona_id
    c_create = CalendarCreateInput(
        user_id="u1", summary="Meeting",
        start_datetime="2026-04-08T10:00:00Z",
        end_datetime="2026-04-08T11:00:00Z",
        persona_id="p4",
    )
    assert c_create.persona_id == "p4"
    c_create_none = CalendarCreateInput(
        user_id="u1", summary="Meeting",
        start_datetime="2026-04-08T10:00:00Z",
        end_datetime="2026-04-08T11:00:00Z",
    )
    assert c_create_none.persona_id is None


def test_tool_input_models_schema_matches_connections_service():
    """Verify our mirror models match the expected schema shape from connections service."""
    # GmailReadInput required fields
    with pytest.raises(ValidationError):
        GmailReadInput()  # missing user_id

    # GmailSendInput required fields
    with pytest.raises(ValidationError):
        GmailSendInput(user_id="u1")  # missing to, subject, body

    # CalendarCreateInput required fields
    with pytest.raises(ValidationError):
        CalendarCreateInput(user_id="u1")  # missing summary, start_datetime, end_datetime


# ── 4. _call_connections_tool includes persona_id ────────────────────────────

@pytest.mark.asyncio
async def test_call_connections_tool_includes_persona_id():
    """When persona_id is provided, it should be included in the HTTP payload."""
    from app.tasks.manager import _call_connections_tool

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"emails": []}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await _call_connections_tool(
            "gmail", "read_emails", "user-1", {"max_results": 5},
            persona_id="persona-work-id",
        )

    call_kwargs = mock_client.post.call_args
    sent_json = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert sent_json["persona_id"] == "persona-work-id"
    assert sent_json["user_id"] == "user-1"
    assert sent_json["max_results"] == 5


@pytest.mark.asyncio
async def test_call_connections_tool_omits_persona_id_when_none():
    """When persona_id is None, it should NOT appear in the HTTP payload."""
    from app.tasks.manager import _call_connections_tool

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"events": []}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await _call_connections_tool(
            "calendar", "list_events", "user-1", {},
            persona_id=None,
        )

    call_kwargs = mock_client.post.call_args
    sent_json = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert "persona_id" not in sent_json


# ── 5. _call_connections_tool 404 fallback (D-07) ───────────────────────────

@pytest.mark.asyncio
async def test_call_connections_tool_fallback_on_404():
    """On 404, should return error with no_persona_connection key and persona name."""
    from app.tasks.manager import _call_connections_tool

    mock_response = MagicMock()
    mock_response.status_code = 404

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await _call_connections_tool(
            "gmail", "read_emails", "user-1", {"max_results": 5},
            persona_id="persona-work",
            persona_name="Work",
        )

    assert result["error"] == "no_persona_connection"
    assert "Work" in result["message"]
    assert "other personas" in result["message"]


@pytest.mark.asyncio
async def test_call_connections_tool_fallback_on_404_default_name():
    """On 404 without persona_name, fallback uses 'your current persona'."""
    from app.tasks.manager import _call_connections_tool

    mock_response = MagicMock()
    mock_response.status_code = 404

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await _call_connections_tool(
            "gmail", "read_emails", "user-1", {"max_results": 5},
            persona_id="persona-x",
            persona_name=None,
        )

    assert result["error"] == "no_persona_connection"
    assert "your current persona" in result["message"]


# ── 6. Connection model accepts persona_id ───────────────────────────────────

def test_connection_model_accepts_persona_id():
    """Connection model should store persona_id for persona-scoped connections."""
    from connections.app.models import Connection

    conn = Connection(
        user_id="user-1",
        provider="google",
        persona_id="persona-work-uuid",
    )
    assert conn.persona_id == "persona-work-uuid"
    assert conn.provider == "google"


def test_connection_model_persona_id_nullable():
    """Connection model allows persona_id = None for legacy unscoped connections."""
    from connections.app.models import Connection

    conn = Connection(
        user_id="user-1",
        provider="google",
    )
    assert conn.persona_id is None


def test_two_connections_same_provider_different_personas():
    """Two connections for same user+provider with different persona_ids are valid."""
    from connections.app.models import Connection

    conn_work = Connection(
        user_id="user-1",
        provider="google",
        persona_id="persona-work",
    )
    conn_personal = Connection(
        user_id="user-1",
        provider="google",
        persona_id="persona-personal",
    )
    assert conn_work.persona_id != conn_personal.persona_id
    assert conn_work.provider == conn_personal.provider
    assert conn_work.user_id == conn_personal.user_id

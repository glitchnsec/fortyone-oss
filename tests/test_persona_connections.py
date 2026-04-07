"""Tests for persona_id threading through the main API.

Verifies:
  - InitiateBody model requires persona_id
  - _call_connections_tool includes persona_id in payload when provided
  - _call_connections_tool omits persona_id when None
  - D-07 fallback returns helpful message on 404
"""
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ── Model validation ─────────────────────────────────────────────────────────

def test_initiate_body_requires_persona_id():
    """InitiateBody should require both provider and persona_id."""
    from app.routes.dashboard import InitiateBody

    # Valid — has both fields
    body = InitiateBody(provider="google", persona_id="uuid-123")
    assert body.provider == "google"
    assert body.persona_id == "uuid-123"

    # Invalid — missing persona_id
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        InitiateBody(provider="google")


def test_initiate_body_persona_id_in_model_fields():
    """persona_id must be declared on the model."""
    from app.routes.dashboard import InitiateBody
    assert "persona_id" in InitiateBody.model_fields


# ── _call_connections_tool persona_id threading ──────────────────────────────

@pytest.mark.asyncio
async def test_call_connections_tool_includes_persona_id():
    """When persona_id is provided, it should be included in the JSON payload."""
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
            persona_id="persona-uuid-abc",
        )

    # Verify the POST payload included persona_id
    call_args = mock_client.post.call_args
    posted_json = call_args.kwargs.get("json") or call_args[1].get("json")
    assert posted_json["persona_id"] == "persona-uuid-abc"
    assert posted_json["user_id"] == "user-1"
    assert posted_json["max_results"] == 5


@pytest.mark.asyncio
async def test_call_connections_tool_omits_persona_id_when_none():
    """When persona_id is None, it should NOT appear in the JSON payload."""
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

    call_args = mock_client.post.call_args
    posted_json = call_args.kwargs.get("json") or call_args[1].get("json")
    assert "persona_id" not in posted_json


# ── D-07 fallback on 404 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_connections_tool_404_fallback():
    """404 from connections service should return D-07 fallback message."""
    from app.tasks.manager import _call_connections_tool

    mock_response = MagicMock()
    mock_response.status_code = 404

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await _call_connections_tool(
            "gmail", "read_emails", "user-1", {},
            persona_id="persona-uuid",
            persona_name="Work",
        )

    assert result["error"] == "no_persona_connection"
    assert "Work" in result["message"]
    assert "other personas" in result["message"]


@pytest.mark.asyncio
async def test_call_connections_tool_404_fallback_default_persona_name():
    """404 fallback uses 'your current persona' when no persona_name given."""
    from app.tasks.manager import _call_connections_tool

    mock_response = MagicMock()
    mock_response.status_code = 404

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await _call_connections_tool(
            "calendar", "list_events", "user-1", {},
            persona_id="persona-uuid",
        )

    assert result["error"] == "no_persona_connection"
    assert "your current persona" in result["message"]

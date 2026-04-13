"""Tests for cross-persona tool error messages (UAT Test 4 fix).

Verifies that when a tool call fails because the active persona lacks a
connection, the error message names which specific persona HAS the tool.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_find_persona_with_tool_found():
    """When another persona has the tool, returns that persona's name."""
    from app.core.capabilities import find_persona_with_tool

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "connections": [
            {
                "persona_id": "work-id",
                "provider": "google",
                "status": "connected",
                "capabilities": {"tools": ["read_emails"]},
            },
            {
                "persona_id": "personal-id",
                "provider": "slack",
                "status": "connected",
                "capabilities": {"tools": ["slack_read_channels", "slack_get_workspace", "slack_read_threads"]},
            },
        ]
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    # Mock persona DB lookup
    mock_persona = MagicMock()
    mock_persona.name = "Personal"

    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_persona

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.core.capabilities.httpx.AsyncClient", return_value=mock_client), \
         patch("app.core.capabilities.get_settings") as mock_settings, \
         patch("app.database.AsyncSessionLocal", return_value=mock_session):
        mock_settings.return_value.connections_service_url = "http://localhost:8001"

        result = await find_persona_with_tool("user-1", "slack_read_channels", exclude_persona_id="work-id")
        assert result == "Personal"


@pytest.mark.asyncio
async def test_find_persona_with_tool_not_found():
    """When no other persona has the tool, returns None."""
    from app.core.capabilities import find_persona_with_tool

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "connections": [
            {
                "persona_id": "work-id",
                "provider": "google",
                "status": "connected",
                "capabilities": {"tools": ["read_emails"]},
            },
        ]
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.core.capabilities.httpx.AsyncClient", return_value=mock_client), \
         patch("app.core.capabilities.get_settings") as mock_settings:
        mock_settings.return_value.connections_service_url = "http://localhost:8001"

        result = await find_persona_with_tool("user-1", "slack_read_channels", exclude_persona_id="work-id")
        assert result is None


@pytest.mark.asyncio
async def test_tool_failure_message_with_alternate_persona():
    """_tool_failure_user_message includes alternate persona name when available."""
    from app.tasks.manager import _tool_failure_user_message

    with patch("app.core.capabilities.find_persona_with_tool", new_callable=AsyncMock) as mock_find:
        mock_find.return_value = "Personal"

        msg = await _tool_failure_user_message(
            "slack_read_channels", "Work", "http://dash",
            user_id="u1", persona_id="work-id",
        )
        assert "Personal persona has this connected" in msg
        assert "Want me to use that instead?" in msg


@pytest.mark.asyncio
async def test_tool_failure_message_without_alternate_persona():
    """_tool_failure_user_message falls back to dashboard link when no alternate."""
    from app.tasks.manager import _tool_failure_user_message

    with patch("app.core.capabilities.find_persona_with_tool", new_callable=AsyncMock) as mock_find:
        mock_find.return_value = None

        msg = await _tool_failure_user_message(
            "slack_read_channels", "Work", "http://dash",
            user_id="u1", persona_id="work-id",
        )
        assert "Set it up at" in msg
        assert "Want me to use" not in msg


@pytest.mark.asyncio
async def test_call_connections_tool_404_with_alternate_persona():
    """_call_connections_tool 404 response includes alternate persona name."""
    from app.tasks.manager import _call_connections_tool

    mock_response = MagicMock()
    mock_response.status_code = 404

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch("app.tasks.manager.get_settings") as mock_settings, \
         patch("app.core.capabilities.find_persona_with_tool", new_callable=AsyncMock) as mock_find:
        mock_settings.return_value.connections_service_url = "http://localhost:8001"
        mock_settings.return_value.dashboard_url = "http://dash"
        mock_find.return_value = "Personal"

        result = await _call_connections_tool(
            "slack", "slack_read_channels", "u1", {},
            persona_id="work-id", persona_name="Work",
        )
        assert "Personal persona has it" in result["message"]
        assert "Want me to use your Personal persona" in result["message"]

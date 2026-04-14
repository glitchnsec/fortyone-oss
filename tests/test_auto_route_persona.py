"""Tests for tool-aware persona auto-routing (Phase 10.1 Plan 02).

Verifies all auto-routing paths in _execute_tool:
  1. Single-match auto-switch: exactly one other persona has the tool
  2. Multi-match disambiguation: 2+ personas have the tool
  3. No-match fallback: no persona has the tool
  4. Normal path: active persona already has the tool (no auto-routing)
  5. find_personas_with_tool returns all matches (not just first)
  6. find_personas_with_tool excludes the specified persona_id
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers ──────────────────────────────────────────────────────────

def _make_payload(
    user_id: str = "user-1",
    persona_id: str = "work-id",
    persona: str = "Work",
    body: str = "check my emails",
):
    """Build a minimal payload dict for _execute_tool calls."""
    return {
        "user_id": user_id,
        "persona_id": persona_id,
        "persona": persona,
        "persona_confidence": 0.9,
        "source": "sms",
        "body": body,
    }


def _mock_redis_with_caps(tools: list[str]):
    """Return an AsyncMock Redis that serves get_capabilities with given tools."""
    import json
    r = AsyncMock()
    # Cache miss on first call, so _fetch won't be hit if we mock get_capabilities
    r.get.return_value = json.dumps({"tools": tools})
    return r


# ── Test 1: Auto-switch when exactly one other persona has the tool ──

@pytest.mark.asyncio
async def test_auto_switch_single_persona():
    """Per D-02: exactly one other persona has tool -> auto-switch, prepend note."""
    from app.tasks.manager import _execute_tool

    payload = _make_payload(persona_id="work-id", persona="Work")
    redis_mock = _mock_redis_with_caps([])  # active persona has NO tools

    mock_queue_client = MagicMock()
    mock_queue_client._redis = redis_mock

    with patch("app.queue.client.queue_client", mock_queue_client), \
         patch("app.core.capabilities.get_capabilities", new_callable=AsyncMock) as mock_caps, \
         patch("app.core.capabilities.find_personas_with_tool", new_callable=AsyncMock) as mock_find, \
         patch("app.tasks.manager._call_connections_tool", new_callable=AsyncMock) as mock_call:

        # Active persona has no tools
        mock_caps.return_value = {"tools": []}

        # Exactly one other persona has "read_emails"
        mock_find.return_value = [
            {"persona_id": "personal-id", "persona_name": "Personal"},
        ]

        # Connection tool returns a result with "content" field
        mock_call.return_value = {"content": "You have 3 emails"}

        result = await _execute_tool("read_emails", "{}", payload)

        # Should have auto-switched to Personal persona
        mock_call.assert_called_once()
        call_kwargs = mock_call.call_args
        assert call_kwargs[1]["persona_id"] == "personal-id"
        assert call_kwargs[1]["persona_name"] == "Personal"

        # Result should have auto-switch note prepended
        assert "[Using your Personal persona for this action.]" in result["content"]
        assert "You have 3 emails" in result["content"]


# ── Test 2: Disambiguation when multiple personas have the tool ──

@pytest.mark.asyncio
async def test_disambiguation_multiple_personas():
    """Per D-02: 2+ personas have tool -> return disambiguation error."""
    from app.tasks.manager import _execute_tool

    payload = _make_payload(persona_id="work-id", persona="Work")
    redis_mock = _mock_redis_with_caps([])

    mock_queue_client = MagicMock()
    mock_queue_client._redis = redis_mock

    with patch("app.queue.client.queue_client", mock_queue_client), \
         patch("app.core.capabilities.get_capabilities", new_callable=AsyncMock) as mock_caps, \
         patch("app.core.capabilities.find_personas_with_tool", new_callable=AsyncMock) as mock_find:

        mock_caps.return_value = {"tools": []}

        # Multiple personas have the tool
        mock_find.return_value = [
            {"persona_id": "personal-id", "persona_name": "Personal"},
            {"persona_id": "freelance-id", "persona_name": "Freelance"},
        ]

        result = await _execute_tool("slack_read_channels", "{}", payload)

        assert result["error"] == "persona_disambiguation"
        assert "Multiple personas" in result["user_message"]
        assert "Personal" in result["user_message"]
        assert "Freelance" in result["user_message"]


# ── Test 3: No-match fallback when no persona has the tool ──

@pytest.mark.asyncio
async def test_no_match_fallback():
    """Per D-02: no persona has tool -> return missing_capability error."""
    from app.tasks.manager import _execute_tool

    payload = _make_payload(persona_id="work-id", persona="Work")
    redis_mock = _mock_redis_with_caps([])

    mock_queue_client = MagicMock()
    mock_queue_client._redis = redis_mock

    with patch("app.queue.client.queue_client", mock_queue_client), \
         patch("app.core.capabilities.get_capabilities", new_callable=AsyncMock) as mock_caps, \
         patch("app.core.capabilities.find_personas_with_tool", new_callable=AsyncMock) as mock_find, \
         patch("app.tasks.manager.get_settings") as mock_settings, \
         patch("app.tasks.manager._tool_failure_user_message", new_callable=AsyncMock) as mock_msg:

        mock_caps.return_value = {"tools": []}
        mock_find.return_value = []  # No persona has it
        mock_settings.return_value.dashboard_url = "http://dash"
        mock_msg.return_value = "Tool not available. Set it up at http://dash"

        result = await _execute_tool("read_emails", "{}", payload)

        assert result["error"] == "missing_capability"
        assert "user_message" in result


# ── Test 4: No auto-routing when active persona HAS the tool ──

@pytest.mark.asyncio
async def test_no_auto_route_when_tool_available():
    """When active persona has the tool, no auto-routing occurs (normal path)."""
    from app.tasks.manager import _execute_tool

    payload = _make_payload(persona_id="work-id", persona="Work")
    redis_mock = _mock_redis_with_caps(["read_emails", "send_email"])

    mock_queue_client = MagicMock()
    mock_queue_client._redis = redis_mock

    with patch("app.queue.client.queue_client", mock_queue_client), \
         patch("app.core.capabilities.get_capabilities", new_callable=AsyncMock) as mock_caps, \
         patch("app.core.capabilities.find_personas_with_tool", new_callable=AsyncMock) as mock_find, \
         patch("app.tasks.manager._call_connections_tool", new_callable=AsyncMock) as mock_call:

        # Active persona HAS the tool
        mock_caps.return_value = {"tools": ["read_emails", "send_email"]}

        mock_call.return_value = {"content": "You have 3 emails"}

        result = await _execute_tool("read_emails", "{}", payload)

        # find_personas_with_tool should NOT have been called
        mock_find.assert_not_called()

        # Result should NOT have auto-switch note
        assert result["content"] == "You have 3 emails"
        assert "Using your" not in result.get("content", "")


# ── Test 5: find_personas_with_tool returns all matching personas ──

@pytest.mark.asyncio
async def test_find_personas_with_tool_returns_multiple():
    """find_personas_with_tool returns ALL matching personas, not just first."""
    from app.core.capabilities import find_personas_with_tool

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
                "provider": "google",
                "status": "connected",
                "capabilities": {"tools": ["read_emails"]},
            },
            {
                "persona_id": "freelance-id",
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

    # Mock persona DB lookup: return all three personas
    mock_persona_work = MagicMock()
    mock_persona_work.id = "work-id"
    mock_persona_work.name = "Work"

    mock_persona_personal = MagicMock()
    mock_persona_personal.id = "personal-id"
    mock_persona_personal.name = "Personal"

    mock_persona_freelance = MagicMock()
    mock_persona_freelance.id = "freelance-id"
    mock_persona_freelance.name = "Freelance"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [
        mock_persona_personal, mock_persona_freelance,
    ]

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.core.capabilities.httpx.AsyncClient", return_value=mock_client), \
         patch("app.core.capabilities.get_settings") as mock_settings, \
         patch("app.database.AsyncSessionLocal", return_value=mock_session):
        mock_settings.return_value.connections_service_url = "http://localhost:8001"

        # Exclude work-id — should get personal and freelance
        result = await find_personas_with_tool(
            "user-1", "read_emails", exclude_persona_id="work-id",
        )
        assert len(result) == 2
        persona_names = {p["persona_name"] for p in result}
        assert "Personal" in persona_names
        assert "Freelance" in persona_names


# ── Test 6: find_personas_with_tool excludes the current persona ──

@pytest.mark.asyncio
async def test_find_personas_with_tool_excludes_current():
    """find_personas_with_tool correctly excludes the specified persona_id."""
    from app.core.capabilities import find_personas_with_tool

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

    mock_persona = MagicMock()
    mock_persona.id = "personal-id"
    mock_persona.name = "Personal"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_persona]

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.core.capabilities.httpx.AsyncClient", return_value=mock_client), \
         patch("app.core.capabilities.get_settings") as mock_settings, \
         patch("app.database.AsyncSessionLocal", return_value=mock_session):
        mock_settings.return_value.connections_service_url = "http://localhost:8001"

        # Exclude work-id — should only get personal-id
        result = await find_personas_with_tool(
            "user-1", "read_emails", exclude_persona_id="work-id",
        )
        assert len(result) == 1
        assert result[0]["persona_id"] == "personal-id"
        assert result[0]["persona_name"] == "Personal"

        # Now exclude personal-id — should only get work-id
        mock_persona_work = MagicMock()
        mock_persona_work.id = "work-id"
        mock_persona_work.name = "Work"
        mock_result.scalars.return_value.all.return_value = [mock_persona_work]

        result2 = await find_personas_with_tool(
            "user-1", "read_emails", exclude_persona_id="personal-id",
        )
        assert len(result2) == 1
        assert result2[0]["persona_id"] == "work-id"
        assert result2[0]["persona_name"] == "Work"

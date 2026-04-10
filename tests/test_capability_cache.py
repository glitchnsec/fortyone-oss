"""Integration tests for dynamic capability discovery (Phase 08/09).

Covers:
  - resolve_capability_persona: persona scoping (proactive, shared, low conf, explicit)
  - get_capabilities: caching in Redis, graceful degradation (tools list format)
  - invalidate_capabilities: cache key deletion (v1 + v2)
  - _fetch_capabilities: aggregation across multiple connections (tools list)
  - _tool_failure_user_message: error message formatting
"""
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch


# -- Helpers: dict-backed async Redis mock ---------------------------------

class FakeRedis:
    """Minimal async Redis mock backed by a plain dict."""

    def __init__(self):
        self._store: dict[str, str] = {}

    async def get(self, key: str):
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int = None):
        self._store[key] = value

    async def delete(self, key: str):
        self._store.pop(key, None)

    async def scan_iter(self, match: str = "*", count: int = 100):
        import fnmatch
        for k in list(self._store.keys()):
            if fnmatch.fnmatch(k, match):
                yield k


# -- Persona resolution tests ----------------------------------------------

class TestResolveCapabilityPersona:
    def test_proactive_returns_none(self):
        from app.core.capabilities import resolve_capability_persona
        result = resolve_capability_persona({"source": "proactive", "persona": "work", "persona_id": "abc"})
        assert result is None

    def test_scheduler_returns_none(self):
        from app.core.capabilities import resolve_capability_persona
        result = resolve_capability_persona({"source": "scheduled_execute", "persona": "work", "persona_id": "abc"})
        assert result is None

    def test_shared_returns_none(self):
        from app.core.capabilities import resolve_capability_persona
        result = resolve_capability_persona({"source": "user_message", "persona": "shared", "persona_id": "abc", "persona_confidence": 0.9})
        assert result is None

    def test_low_confidence_returns_none(self):
        from app.core.capabilities import resolve_capability_persona
        result = resolve_capability_persona({"source": "user_message", "persona": "work", "persona_id": "xyz", "persona_confidence": 0.3})
        assert result is None

    def test_explicit_returns_id(self):
        from app.core.capabilities import resolve_capability_persona
        result = resolve_capability_persona({"source": "user_message", "persona": "work", "persona_id": "abc-123", "persona_confidence": 0.9})
        assert result == "abc-123"


# -- Cache behaviour (async) -----------------------------------------------

@pytest.mark.asyncio
async def test_get_capabilities_caches_in_redis():
    """Second call for same user should hit cache, not connections service."""
    from app.core.capabilities import get_capabilities

    fake_redis = FakeRedis()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "connections": [
            {"id": "c1", "capabilities": {"tools": ["read_emails", "send_email"]}}
        ]
    }

    call_count = 0

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None):
            nonlocal call_count
            call_count += 1
            return mock_response

    with patch("app.core.capabilities.httpx.AsyncClient", return_value=FakeClient()):
        caps1 = await get_capabilities(fake_redis, "user1")
        caps2 = await get_capabilities(fake_redis, "user1")

    assert call_count == 1, f"Expected 1 HTTP call (cache hit on second), got {call_count}"
    assert "read_emails" in caps1["tools"]
    assert "send_email" in caps1["tools"]
    assert caps2 == caps1


@pytest.mark.asyncio
async def test_get_capabilities_graceful_degradation():
    """On connection error, all known tools should be returned (D-08)."""
    from app.core.capabilities import get_capabilities, _ALL_TOOLS

    fake_redis = FakeRedis()

    with patch("app.core.capabilities.httpx.AsyncClient", side_effect=ConnectionError("down")):
        caps = await get_capabilities(fake_redis, "user-err")

    assert isinstance(caps["tools"], list)
    for tool in ["read_emails", "send_email", "list_events", "create_event", "web_search"]:
        assert tool in caps["tools"], f"Expected {tool} in degraded tools list"


# -- Invalidation ----------------------------------------------------------

@pytest.mark.asyncio
async def test_invalidate_capabilities():
    """invalidate_capabilities should delete all user cache keys (v1 and v2)."""
    from app.core.capabilities import invalidate_capabilities

    fake_redis = FakeRedis()
    fake_redis._store["capabilities:user123:all"] = json.dumps({"tools": ["read_emails"]})
    fake_redis._store["capabilities_v2:user123:all"] = json.dumps({"tools": ["read_emails"]})
    fake_redis._store["capabilities_v2:user123:persona1"] = json.dumps({"tools": ["send_email"]})
    fake_redis._store["capabilities_v2:other_user:all"] = json.dumps({"tools": []})

    await invalidate_capabilities(fake_redis, "user123")

    assert "capabilities:user123:all" not in fake_redis._store
    assert "capabilities_v2:user123:all" not in fake_redis._store
    assert "capabilities_v2:user123:persona1" not in fake_redis._store
    # Other user's key should be untouched
    assert "capabilities_v2:other_user:all" in fake_redis._store


# -- Multi-connection aggregation ------------------------------------------

@pytest.mark.asyncio
async def test_fetch_aggregates_multiple_connections():
    """_fetch_capabilities should union tool names across multiple connections."""
    from app.core.capabilities import _fetch_capabilities

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "connections": [
            {"id": "c1", "capabilities": {"tools": ["read_emails"]}},
            {"id": "c2", "capabilities": {"tools": ["send_email", "read_emails"]}},
        ]
    }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None):
            return mock_response

    with patch("app.core.capabilities.httpx.AsyncClient", return_value=FakeClient()):
        caps = await _fetch_capabilities("user-multi")

    assert "read_emails" in caps["tools"]
    assert "send_email" in caps["tools"]
    # Deduplication: read_emails should appear only once
    assert caps["tools"].count("read_emails") == 1


# -- Cache key uses v2 prefix ----------------------------------------------

@pytest.mark.asyncio
async def test_cache_key_uses_v2_prefix():
    """Cache key should use capabilities_v2 prefix to avoid stale v1 entries."""
    from app.core.capabilities import get_capabilities

    fake_redis = FakeRedis()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"connections": []}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None):
            return mock_response

    with patch("app.core.capabilities.httpx.AsyncClient", return_value=FakeClient()):
        await get_capabilities(fake_redis, "user-v2-test")

    assert "capabilities_v2:user-v2-test:all" in fake_redis._store
    assert "capabilities:user-v2-test:all" not in fake_redis._store


# -- Error message formatting ----------------------------------------------

# -- MCP tools in capability cache -------------------------------------------

@pytest.mark.asyncio
async def test_capabilities_include_mcp_tools():
    """MCP tool names should appear in the tools list with namespaced prefix."""
    from app.core.capabilities import _fetch_capabilities

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "connections": [
            {
                "id": "conn-mcp-1234-5678",
                "provider": "mcp",
                "execution_type": "mcp",
                "status": "connected",
                "capabilities": {"tools": []},
                "mcp_tools": [
                    {"name": "get_weather", "description": "Get weather"},
                    {"name": "translate", "description": "Translate text"},
                ],
            },
            {
                "id": "c2",
                "provider": "google",
                "capabilities": {"tools": ["read_emails"]},
            },
        ]
    }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None):
            return mock_response

    with patch("app.core.capabilities.httpx.AsyncClient", return_value=FakeClient()):
        caps = await _fetch_capabilities("user-mcp-test")

    # MCP tools should be namespaced
    assert "mcp_conn-mcp_get_weather" in caps["tools"]
    assert "mcp_conn-mcp_translate" in caps["tools"]
    # Google tools too
    assert "read_emails" in caps["tools"]


@pytest.mark.asyncio
async def test_mcp_tool_connections_mapping():
    """mcp_tool_connections dict should map namespaced tool names to connection IDs."""
    from app.core.capabilities import _fetch_capabilities

    conn_id = "mcp-conn-abcdef12"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "connections": [
            {
                "id": conn_id,
                "provider": "mcp",
                "execution_type": "mcp",
                "status": "connected",
                "capabilities": {"tools": []},
                "mcp_tools": [
                    {"name": "fetch_data", "description": "Fetch data"},
                ],
            },
        ]
    }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None):
            return mock_response

    with patch("app.core.capabilities.httpx.AsyncClient", return_value=FakeClient()):
        caps = await _fetch_capabilities("user-mcp-map")

    short_id = conn_id[:8]
    namespaced = f"mcp_{short_id}_fetch_data"
    assert namespaced in caps["tools"]
    assert "mcp_tool_connections" in caps
    assert caps["mcp_tool_connections"][namespaced] == conn_id


@pytest.mark.asyncio
async def test_graceful_degradation_includes_mcp():
    """On connection error, degradation returns a tools list (basic tools)."""
    from app.core.capabilities import get_capabilities

    fake_redis = FakeRedis()

    with patch("app.core.capabilities.httpx.AsyncClient", side_effect=ConnectionError("down")):
        caps = await get_capabilities(fake_redis, "user-mcp-degrade")

    # Should return a list of tools (graceful degradation)
    assert isinstance(caps["tools"], list)
    assert len(caps["tools"]) > 0
    # Should include standard built-in tools
    assert "read_emails" in caps["tools"]


# -- Error message formatting ----------------------------------------------

class TestErrorMessages:
    def test_send_email_message(self):
        from app.tasks.manager import _tool_failure_user_message
        msg = _tool_failure_user_message("send_email", "Work", "http://localhost:8000")
        assert "Gmail" in msg
        assert "Work" in msg
        assert "/connections" in msg

    def test_list_events_message(self):
        from app.tasks.manager import _tool_failure_user_message
        msg = _tool_failure_user_message("list_events", "Personal", "http://localhost:8000")
        assert "Calendar" in msg
        assert "Personal" in msg
        assert "/connections" in msg

    def test_default_message(self):
        from app.tasks.manager import _tool_failure_user_message
        msg = _tool_failure_user_message("unknown_tool", "Work", "http://localhost:8000")
        assert "/connections" in msg

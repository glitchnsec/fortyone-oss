"""Integration tests for dynamic capability discovery (Phase 08).

Covers:
  - resolve_capability_persona: persona scoping (proactive, shared, low conf, explicit)
  - TOOL_CAPABILITY_MAP: completeness check
  - get_capabilities: caching in Redis, graceful degradation
  - invalidate_capabilities: cache key deletion
  - _fetch_capabilities: aggregation across multiple connections
  - _tool_failure_user_message: error message formatting
"""
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers: dict-backed async Redis mock ───────────────────────────────

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


# ── Persona resolution tests ───────────────────────────────────────────

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


# ── TOOL_CAPABILITY_MAP completeness ────────────────────────────────────

class TestToolCapabilityMap:
    def test_completeness(self):
        from app.core.capabilities import TOOL_CAPABILITY_MAP
        expected_tools = {"read_emails", "send_email", "list_events", "create_event"}
        assert set(TOOL_CAPABILITY_MAP.keys()) == expected_tools


# ── Cache behaviour (async) ─────────────────────────────────────────────

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
            {"id": "c1", "capabilities": {"can_read_email": True, "can_send_email": True, "can_read_calendar": False, "can_write_calendar": False}}
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
    assert caps1["can_read_email"] is True
    assert caps1["can_send_email"] is True
    assert caps2 == caps1


@pytest.mark.asyncio
async def test_get_capabilities_graceful_degradation():
    """On connection error, all capabilities should be True (D-08)."""
    from app.core.capabilities import get_capabilities

    fake_redis = FakeRedis()

    with patch("app.core.capabilities.httpx.AsyncClient", side_effect=ConnectionError("down")):
        caps = await get_capabilities(fake_redis, "user-err")

    assert caps["can_read_email"] is True
    assert caps["can_send_email"] is True
    assert caps["can_read_calendar"] is True
    assert caps["can_write_calendar"] is True


# ── Invalidation ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalidate_capabilities():
    """invalidate_capabilities should delete all user cache keys."""
    from app.core.capabilities import invalidate_capabilities

    fake_redis = FakeRedis()
    fake_redis._store["capabilities:user123:all"] = json.dumps({"can_read_email": True})
    fake_redis._store["capabilities:user123:persona1"] = json.dumps({"can_send_email": True})
    fake_redis._store["capabilities:other_user:all"] = json.dumps({"can_read_email": False})

    await invalidate_capabilities(fake_redis, "user123")

    assert "capabilities:user123:all" not in fake_redis._store
    assert "capabilities:user123:persona1" not in fake_redis._store
    # Other user's key should be untouched
    assert "capabilities:other_user:all" in fake_redis._store


# ── Multi-connection aggregation ────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_aggregates_multiple_connections():
    """_fetch_capabilities should OR capabilities across multiple connections."""
    from app.core.capabilities import _fetch_capabilities

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "connections": [
            {"id": "c1", "capabilities": {"can_read_email": True, "can_send_email": False, "can_read_calendar": False, "can_write_calendar": False}},
            {"id": "c2", "capabilities": {"can_read_email": False, "can_send_email": True, "can_read_calendar": False, "can_write_calendar": False}},
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

    assert caps["can_read_email"] is True
    assert caps["can_send_email"] is True
    assert caps["can_read_calendar"] is False
    assert caps["can_write_calendar"] is False


# ── Error message formatting ────────────────────────────────────────────

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

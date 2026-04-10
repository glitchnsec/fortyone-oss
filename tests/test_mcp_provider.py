"""Integration tests for MCP provider (Phase 09-04).

Covers:
  - Tool name validation (D-14): valid names, length, special chars, reserved, empty, max list
  - MCPProvider: capability manifest, provider name
  - JSON-RPC message construction: payload structure, error handling, response size limit
  - MCP tool schema assembly: empty connections, correct formatting with namespacing
  - get_provider factory: MCP and Google routing

The connections service shares the ``app`` package name with the main API, so we
import it via importlib with a temporary sys.path swap to avoid collisions.
"""
import importlib
import json
import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helper: import from connections service safely ────────────────────────

_CONNECTIONS_ROOT = os.path.join(os.path.dirname(__file__), "..", "connections")

def _import_connections_module(dotted: str):
    """Import a module from the connections service without polluting sys.modules.

    Temporarily swaps sys.path AND clears cached ``app.*`` modules so the
    connections-side ``app`` package is loaded instead of the main API one.
    Restores everything afterward so later ``from app.core...`` imports work.
    """
    original_path = sys.path[:]
    saved_modules = {}
    # Save and remove any cached ``app`` modules
    for key in list(sys.modules.keys()):
        if key == "app" or key.startswith("app."):
            saved_modules[key] = sys.modules.pop(key)
    try:
        sys.path.insert(0, os.path.abspath(_CONNECTIONS_ROOT))
        mod = importlib.import_module(dotted)
        return mod
    finally:
        sys.path[:] = original_path
        # Remove connections-side ``app`` modules from cache
        for key in list(sys.modules.keys()):
            if key == "app" or key.startswith("app."):
                del sys.modules[key]
        # Restore original ``app`` modules
        sys.modules.update(saved_modules)


# Pre-load the modules we need
_mcp_mod = _import_connections_module("app.providers.mcp")
_google_mod = _import_connections_module("app.providers.google")

# Re-export symbols for convenience
validate_tool_name = _mcp_mod.validate_tool_name
validate_tool_list = _mcp_mod.validate_tool_list
MCPProvider = _mcp_mod.MCPProvider
MCPError = _mcp_mod.MCPError
mcp_call = _mcp_mod.mcp_call
MAX_TOOLS_PER_CONNECTION = _mcp_mod.MAX_TOOLS_PER_CONNECTION
MAX_RESPONSE_BYTES = _mcp_mod.MAX_RESPONSE_BYTES
RESERVED_TOOL_NAMES = _mcp_mod.RESERVED_TOOL_NAMES
get_provider = _google_mod.get_provider


# -- Tool name validation tests (D-14) ----------------------------------------

class TestToolNameValidation:
    def test_valid_tool_name(self):
        ok, reason = validate_tool_name("my_tool_123")
        assert ok is True
        assert reason == ""

    def test_valid_tool_name_uppercase(self):
        ok, _ = validate_tool_name("MyTool")
        assert ok is True

    def test_tool_name_too_long(self):
        long_name = "a" * 65
        ok, reason = validate_tool_name(long_name)
        assert ok is False
        assert "64" in reason

    def test_tool_name_special_chars(self):
        for name in ["my-tool", "my.tool", "my tool", "tool@home"]:
            ok, reason = validate_tool_name(name)
            assert ok is False, f"Expected '{name}' to be rejected"
            assert "invalid characters" in reason.lower()

    def test_tool_name_reserved(self):
        for reserved in ["web_search", "send_email", "list_events"]:
            assert reserved in RESERVED_TOOL_NAMES
            ok, reason = validate_tool_name(reserved)
            assert ok is False, f"Expected reserved name '{reserved}' to be rejected"
            assert "built-in" in reason.lower() or "conflicts" in reason.lower()

    def test_tool_name_empty(self):
        ok, reason = validate_tool_name("")
        assert ok is False
        assert "empty" in reason.lower()

    def test_validate_tool_list_max_tools(self):
        tools = [{"name": f"tool_{i}"} for i in range(25)]
        valid, errors = validate_tool_list(tools)
        assert len(valid) <= MAX_TOOLS_PER_CONNECTION
        assert any("maximum" in e.lower() or str(MAX_TOOLS_PER_CONNECTION) in e for e in errors)


# -- MCPProvider tests ---------------------------------------------------------

class TestMCPProvider:
    def test_mcp_provider_name(self):
        provider = MCPProvider()
        assert provider.name == "mcp"

    def test_mcp_provider_capability_manifest(self):
        provider = MCPProvider()
        manifest = provider.capability_manifest(["tool_a", "tool_b"])
        assert manifest.provider == "mcp"
        assert "tool_a" in manifest.tools
        assert "tool_b" in manifest.tools
        assert len(manifest.tools) == 2


# -- JSON-RPC message construction tests --------------------------------------

class TestMCPCall:
    @pytest.mark.asyncio
    async def test_mcp_call_builds_correct_payload(self):
        """Verify the JSON-RPC 2.0 request structure."""
        captured_payload = {}

        class FakeResponse:
            status_code = 200
            content = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'

            def raise_for_status(self):
                pass

            def json(self):
                return {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, headers=None):
                captured_payload.update(json or {})
                return FakeResponse()

        with patch.object(_mcp_mod.httpx, "AsyncClient", return_value=FakeClient()):
            result = await mcp_call("https://mcp.example.com", "tools/list", params={"cursor": None})

        assert captured_payload["jsonrpc"] == "2.0"
        assert captured_payload["method"] == "tools/list"
        assert "id" in captured_payload
        assert captured_payload["params"] == {"cursor": None}
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_mcp_call_handles_error_response(self):
        """JSON-RPC error responses should raise MCPError."""
        class FakeResponse:
            status_code = 200
            content = b'{"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"Method not found"}}'

            def raise_for_status(self):
                pass

            def json(self):
                return {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, headers=None):
                return FakeResponse()

        with patch.object(_mcp_mod.httpx, "AsyncClient", return_value=FakeClient()):
            with pytest.raises(MCPError) as exc_info:
                await mcp_call("https://mcp.example.com", "tools/list")

        assert exc_info.value.code == -32601
        assert "Method not found" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_mcp_call_response_size_limit(self):
        """Responses larger than 1MB should be rejected."""
        big_content = b"x" * (MAX_RESPONSE_BYTES + 1)

        class FakeResponse:
            status_code = 200
            content = big_content

            def raise_for_status(self):
                pass

            def json(self):
                return {"jsonrpc": "2.0", "id": 1, "result": {}}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, json=None, headers=None):
                return FakeResponse()

        with patch.object(_mcp_mod.httpx, "AsyncClient", return_value=FakeClient()):
            with pytest.raises(MCPError) as exc_info:
                await mcp_call("https://mcp.example.com", "tools/list")

        assert "too large" in str(exc_info.value).lower()


# -- MCP tool schema assembly tests -------------------------------------------

class TestMCPToolSchemas:
    @pytest.mark.asyncio
    async def test_get_mcp_tool_schemas_empty_connections(self):
        """No MCP connections returns empty list."""
        from app.core.tools import get_mcp_tool_schemas

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

        with patch("httpx.AsyncClient", return_value=FakeClient()):
            schemas = await get_mcp_tool_schemas("user1")

        assert schemas == []

    @pytest.mark.asyncio
    async def test_get_mcp_tool_schemas_formats_correctly(self):
        """Tool names should be prefixed with mcp_{conn_short}_ for namespacing."""
        from app.core.tools import get_mcp_tool_schemas

        conn_id = "abcdef12-3456-7890-abcd-ef1234567890"
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
                        {"name": "get_weather", "description": "Get weather for a location", "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}}},
                        {"name": "translate", "description": "Translate text", "inputSchema": {"type": "object", "properties": {}}},
                    ],
                }
            ]
        }

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, params=None):
                return mock_response

        with patch("httpx.AsyncClient", return_value=FakeClient()):
            schemas = await get_mcp_tool_schemas("user1")

        assert len(schemas) == 2
        short_id = conn_id[:8]
        names = [s["function"]["name"] for s in schemas]
        assert f"mcp_{short_id}_get_weather" in names
        assert f"mcp_{short_id}_translate" in names

        # Verify schema structure
        weather_schema = next(s for s in schemas if "get_weather" in s["function"]["name"])
        assert weather_schema["type"] == "function"
        assert weather_schema["function"]["description"] == "Get weather for a location"
        assert "city" in weather_schema["function"]["parameters"]["properties"]


# -- get_provider factory tests ------------------------------------------------

class TestGetProvider:
    def _run_in_connections_context(self, fn):
        """Execute fn with connections/app on sys.path and in sys.modules."""
        original_path = sys.path[:]
        saved_modules = {}
        for key in list(sys.modules.keys()):
            if key == "app" or key.startswith("app."):
                saved_modules[key] = sys.modules.pop(key)
        try:
            sys.path.insert(0, os.path.abspath(_CONNECTIONS_ROOT))
            return fn()
        finally:
            sys.path[:] = original_path
            for key in list(sys.modules.keys()):
                if key == "app" or key.startswith("app."):
                    del sys.modules[key]
            sys.modules.update(saved_modules)

    def test_get_provider_mcp(self):
        def _test():
            from app.providers.google import get_provider
            p = get_provider("mcp")
            assert p.name == "mcp"
        self._run_in_connections_context(_test)

    def test_get_provider_google(self):
        def _test():
            from app.providers.google import get_provider
            p = get_provider("google")
            assert p.name == "google"
        self._run_in_connections_context(_test)

    def test_get_provider_unknown_raises(self):
        def _test():
            from app.providers.google import get_provider
            with pytest.raises(ValueError, match="Unknown provider"):
                get_provider("unknown")
        self._run_in_connections_context(_test)

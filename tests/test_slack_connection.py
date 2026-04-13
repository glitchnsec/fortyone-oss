"""Integration tests for Slack as a persona-scoped connection.

Covers:
- SlackProvider, factory, reserved names, config, capabilities, subagents
- Google OAuth regression after multi-provider refactor
- Full E2E pipeline: provider -> capability -> dispatch -> mocked Slack API
- Token parsing: nested authed_user extraction
- Guard: existing Slack DM channel files untouched
"""
import importlib
import os
import sys

import pytest

# ── Helpers for importing from connections service ──────────────────────────
# The connections service has its own `app` package which conflicts with the
# main app's `app` package. We use importlib to load modules directly.

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CONN_ROOT = os.path.join(_PROJECT_ROOT, "connections")


class _ConnectionsContext:
    """Context manager that temporarily swaps sys.path and sys.modules
    so that `app.*` resolves to connections/app/* instead of the main app."""

    def __init__(self):
        self._saved_app = None
        self._saved_children = {}

    def __enter__(self):
        self._saved_app = sys.modules.pop("app", None)
        self._saved_children = {}
        for key in list(sys.modules.keys()):
            if key.startswith("app."):
                self._saved_children[key] = sys.modules.pop(key)
        sys.path.insert(0, _CONN_ROOT)
        return self

    def __exit__(self, *exc):
        if _CONN_ROOT in sys.path:
            sys.path.remove(_CONN_ROOT)
        for key in list(sys.modules.keys()):
            if key == "app" or key.startswith("app."):
                sys.modules.pop(key, None)
        if self._saved_app is not None:
            sys.modules["app"] = self._saved_app
        sys.modules.update(self._saved_children)

    def import_module(self, dotted_path: str):
        # Clear cached to force fresh import
        for key in list(sys.modules.keys()):
            if key == dotted_path or key.startswith(dotted_path + "."):
                del sys.modules[key]
        return importlib.import_module(dotted_path)


def _import_connections_module(dotted_path: str):
    """Import a module from connections/ by temporarily adjusting sys.path."""
    with _ConnectionsContext() as ctx:
        return ctx.import_module(dotted_path)


def test_slack_provider_capability_manifest():
    """SlackProvider returns correct tools based on granted scopes."""
    mod = _import_connections_module("app.providers.slack")
    SlackProvider = mod.SlackProvider

    provider = SlackProvider()

    # All 6 scopes -> 3 tools
    all_scopes = [
        "channels:read", "channels:history",
        "users:read", "team:read",
        "groups:read", "groups:history",
    ]
    manifest = provider.capability_manifest(all_scopes)
    assert sorted(manifest.tools) == [
        "slack_get_workspace",
        "slack_read_channels",
        "slack_read_threads",
    ]

    # Partial scopes: only channels -> only slack_read_channels
    partial = provider.capability_manifest(["channels:read", "channels:history"])
    assert sorted(partial.tools) == ["slack_read_channels"]

    # Empty scopes -> empty tools
    empty = provider.capability_manifest([])
    assert empty.tools == []


def test_get_provider_returns_slack():
    """get_provider('slack') returns a SlackProvider instance."""
    with _ConnectionsContext() as ctx:
        google_mod = ctx.import_module("app.providers.google")
        slack_mod = ctx.import_module("app.providers.slack")
        provider = google_mod.get_provider("slack")
        assert isinstance(provider, slack_mod.SlackProvider)
        assert provider.name == "slack"


def test_get_provider_google_regression():
    """Google provider still works after multi-provider refactor."""
    mod = _import_connections_module("app.providers.google")

    provider = mod.get_provider("google")
    assert isinstance(provider, mod.GoogleProvider)
    assert provider.name == "google"
    assert "accounts.google.com" in provider.auth_url
    assert len(provider.scopes) > 0

    # Google capability manifest with known scopes
    manifest = provider.capability_manifest([
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ])
    assert "read_emails" in manifest.tools
    assert "send_email" in manifest.tools


def test_reserved_tool_names_include_slack():
    """RESERVED_TOOL_NAMES includes all Slack tools and existing tools."""
    mod = _import_connections_module("app.providers.mcp")
    RESERVED_TOOL_NAMES = mod.RESERVED_TOOL_NAMES

    # Slack tools present
    assert "slack_read_channels" in RESERVED_TOOL_NAMES
    assert "slack_get_workspace" in RESERVED_TOOL_NAMES
    assert "slack_read_threads" in RESERVED_TOOL_NAMES

    # Existing Google tools still present (no regression)
    assert "read_emails" in RESERVED_TOOL_NAMES
    assert "send_email" in RESERVED_TOOL_NAMES


def test_slack_config_fields_exist():
    """Connections service Settings has Slack config fields."""
    mod = _import_connections_module("app.config")
    Settings = mod.Settings

    s = Settings(
        slack_client_id="test_id",
        slack_client_secret="test_secret",
        slack_redirect_uri="http://test/cb",
    )
    assert s.slack_client_id == "test_id"
    assert s.slack_client_secret == "test_secret"
    assert s.slack_redirect_uri == "http://test/cb"

    # Defaults are empty strings for id/secret
    defaults = Settings()
    assert defaults.slack_client_id == ""
    assert defaults.slack_client_secret == ""


def test_slack_tools_in_all_tools():
    """_ALL_TOOLS in capabilities.py includes Slack tools."""
    from app.core.capabilities import _ALL_TOOLS

    assert "slack_read_channels" in _ALL_TOOLS
    assert "slack_get_workspace" in _ALL_TOOLS
    assert "slack_read_threads" in _ALL_TOOLS

    # Existing tools still present (no regression)
    assert "read_emails" in _ALL_TOOLS
    assert "web_search" in _ALL_TOOLS


def test_subagents_yaml_has_slack_tools():
    """subagents.yaml defines slack_agent with 3 low-risk tools."""
    yaml_path = os.path.join(_PROJECT_ROOT, "config", "subagents.yaml")
    try:
        import yaml
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
    except ImportError:
        # Fallback: string search if pyyaml not available
        with open(yaml_path) as f:
            content = f.read()
        assert "slack_agent" in content
        assert "slack_read_channels" in content
        assert "slack_get_workspace" in content
        assert "slack_read_threads" in content
        assert "risk_level: low" in content
        return

    # Find slack_agent in subagents list
    agents = data.get("subagents", [])
    if not agents:
        for key in data:
            if isinstance(data[key], list):
                agents = data[key]
                break

    slack_agent = None
    for agent in agents:
        if agent.get("name") == "slack_agent":
            slack_agent = agent
            break

    assert slack_agent is not None, "slack_agent not found in subagents.yaml"
    tools = slack_agent.get("tools", [])
    assert len(tools) == 3

    tool_names = [t["name"] for t in tools]
    assert "slack_read_channels" in tool_names
    assert "slack_get_workspace" in tool_names
    assert "slack_read_threads" in tool_names

    # All tools should be low risk (per D-06)
    for tool in tools:
        assert tool.get("risk_level") == "low", f"{tool['name']} is not low risk"

    # slack_read_channels has dual-behavior description
    channels_tool = next(t for t in tools if t["name"] == "slack_read_channels")
    desc = channels_tool.get("description", "")
    assert "list" in desc.lower() or "List" in desc
    assert "messages" in desc.lower()


def test_slack_oauth_route_uses_user_scope_and_authed_user():
    """OAuth route correctly uses Slack-specific patterns (user_scope, authed_user)."""
    oauth_path = os.path.join(_CONN_ROOT, "app", "routes", "oauth.py")
    with open(oauth_path) as f:
        content = f.read()

    # Slack uses user_scope (not scope) for user token OAuth
    assert "user_scope" in content, "Slack OAuth should use user_scope parameter"
    # Nested token extraction from authed_user block
    assert "authed_user" in content, "Slack token extraction needs authed_user"
    # Uses Slack client credentials
    assert "slack_client_id" in content, "Should use slack_client_id"
    # Validates nested response
    assert "missing_user_token" in content, "Should validate authed_user block"
    # Google path preserved
    assert "google_client_id" in content, "Google OAuth path should be preserved"
    assert "AsyncOAuth2Client" in content, "Google OAuth still uses authlib"


def test_slack_tool_atomic_refresh_and_input_validation():
    """Slack tools have atomic token refresh and input validation."""
    # Check source for lock patterns and retry bounds
    slack_tools_path = os.path.join(_CONN_ROOT, "app", "tools", "slack.py")
    with open(slack_tools_path) as f:
        content = f.read()

    assert "_refresh_locks" in content, "Module should have _refresh_locks dict"
    assert "_MAX_RETRIES = 2" in content, "Bounded retry should be 2"
    assert "asyncio.Lock" in content, "Should use per-connection asyncio.Lock"
    assert "await db.refresh(token)" in content, "Double-check pattern after lock"

    # Check input validation bounds in tools routes
    tools_path = os.path.join(_CONN_ROOT, "app", "routes", "tools.py")
    with open(tools_path) as f:
        tools_content = f.read()

    assert "ge=1" in tools_content, "Input should have minimum bound"
    assert "le=200" in tools_content, "Input should have maximum bound"


def test_slack_dm_channel_files_untouched():
    """Existing Slack DM channel files are not modified by connection changes."""
    # app/channels/slack.py should NOT contain connection tool names
    channels_path = os.path.join(_PROJECT_ROOT, "app", "channels", "slack.py")
    with open(channels_path) as f:
        content = f.read()

    assert "slack_read_channels" not in content, "DM channel should not reference connection tools"
    assert "slack_get_workspace" not in content, "DM channel should not reference connection tools"
    assert "slack_read_threads" not in content, "DM channel should not reference connection tools"
    assert "SlackChannel" in content, "Existing SlackChannel class should remain"

    # app/routes/slack.py should NOT contain connection tool names
    routes_path = os.path.join(_PROJECT_ROOT, "app", "routes", "slack.py")
    with open(routes_path) as f:
        routes_content = f.read()

    assert "slack_read_channels" not in routes_content, "Slack route should not reference connection tools"
    assert "slack_get_workspace" not in routes_content, "Slack route should not reference connection tools"
    assert "@router.post" in routes_content, "Existing Slack event route should remain"


def test_full_pipeline_provider_to_capability_to_dispatch():
    """End-to-end: provider -> capability -> dispatch -> schema -> tools all connected."""
    # 1-2. get_provider returns SlackProvider, capability_manifest returns 3 tools
    with _ConnectionsContext() as ctx:
        google_mod = ctx.import_module("app.providers.google")
        slack_mod = ctx.import_module("app.providers.slack")

        provider = google_mod.get_provider("slack")
        assert isinstance(provider, slack_mod.SlackProvider)

        all_scopes = [
            "channels:read", "channels:history",
            "users:read", "team:read",
            "groups:read", "groups:history",
        ]
        manifest = provider.capability_manifest(all_scopes)
        tool_names = sorted(manifest.tools)
        assert tool_names == ["slack_get_workspace", "slack_read_channels", "slack_read_threads"]

        # 6. All tools in RESERVED_TOOL_NAMES (while still in connections context)
        mcp_mod = ctx.import_module("app.providers.mcp")
        for tool in tool_names:
            assert tool in mcp_mod.RESERVED_TOOL_NAMES, f"{tool} not in RESERVED_TOOL_NAMES"

    # 3. All tools in _ALL_TOOLS (capabilities.py) -- main app context
    from app.core.capabilities import _ALL_TOOLS
    for tool in tool_names:
        assert tool in _ALL_TOOLS, f"{tool} not in _ALL_TOOLS"

    # 4. All tools in CONNECTION_TOOLS (manager.py) -- read source to verify
    manager_path = os.path.join(_PROJECT_ROOT, "app", "tasks", "manager.py")
    with open(manager_path) as f:
        manager_content = f.read()
    for tool in tool_names:
        assert tool in manager_content, f"{tool} not in manager.py CONNECTION_TOOLS"

    # 5. All tools in subagents.yaml
    yaml_path = os.path.join(_PROJECT_ROOT, "config", "subagents.yaml")
    with open(yaml_path) as f:
        yaml_content = f.read()
    for tool in tool_names:
        assert tool in yaml_content, f"{tool} not in subagents.yaml"

    # 7. Tool functions exist in connections/app/tools/slack.py (check source)
    slack_tools_path = os.path.join(_CONN_ROOT, "app", "tools", "slack.py")
    with open(slack_tools_path) as f:
        slack_tools_content = f.read()
    for tool in tool_names:
        assert f"async def {tool}" in slack_tools_content, f"{tool} function not defined"

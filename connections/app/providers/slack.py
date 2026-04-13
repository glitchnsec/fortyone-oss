"""SlackProvider: read-only Slack workspace tools via user OAuth tokens."""
from typing import List
from app.providers.base import AbstractProvider, CapabilityManifest

# User token OAuth scopes (per D-05, D-07)
_CHANNELS_READ = "channels:read"
_CHANNELS_HISTORY = "channels:history"
_USERS_READ = "users:read"
_TEAM_READ = "team:read"
_GROUPS_READ = "groups:read"
_GROUPS_HISTORY = "groups:history"

# Maps scopes to tool names (per D-04, D-11)
_SCOPE_TO_TOOL = {
    _CHANNELS_READ: "slack_read_channels",
    _CHANNELS_HISTORY: "slack_read_channels",
    _USERS_READ: "slack_get_workspace",
    _TEAM_READ: "slack_get_workspace",
    _GROUPS_READ: "slack_read_channels",
    _GROUPS_HISTORY: "slack_read_threads",
}


class SlackProvider(AbstractProvider):
    name = "slack"
    auth_url = "https://slack.com/oauth/v2/authorize"
    token_url = "https://slack.com/api/oauth.v2.access"
    scopes: List[str] = [
        _CHANNELS_READ, _CHANNELS_HISTORY,
        _USERS_READ, _TEAM_READ,
        _GROUPS_READ, _GROUPS_HISTORY,
    ]

    def capability_manifest(self, granted_scopes: List[str]) -> CapabilityManifest:
        tools = list({tool for scope, tool in _SCOPE_TO_TOOL.items() if scope in granted_scopes})
        return CapabilityManifest(provider="slack", tools=sorted(tools))

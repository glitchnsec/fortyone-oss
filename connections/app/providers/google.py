"""GoogleProvider: Gmail + Calendar via single OAuth consent screen."""
from typing import List
from app.providers.base import AbstractProvider, CapabilityManifest

_GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
_GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
_CAL_EVENTS = "https://www.googleapis.com/auth/calendar.events"
_CAL_READONLY = "https://www.googleapis.com/auth/calendar.readonly"

# Maps OAuth scopes to generic tool name strings (D-02)
_SCOPE_TO_TOOL = {
    _GMAIL_READONLY: "read_emails",
    _GMAIL_SEND: "send_email",
    _CAL_READONLY: "list_events",
    _CAL_EVENTS: "create_event",
}


class GoogleProvider(AbstractProvider):
    name = "google"
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    scopes: List[str] = [_GMAIL_READONLY, _GMAIL_SEND, _CAL_EVENTS, _CAL_READONLY]

    def capability_manifest(self, granted_scopes: List[str]) -> CapabilityManifest:
        tools = [tool for scope, tool in _SCOPE_TO_TOOL.items() if scope in granted_scopes]
        return CapabilityManifest(provider="google", tools=tools)


def get_provider(name: str) -> AbstractProvider:
    if name == "google":
        return GoogleProvider()
    if name == "slack":
        from app.providers.slack import SlackProvider
        return SlackProvider()
    if name == "mcp":
        from app.providers.mcp import MCPProvider
        return MCPProvider()
    raise ValueError(f"Unknown provider: {name}")

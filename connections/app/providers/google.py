"""GoogleProvider: Gmail + Calendar via single OAuth consent screen."""
from typing import List
from app.providers.base import AbstractProvider, CapabilityManifest

_GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
_GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
_CAL_EVENTS = "https://www.googleapis.com/auth/calendar.events"
_CAL_READONLY = "https://www.googleapis.com/auth/calendar.readonly"


class GoogleProvider(AbstractProvider):
    name = "google"
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    scopes: List[str] = [_GMAIL_READONLY, _GMAIL_SEND, _CAL_EVENTS, _CAL_READONLY]

    def capability_manifest(self, granted_scopes: List[str]) -> CapabilityManifest:
        return CapabilityManifest(
            provider="google",
            can_read_email=_GMAIL_READONLY in granted_scopes,
            can_send_email=_GMAIL_SEND in granted_scopes,
            can_read_calendar=_CAL_READONLY in granted_scopes,
            can_write_calendar=_CAL_EVENTS in granted_scopes,
        )


def get_provider(name: str) -> AbstractProvider:
    if name == "google":
        return GoogleProvider()
    raise ValueError(f"Unknown provider: {name}")

"""
SMS channel — backed by Twilio.

Set MOCK_SMS=true (or leave TWILIO_ACCOUNT_SID blank) and every send()
just logs to stdout — no credentials needed for local development.

IMPORTANT: The Twilio REST SDK is synchronous (blocking HTTP).  The send()
method offloads the call to a thread via asyncio.to_thread() so it does
NOT block the event loop.  This is critical during proactive briefing
windows when ResponseListener delivers many messages in rapid succession —
without thread offloading, each blocking Twilio call starves inbound
BackgroundTasks (see debug session: delayed-onboarding-sms-during-briefings).
"""
import asyncio
import logging

from app.channels.base import Channel
from app.config import get_settings

logger = logging.getLogger(__name__)

_MAX_SMS_CHARS = 1600
# Reserve room for multipart prefix like "(12/12) ".
_SMS_PREFIX_BUDGET = 12


def _split_sms_parts(body: str, max_chars: int = _MAX_SMS_CHARS) -> list[str]:
    """
    Split an outbound body into Twilio-safe SMS chunks.

    Twilio rejects bodies >1600 chars (error 21617). For long content we split
    on word boundaries where possible and add "(i/n)" prefixes.
    """
    text = (body or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunk_limit = max_chars - _SMS_PREFIX_BUDGET
    if chunk_limit <= 0:
        chunk_limit = max_chars

    parts: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_limit, n)
        if end < n:
            split_at = text.rfind(" ", start, end)
            if split_at <= start:
                split_at = end
        else:
            split_at = end

        part = text[start:split_at].strip()
        if not part:
            split_at = end
            part = text[start:split_at].strip()
        if part:
            parts.append(part)
        start = split_at
        while start < n and text[start].isspace():
            start += 1

    if len(parts) <= 1:
        return parts

    total = len(parts)
    with_prefix = [f"({idx}/{total}) {part}" for idx, part in enumerate(parts, start=1)]
    return [p[:max_chars] for p in with_prefix]


class SMSChannel(Channel):
    name = "sms"
    error_reply = "Something went wrong on my end — try again in a moment."

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client = None

        if not self.settings.is_mock_sms:
            from twilio.rest import Client
            self._client = Client(
                self.settings.twilio_account_sid,
                self.settings.twilio_auth_token,
            )

    def _send_sync(self, to: str, body: str):
        """Synchronous Twilio SDK call — run in a thread, never on the event loop."""
        return self._client.messages.create(
            body=body,
            from_=self.settings.twilio_phone_number,
            to=to,
        )

    async def send(self, to: str, body: str) -> bool:
        parts = _split_sms_parts(body)
        if not parts:
            logger.warning("SMS send skipped empty body to=%s", to)
            return False

        if self.settings.is_mock_sms or not self._client:
            for part in parts:
                logger.info("\U0001f4f1 [MOCK SMS \u2192 %s]\n%s", to, part)
            return True

        try:
            # Offload blocking Twilio HTTP call to a thread so the event loop
            # stays free for other async tasks (inbound webhooks, pub/sub, etc.)
            for idx, part in enumerate(parts, start=1):
                msg = await asyncio.to_thread(self._send_sync, to, part)
                logger.info(
                    "SMS sent  sid=%s  to=%s  part=%d/%d  body=%r",
                    msg.sid, to, idx, len(parts), part[:120],
                )
            return True
        except Exception as exc:
            logger.error("SMS failed to=%s: %s", to, exc)
            return False

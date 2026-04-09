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
        if self.settings.is_mock_sms or not self._client:
            logger.info("\U0001f4f1 [MOCK SMS \u2192 %s]\n%s", to, body)
            return True

        try:
            # Offload blocking Twilio HTTP call to a thread so the event loop
            # stays free for other async tasks (inbound webhooks, pub/sub, etc.)
            msg = await asyncio.to_thread(self._send_sync, to, body)
            logger.info(
                "SMS sent  sid=%s  to=%s  body=%r",
                msg.sid, to, body[:120],
            )
            return True
        except Exception as exc:
            logger.error("SMS failed to=%s: %s", to, exc)
            return False

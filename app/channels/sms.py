"""
SMS channel — backed by Twilio.

Set MOCK_SMS=true (or leave TWILIO_ACCOUNT_SID blank) and every send()
just logs to stdout — no credentials needed for local development.
"""
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

    async def send(self, to: str, body: str) -> bool:
        if self.settings.is_mock_sms or not self._client:
            logger.info("📱 [MOCK SMS → %s]\n%s", to, body)
            return True

        try:
            msg = self._client.messages.create(
                body=body,
                from_=self.settings.twilio_phone_number,
                to=to,
            )
            logger.info(
                "SMS sent  sid=%s  to=%s  body=%r",
                msg.sid, to, body[:120],
            )
            return True
        except Exception as exc:
            logger.error("SMS failed to=%s: %s", to, exc)
            return False

"""
Slack channel.

To activate:
  1. Create a Slack App at https://api.slack.com/apps
  2. Enable "Event Subscriptions" — set the Request URL to:
       https://<your-host>/slack/events
  3. Subscribe to `message.im` (direct messages to the bot)
  4. Under "OAuth & Permissions" add the bot token scope: chat:write
  5. Install the app to your workspace and copy the Bot User OAuth Token
  6. Set in .env:
       SLACK_BOT_TOKEN=xoxb-...
       SLACK_SIGNING_SECRET=...

The inbound route lives in app/routes/slack.py.
User addresses on Slack are the Slack User ID (e.g. "U01ABC123").
"""
import logging

from app.channels.base import Channel

logger = logging.getLogger(__name__)


class SlackChannel(Channel):
    name = "slack"
    error_reply = "Something went wrong on my end — try again in a moment."

    def __init__(self) -> None:
        from app.config import get_settings
        self.settings = get_settings()
        self._client = None

        slack_token = getattr(self.settings, "slack_bot_token", "")
        if slack_token and len(slack_token) > 10:
            try:
                from slack_sdk.web.async_client import AsyncWebClient
                self._client = AsyncWebClient(token=slack_token)
                logger.info("SlackChannel initialized with Slack SDK")
            except ImportError:
                logger.warning(
                    "slack_sdk not installed — run: pip install slack-sdk. "
                    "Slack messages will be mocked."
                )

    async def send(self, to: str, body: str) -> bool:
        """
        Send a DM to the Slack user identified by `to` (Slack User ID).

        The SDK opens a DM channel via conversations.open, then posts there.
        Falls back to logging when the SDK is unavailable.
        """
        if not self._client:
            logger.info("💬 [MOCK SLACK → %s]\n%s", to, body)
            return True

        try:
            # Open / retrieve existing DM channel for this user
            channel_resp = await self._client.conversations_open(users=to)
            channel_id: str = channel_resp["channel"]["id"]

            await self._client.chat_postMessage(
                channel=channel_id,
                text=body,
            )
            logger.info("Slack DM sent  to=%s  body=%r", to, body[:120])
            return True
        except Exception as exc:
            logger.error("Slack DM failed to=%s: %s", to, exc)
            return False

"""
Slack Events API webhook.

POST /slack/events — Slack calls this for every subscribed event.

Setup checklist (see app/channels/slack.py for full instructions):
  1. Create a Slack App → enable Event Subscriptions
  2. Set Request URL: https://<your-host>/slack/events
  3. Subscribe to: message.im  (direct messages to the bot)
  4. Add bot token scope: chat:write
  5. Set SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET in .env

Slack requires a 200 response within 3 seconds or it retries — so real
work runs in a BackgroundTask, same pattern as the SMS route.

Request signature verification uses SLACK_SIGNING_SECRET to prevent
spoofed webhooks.  Requests without a valid signature return 403.
"""
import hashlib
import hmac
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.channels.slack import SlackChannel
from app.database import AsyncSessionLocal
from app.memory.store import MemoryStore
from app.queue.client import queue_client

logger = logging.getLogger(__name__)
router = APIRouter()

_channel = SlackChannel()


# ─── Signature verification ───────────────────────────────────────────────────

def _verify_slack_signature(
    body: bytes,
    timestamp: str,
    signature: str,
    signing_secret: str,
) -> bool:
    """
    Verify the request came from Slack.
    https://api.slack.com/authentication/verifying-requests-from-slack
    """
    # Reject stale requests (> 5 minutes old)
    try:
        if abs(time.time() - float(timestamp)) > 300:
            return False
    except (ValueError, TypeError):
        return False

    base = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected = "v0=" + hmac.new(
        signing_secret.encode(),
        base.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ─── Background task ──────────────────────────────────────────────────────────

async def _process_inbound(address: str, body: str) -> None:
    from app.core.pipeline import MessagePipeline

    async with AsyncSessionLocal() as db:
        try:
            store = MemoryStore(db)
            pipeline = MessagePipeline(channel=_channel, queue=queue_client, store=store)
            await pipeline.handle(address=address, body=body)
        except Exception as exc:
            logger.error("Pipeline error  channel=slack  address=%s: %s", address, exc, exc_info=True)
            try:
                await _channel.send(address, _channel.error_reply)
            except Exception:
                pass


# ─── Route ────────────────────────────────────────────────────────────────────

@router.post("/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_request_timestamp: str = Header(default=""),
    x_slack_signature: str = Header(default=""),
) -> JSONResponse:
    """
    Handles two Slack payload types:
      • url_verification — one-time challenge during app setup
      • event_callback   — real inbound messages
    """
    from app.config import get_settings
    settings = get_settings()

    raw_body = await request.body()
    payload: dict = await request.json()

    # ── Signature check (mandatory — D-09) ───────────────────────────────────
    signing_secret = settings.slack_signing_secret
    if not signing_secret:
        # Router is disabled in app/main.py when secret is not configured.
        # If we somehow receive a request here without a secret, reject it.
        raise HTTPException(status_code=403, detail="Slack integration not configured")
    if not _verify_slack_signature(
        raw_body,
        x_slack_request_timestamp,
        x_slack_signature,
        signing_secret,
    ):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    # ── URL verification (one-time setup handshake) ───────────────────────────
    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge")})

    # ── Event callback ────────────────────────────────────────────────────────
    if payload.get("type") == "event_callback":
        event: dict = payload.get("event", {})

        # Only handle direct messages (im subtype); ignore bot messages
        if event.get("type") == "message" and not event.get("bot_id"):
            user_id: str = event.get("user", "")
            text: str    = (event.get("text") or "").strip()

            if user_id and text:
                logger.info(
                    "INBOUND  channel=slack  from=%s  body=%r",
                    user_id, text[:80],
                )
                background_tasks.add_task(_process_inbound, user_id, text)

    # Slack expects a 200 quickly — return before the background task runs
    return JSONResponse({"ok": True})

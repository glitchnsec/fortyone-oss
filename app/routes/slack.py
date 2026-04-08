"""
Slack Events API webhook.

POST /slack/events -- Slack calls this for every subscribed event.

Setup checklist (see app/channels/slack.py for full instructions):
  1. Create a Slack App -> enable Event Subscriptions
  2. Set Request URL: https://<your-host>/slack/events
  3. Subscribe to: message.im  (direct messages to the bot)
  4. Add bot token scope: chat:write
  5. Set SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET in .env

Slack requires a 200 response within 3 seconds or it retries -- so real
work runs in a BackgroundTask, same pattern as the SMS route.

Request signature verification uses SLACK_SIGNING_SECRET to prevent
spoofed webhooks.  Requests without a valid signature return 403.
"""
import hashlib
import hmac
import logging
import re
import time
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.channels.slack import SlackChannel
from app.database import AsyncSessionLocal
from app.memory.store import MemoryStore
from app.queue.client import queue_client

logger = logging.getLogger(__name__)
router = APIRouter()

_channel = SlackChannel()

# Module-level dict tracking users mid-onboarding (slack_user_id -> {"ts": float})
# Simple TTL eviction: entries older than 600s are ignored.
_pending_onboarding: dict[str, dict] = {}
_ONBOARDING_TTL_S = 600  # 10 minutes

_LINK_CODE_PATTERN = re.compile(r"^[A-Z0-9]{6}$")


# -- Signature verification ---------------------------------------------------

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


# -- Background task -----------------------------------------------------------

async def _process_inbound(slack_user_id: str, body: str) -> None:
    """Two-path flow: known user -> pipeline, unknown -> onboarding."""
    from app.core.pipeline import MessagePipeline

    async with AsyncSessionLocal() as db:
        try:
            store = MemoryStore(db)

            # Path A: Known user -- already linked
            user = await store.get_or_create_user_for_slack(slack_user_id)
            if user:
                pipeline = MessagePipeline(channel=_channel, queue=queue_client, store=store)
                await pipeline.handle_with_user(user=user, body=body)
                return

            # Path B: Unknown user -- onboarding
            await _handle_slack_onboarding(slack_user_id, body, store)
        except Exception as exc:
            logger.error("Pipeline error  channel=slack  address=%s: %s", slack_user_id, exc, exc_info=True)
            try:
                await _channel.send(slack_user_id, _channel.error_reply)
            except Exception:
                pass


async def _handle_slack_onboarding(slack_user_id: str, body: str, store: MemoryStore) -> None:
    """Handle first-time Slack users: auto-link by email, or guide to register/link."""
    from app.config import get_settings
    settings = get_settings()

    # Check if this is a linking code attempt
    stripped = body.strip().upper()
    if _LINK_CODE_PATTERN.match(stripped):
        await _try_linking_code(slack_user_id, stripped, store)
        return

    # Fetch Slack profile for email
    email = None
    phone = None
    if _channel._client:
        try:
            info = await _channel._client.users_info(user=slack_user_id)
            profile = info.get("user", {}).get("profile", {})
            email = profile.get("email")
            phone = profile.get("phone")
        except Exception as exc:
            logger.warning("Slack users.info failed for %s: %s", slack_user_id, exc)

    # Auto-link by email match (D-01)
    if email:
        existing = await store.lookup_by_email(email)
        if existing:
            await store.link_slack_user(existing.id, slack_user_id)
            await _channel.send(
                slack_user_id,
                "I've linked your Slack to your existing account. How can I help?",
            )
            logger.info("SLACK_AUTOLINK  slack=%s  user=%s  email=%s", slack_user_id, existing.id[:8], email)
            # Process original message through pipeline
            from app.core.pipeline import MessagePipeline
            pipeline = MessagePipeline(channel=_channel, queue=queue_client, store=store)
            await pipeline.handle_with_user(user=existing, body=body)
            return

    # No match -- send onboarding message (D-02, D-03)
    _pending_onboarding[slack_user_id] = {"ts": time.time()}

    reg_url = f"{settings.dashboard_url}/auth/register"
    if email:
        reg_url += f"?email={quote(email)}&from=slack&slack_id={quote(slack_user_id)}"
    else:
        reg_url += f"?from=slack&slack_id={quote(slack_user_id)}"

    onboarding_msg = (
        "Hi there! I don't recognize your account yet.\n\n"
        "*New user?*\n"
        f"Create your account here: {reg_url}\n\n"
        "*Already have an account?*\n"
        "Go to your dashboard account settings and generate a linking code, "
        "then paste it here."
    )
    await _channel.send(slack_user_id, onboarding_msg)
    logger.info("SLACK_ONBOARDING  slack=%s  email=%s", slack_user_id, email or "none")


async def _try_linking_code(slack_user_id: str, code: str, store: MemoryStore) -> None:
    """Attempt to link via a 6-char linking code stored in Redis."""
    import redis.asyncio as aioredis
    from app.config import get_settings
    settings = get_settings()

    try:
        r = aioredis.from_url(settings.redis_url)
        try:
            user_id = await r.get(f"slack_link:{code}")
            if user_id:
                user_id_str = user_id.decode() if isinstance(user_id, bytes) else user_id
                await store.link_slack_user(user_id_str, slack_user_id)
                await r.delete(f"slack_link:{code}")
                await _channel.send(
                    slack_user_id,
                    "Your account has been linked! How can I help you today?",
                )
                logger.info("SLACK_LINK_CODE  slack=%s  user=%s", slack_user_id, user_id_str[:8])

                # Clean up onboarding state
                _pending_onboarding.pop(slack_user_id, None)
            else:
                await _channel.send(
                    slack_user_id,
                    "That code is invalid or expired. Please generate a new one from your dashboard.",
                )
        finally:
            await r.aclose()
    except Exception as exc:
        logger.error("Linking code check failed: %s", exc)
        await _channel.send(
            slack_user_id,
            "Something went wrong checking that code. Please try again.",
        )


# -- Route ---------------------------------------------------------------------

@router.post("/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_request_timestamp: str = Header(default=""),
    x_slack_signature: str = Header(default=""),
) -> JSONResponse:
    """
    Handles two Slack payload types:
      - url_verification -- one-time challenge during app setup
      - event_callback   -- real inbound messages
    """
    from app.config import get_settings
    settings = get_settings()

    raw_body = await request.body()
    payload: dict = await request.json()

    # -- Signature check (mandatory -- D-09) -----------------------------------
    signing_secret = settings.slack_signing_secret
    if not signing_secret:
        raise HTTPException(status_code=403, detail="Slack integration not configured")
    if not _verify_slack_signature(
        raw_body,
        x_slack_request_timestamp,
        x_slack_signature,
        signing_secret,
    ):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    # -- URL verification (one-time setup handshake) ---------------------------
    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge")})

    # -- Event callback --------------------------------------------------------
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

    # Slack expects a 200 quickly -- return before the background task runs
    return JSONResponse({"ok": True})

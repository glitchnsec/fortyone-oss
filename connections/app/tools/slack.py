"""Slack tools: read channels, workspace info, and thread replies via Slack Web API.

Provides three read-only tools callable by the main API's manager dispatch:
- slack_read_channels: List channels or read messages from a specific channel
- slack_get_workspace: Get workspace metadata and member list
- slack_read_threads: Read thread replies

Token refresh uses a per-connection asyncio.Lock to handle Slack's single-use
refresh tokens safely under concurrent requests. Rate-limited responses are
retried with bounded retries (max 2).
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.crypto import decrypt, encrypt
from app.models import Connection, OAuthToken

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api"

# ── Atomic token refresh ────────────────────────────────────────────────
# Slack refresh tokens are single-use. A module-level lock dict prevents
# concurrent tool calls from reusing a rotated token.
_refresh_locks: dict[str, asyncio.Lock] = {}

# ── Bounded retry for rate limits ───────────────────────────────────────
_MAX_RETRIES = 2


async def _get_fresh_token(conn: Connection, token: OAuthToken, db: AsyncSession) -> str:
    """Return a valid access token. Refreshes if expired. Sets needs_reauth if refresh fails.

    Uses a per-connection asyncio.Lock to ensure atomic refresh for Slack's
    single-use refresh tokens.
    """
    now = datetime.now(timezone.utc)
    expires_at = token.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and expires_at > now:
        return decrypt(token.access_token_enc)

    # Atomic refresh: lock per connection to prevent concurrent rotation
    lock = _refresh_locks.setdefault(conn.id, asyncio.Lock())
    async with lock:
        # Re-check after acquiring lock (another coroutine may have refreshed)
        await db.refresh(token)
        expires_at = token.expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at and expires_at > now:
            return decrypt(token.access_token_enc)

        # If no refresh token (rotation not enabled), return current token
        if not token.refresh_token_enc:
            return decrypt(token.access_token_enc)

        try:
            settings = get_settings()
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{SLACK_API}/oauth.v2.access", data={
                    "client_id": settings.slack_client_id,
                    "client_secret": settings.slack_client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": decrypt(token.refresh_token_enc),
                })
                data = resp.json()
                if not data.get("ok"):
                    raise Exception(f"Slack refresh failed: {data.get('error')}")

            new_access = data["access_token"]
            new_refresh = data.get("refresh_token")
            exp = now + timedelta(seconds=data.get("expires_in", 43200))
            token.access_token_enc = encrypt(new_access)
            # MUST store new refresh token -- old one is invalidated
            if new_refresh:
                token.refresh_token_enc = encrypt(new_refresh)
            token.expires_at = exp
            await db.commit()
            return new_access
        except Exception as e:
            logger.error("Slack token refresh failed conn=%s error=%s", conn.id, e)
            conn.status = "needs_reauth"
            await db.commit()
            raise HTTPException(
                401,
                "Your Slack connection needs reauthorization. Reconnect to restore access.",
            )


async def _slack_api_get(
    client: httpx.AsyncClient, url: str, params: dict, access_token: str,
) -> dict:
    """GET a Slack API endpoint with bounded retry on 429 rate limits."""
    for attempt in range(_MAX_RETRIES + 1):
        resp = await client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code == 429:
            if attempt < _MAX_RETRIES:
                retry_after = int(resp.headers.get("Retry-After", "2"))
                retry_after = min(retry_after, 10)  # cap wait to 10s
                await asyncio.sleep(retry_after)
                continue
            return {
                "ok": False,
                "error": "rate_limited",
                "retry_after": resp.headers.get("Retry-After", "60"),
            }
        data = resp.json()
        if not data.get("ok"):
            raise HTTPException(502, f"Slack API error: {data.get('error', 'unknown')}")
        return data
    return {"ok": False, "error": "max_retries_exceeded"}


# ── Connection lookup ───────────────────────────────────────────────────

async def _get_slack_connection(
    user_id: str, db: AsyncSession, persona_id: str | None = None,
) -> tuple[Connection, OAuthToken]:
    """Return a single (Connection, OAuthToken) for a Slack connection."""
    query = select(Connection).where(
        Connection.user_id == user_id,
        Connection.provider == "slack",
    )
    if persona_id:
        query = query.where(Connection.persona_id == persona_id)
    query = query.order_by(Connection.updated_at.desc())
    result = await db.execute(query)
    conn = result.scalars().first()
    if not conn:
        raise HTTPException(404, "No Slack connection found for this user")

    tok = (
        await db.execute(select(OAuthToken).where(OAuthToken.connection_id == conn.id))
    ).scalar_one_or_none()
    if not tok:
        raise HTTPException(404, "No OAuth token found for Slack connection")
    return conn, tok


# ── Channel ID validation ──────────────────────────────────────────────

_CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]+$")


def _validate_channel_id(channel_id: str) -> None:
    """Raise HTTPException(400) if channel_id is not a valid Slack channel ID."""
    if not _CHANNEL_ID_RE.match(channel_id):
        raise HTTPException(400, f"Invalid Slack channel ID: {channel_id}")


# ── Tool functions ──────────────────────────────────────────────────────

async def slack_read_channels(
    user_id: str,
    db: AsyncSession,
    persona_id: str | None = None,
    channel_id: str | None = None,
    limit: int = 20,
) -> dict:
    """List channels or read messages from a specific channel.

    When channel_id is None: returns list of channels the user belongs to.
    When channel_id is set: returns recent messages from that channel.
    """
    limit = max(1, min(limit, 200))
    conn, token = await _get_slack_connection(user_id, db, persona_id=persona_id)
    access_token = await _get_fresh_token(conn, token, db)

    async with httpx.AsyncClient() as client:
        if channel_id is None:
            # List channels
            data = await _slack_api_get(client, f"{SLACK_API}/conversations.list", {
                "types": "public_channel,private_channel",
                "exclude_archived": "true",
                "limit": str(min(limit, 200)),
            }, access_token)
            return {"channels": data.get("channels", [])}
        else:
            # Read messages from a specific channel
            _validate_channel_id(channel_id)
            data = await _slack_api_get(client, f"{SLACK_API}/conversations.history", {
                "channel": channel_id,
                "limit": str(min(limit, 100)),
            }, access_token)
            return {"messages": data.get("messages", [])}


async def slack_get_workspace(
    user_id: str,
    db: AsyncSession,
    persona_id: str | None = None,
) -> dict:
    """Get workspace metadata including team name, domain, and member list."""
    conn, token = await _get_slack_connection(user_id, db, persona_id=persona_id)
    access_token = await _get_fresh_token(conn, token, db)

    async with httpx.AsyncClient() as client:
        team_data = await _slack_api_get(
            client, f"{SLACK_API}/team.info", {}, access_token,
        )
        members_data = await _slack_api_get(
            client, f"{SLACK_API}/users.list", {"limit": "200"}, access_token,
        )
    return {
        "team": team_data.get("team", {}),
        "members": members_data.get("members", []),
    }


async def slack_read_threads(
    user_id: str,
    db: AsyncSession,
    persona_id: str | None = None,
    channel_id: str = "",
    thread_ts: str = "",
    limit: int = 50,
) -> dict:
    """Read thread replies for a given parent message."""
    limit = max(1, min(limit, 200))

    if not channel_id:
        raise HTTPException(400, "channel_id is required")
    _validate_channel_id(channel_id)

    if not thread_ts or "." not in thread_ts:
        raise HTTPException(
            400,
            "thread_ts is required and must be a Slack timestamp (e.g. 1234567890.123456)",
        )

    conn, token = await _get_slack_connection(user_id, db, persona_id=persona_id)
    access_token = await _get_fresh_token(conn, token, db)

    async with httpx.AsyncClient() as client:
        data = await _slack_api_get(client, f"{SLACK_API}/conversations.replies", {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": str(min(limit, 200)),
        }, access_token)
    return {"messages": data.get("messages", [])}

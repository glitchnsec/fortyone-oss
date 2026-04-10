"""Gmail tool: read and send email via Gmail REST API."""
import base64
import logging
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import get_settings
from app.crypto import encrypt, decrypt
from app.models import Connection, OAuthToken

logger = logging.getLogger(__name__)

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


async def _get_fresh_token(conn: Connection, token: OAuthToken, db: AsyncSession) -> str:
    """Return a valid access token. Refreshes if expired. Sets needs_reauth if refresh fails."""
    now = datetime.now(timezone.utc)
    expires_at = token.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and expires_at > now:
        return decrypt(token.access_token_enc)
    # Attempt refresh
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post("https://oauth2.googleapis.com/token", data={
                "client_id": get_settings().google_client_id,
                "client_secret": get_settings().google_client_secret,
                "refresh_token": decrypt(token.refresh_token_enc),
                "grant_type": "refresh_token",
            })
            resp.raise_for_status()
            data = resp.json()
        new_access = data["access_token"]
        exp = now + timedelta(seconds=data.get("expires_in", 3600))
        token.access_token_enc = encrypt(new_access)
        token.expires_at = exp
        await db.commit()
        return new_access
    except Exception as e:
        logger.error("token refresh failed conn=%s error=%s", conn.id, e)
        conn.status = "needs_reauth"
        await db.commit()
        raise HTTPException(401, "Your Google connection needs reauthorization. Reconnect to restore full access.")


async def _get_all_connections(user_id: str, db: AsyncSession, persona_id: str | None = None) -> list[tuple]:
    """Return (Connection, OAuthToken) pairs for Google connections.

    When persona_id is set: returns the single matching connection.
    When persona_id is None (shared/undetected): returns ALL connections
    across personas so read operations can merge results.
    """
    query = select(Connection).where(Connection.user_id == user_id, Connection.provider == "google")
    if persona_id:
        query = query.where(Connection.persona_id == persona_id)
    query = query.order_by(Connection.updated_at.desc())
    result = await db.execute(query)
    conns = result.scalars().all()
    if not conns:
        raise HTTPException(404, "No Google connection found for this user")

    pairs = []
    for conn in conns:
        tok = (await db.execute(select(OAuthToken).where(OAuthToken.connection_id == conn.id))).scalar_one_or_none()
        if tok:
            pairs.append((conn, tok))
    if not pairs:
        raise HTTPException(404, "No OAuth token found")
    return pairs


async def _get_connection(user_id: str, db: AsyncSession, persona_id: str | None = None):
    """Return a single (Connection, OAuthToken) — for write operations like send_email."""
    pairs = await _get_all_connections(user_id, db, persona_id=persona_id)
    return pairs[0]


async def _fetch_emails_for_connection(conn, token, db, client, max_results: int) -> list:
    """Fetch emails from a single Gmail connection."""
    access_token = await _get_fresh_token(conn, token, db)
    resp = await client.get(
        f"{GMAIL_BASE}/messages",
        params={"maxResults": max_results},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    resp.raise_for_status()
    ids = [m["id"] for m in resp.json().get("messages", [])]
    results = []
    for msg_id in ids[:max_results]:
        detail = await client.get(
            f"{GMAIL_BASE}/messages/{msg_id}",
            params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if detail.status_code == 200:
            payload_data = detail.json()
            hdrs = {h["name"]: h["value"] for h in payload_data.get("payload", {}).get("headers", [])}
            results.append({
                "id": msg_id,
                "subject": hdrs.get("Subject", "(no subject)"),
                "from": hdrs.get("From", ""),
                "snippet": payload_data.get("snippet", ""),
                "date": hdrs.get("Date", ""),
            })
    return results


async def read_emails(user_id: str, max_results: int = 10, db: AsyncSession = None, persona_id: str | None = None) -> list:
    """Return list of recent email summaries.

    When persona_id is set: emails from that persona's Gmail only.
    When persona_id is None (shared/undetected): merges emails across
    all connected Gmail accounts, sorted by date.
    """
    pairs = await _get_all_connections(user_id, db, persona_id=persona_id)

    if len(pairs) == 1:
        conn, token = pairs[0]
        async with httpx.AsyncClient() as client:
            return await _fetch_emails_for_connection(conn, token, db, client, max_results)

    # Multiple connections (shared/undetected) — merge across all
    all_emails = []
    async with httpx.AsyncClient() as client:
        for conn, token in pairs:
            try:
                emails = await _fetch_emails_for_connection(conn, token, db, client, max_results)
                all_emails.extend(emails)
            except Exception as exc:
                logger.warning("gmail fetch failed conn=%s error=%s", conn.id[:8], exc)

    all_emails.sort(key=lambda e: e.get("date") or "", reverse=True)
    return all_emails[:max_results]


async def send_email(user_id: str, to: str, subject: str, body: str, db: AsyncSession = None, persona_id: str | None = None) -> dict:
    """Send an email on behalf of the user."""
    conn, token = await _get_connection(user_id, db, persona_id=persona_id)
    access_token = await _get_fresh_token(conn, token, db)
    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GMAIL_BASE}/messages/send",
            json={"raw": raw},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return {"message_id": resp.json().get("id")}

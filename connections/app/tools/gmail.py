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
    if token.expires_at and token.expires_at > now:
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


async def _get_connection(user_id: str, db: AsyncSession, persona_id: str | None = None):
    """Fetch Connection + OAuthToken for user. Optionally scoped by persona_id. Raises 404 if not connected."""
    query = select(Connection).where(Connection.user_id == user_id, Connection.provider == "google")
    if persona_id:
        query = query.where(Connection.persona_id == persona_id)
    result = await db.execute(query)
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "No Google connection found for this user")
    tok_result = await db.execute(select(OAuthToken).where(OAuthToken.connection_id == conn.id))
    token = tok_result.scalar_one_or_none()
    if not token:
        raise HTTPException(404, "No OAuth token found")
    return conn, token


async def read_emails(user_id: str, max_results: int = 10, db: AsyncSession = None, persona_id: str | None = None) -> list:
    """Return list of recent email summaries."""
    conn, token = await _get_connection(user_id, db, persona_id=persona_id)
    access_token = await _get_fresh_token(conn, token, db)
    async with httpx.AsyncClient() as client:
        # List message IDs
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
                params={"format": "metadata", "metadataHeaders": ["Subject", "From"]},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if detail.status_code == 200:
                payload = detail.json()
                headers = {h["name"]: h["value"] for h in payload.get("payload", {}).get("headers", [])}
                results.append({
                    "id": msg_id,
                    "subject": headers.get("Subject", "(no subject)"),
                    "from": headers.get("From", ""),
                    "snippet": payload.get("snippet", ""),
                })
        return results


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

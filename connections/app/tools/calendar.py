"""Calendar tool: list and create events via Google Calendar REST API."""
import logging
from fastapi import HTTPException
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.crypto import decrypt
from app.models import Connection, OAuthToken
from app.tools.gmail import _get_fresh_token  # reuse token refresh logic

logger = logging.getLogger(__name__)
CAL_BASE = "https://www.googleapis.com/calendar/v3/calendars/primary"


async def _get_connection(user_id: str, db: AsyncSession, persona_id: str | None = None):
    query = select(Connection).where(Connection.user_id == user_id, Connection.provider == "google")
    if persona_id:
        query = query.where(Connection.persona_id == persona_id)
    result = await db.execute(query)
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "No Google connection found")
    tok = (await db.execute(select(OAuthToken).where(OAuthToken.connection_id == conn.id))).scalar_one_or_none()
    if not tok:
        raise HTTPException(404, "No OAuth token")
    return conn, tok


async def list_events(user_id: str, max_results: int = 10, db: AsyncSession = None, persona_id: str | None = None) -> list:
    """Return upcoming calendar events."""
    from datetime import datetime, timezone
    conn, token = await _get_connection(user_id, db, persona_id=persona_id)
    access_token = await _get_fresh_token(conn, token, db)
    now = datetime.now(timezone.utc).isoformat()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{CAL_BASE}/events",
            params={"timeMin": now, "maxResults": max_results, "orderBy": "startTime", "singleEvents": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return [
            {
                "id": e.get("id"),
                "summary": e.get("summary", "(no title)"),
                "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date")),
                "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date")),
                "description": e.get("description"),
            }
            for e in items
        ]


async def create_event(
    user_id: str,
    summary: str,
    start_datetime: str,   # ISO 8601 e.g. "2026-04-15T09:00:00-05:00"
    end_datetime: str,
    timezone_str: str = "UTC",
    description: str = "",
    db: AsyncSession = None,
    persona_id: str | None = None,
) -> dict:
    """Create a calendar event on behalf of the user."""
    conn, token = await _get_connection(user_id, db, persona_id=persona_id)
    access_token = await _get_fresh_token(conn, token, db)
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{CAL_BASE}/events",
            json={
                "summary": summary,
                "description": description,
                "start": {"dateTime": start_datetime, "timeZone": timezone_str},
                "end": {"dateTime": end_datetime, "timeZone": timezone_str},
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return {"event_id": resp.json().get("id")}

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
        raise HTTPException(404, "No Google connection found")

    pairs = []
    for conn in conns:
        tok = (await db.execute(select(OAuthToken).where(OAuthToken.connection_id == conn.id))).scalar_one_or_none()
        if tok:
            pairs.append((conn, tok))
    if not pairs:
        raise HTTPException(404, "No OAuth token")
    return pairs


async def _get_connection(user_id: str, db: AsyncSession, persona_id: str | None = None):
    """Return a single (Connection, OAuthToken) — for write operations like create_event."""
    pairs = await _get_all_connections(user_id, db, persona_id=persona_id)
    return pairs[0]


async def _fetch_events_for_connection(conn, token, db, client, now: str, max_results: int) -> list:
    """Fetch events from a single Google Calendar connection."""
    access_token = await _get_fresh_token(conn, token, db)
    resp = await client.get(
        f"{CAL_BASE}/events",
        params={"timeMin": now, "maxResults": max_results, "orderBy": "startTime", "singleEvents": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    resp.raise_for_status()
    return [
        {
            "id": e.get("id"),
            "summary": e.get("summary", "(no title)"),
            "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date")),
            "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date")),
            "description": e.get("description"),
        }
        for e in resp.json().get("items", [])
    ]


async def list_events(user_id: str, max_results: int = 10, db: AsyncSession = None, persona_id: str | None = None) -> list:
    """Return upcoming calendar events.

    When persona_id is set: events from that persona's calendar only.
    When persona_id is None (shared/undetected): merges events across
    all connected calendars, sorted by start time.
    """
    from datetime import datetime, timezone
    pairs = await _get_all_connections(user_id, db, persona_id=persona_id)
    now = datetime.now(timezone.utc).isoformat()

    if len(pairs) == 1:
        conn, token = pairs[0]
        async with httpx.AsyncClient() as client:
            return await _fetch_events_for_connection(conn, token, db, client, now, max_results)

    # Multiple connections (shared/undetected) — merge across all
    all_events = []
    async with httpx.AsyncClient() as client:
        for conn, token in pairs:
            try:
                events = await _fetch_events_for_connection(conn, token, db, client, now, max_results)
                all_events.extend(events)
            except Exception as exc:
                logger.warning("calendar fetch failed conn=%s error=%s", conn.id[:8], exc)

    all_events.sort(key=lambda e: e.get("start") or "")
    return all_events[:max_results]


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

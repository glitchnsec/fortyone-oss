"""
Dynamic capability discovery with Redis caching.

Fetches user capabilities from the Connections Service and caches them
in Redis with a 30-minute TTL.  The cache key includes the persona ID
so persona-scoped queries hit their own cache entry.

Three-tier persona scoping (resolve_capability_persona):
  1. Explicit persona (high confidence, known persona_id) -> filter by persona
  2. Shared / low-confidence persona -> return None (query all connections)
  3. Proactive / scheduled sources -> return None (query all connections)

On any failure fetching from the Connections Service the module degrades
gracefully by returning all known tools, so the assistant never blocks a
user action due to an infrastructure hiccup.

Response format: {"tools": ["read_emails", "send_email", ...]}
"""
import json
import logging
from typing import Optional

import httpx
import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

# Cache TTL in seconds (30 minutes)
CACHE_TTL = 1800

# Sources that should query ALL connections (not persona-scoped)
_UNSCOPED_SOURCES = frozenset({
    "scheduled_execute",
    "proactive",
    "scheduler",
    "briefing",
    "goal_checkin",
    "recap",
})

# All known built-in tool names — used for graceful degradation
_ALL_TOOLS: list[str] = [
    "read_emails",
    "send_email",
    "list_events",
    "create_event",
    "web_search",
]

_ALL_TOOLS_RESPONSE: dict = {"tools": _ALL_TOOLS}


async def get_capabilities(
    r: aioredis.Redis,
    user_id: str,
    persona_id: Optional[str] = None,
) -> dict:
    """
    Return capability dict with a ``tools`` key listing available tool names.

    Checks Redis cache first; on miss, fetches from the Connections Service
    and caches the result for CACHE_TTL seconds.
    """
    cache_key = f"capabilities_v2:{user_id}:{persona_id or 'all'}"

    # -- Cache hit ---------------------------------------------------------
    cached = await r.get(cache_key)
    if cached is not None:
        logger.info("CAPABILITIES cache_hit key=%s", cache_key)
        return json.loads(cached)

    # -- Cache miss — fetch from Connections Service -----------------------
    logger.info("CAPABILITIES cache_miss key=%s", cache_key)
    caps = await _fetch_capabilities(user_id, persona_id)
    await r.set(cache_key, json.dumps(caps), ex=CACHE_TTL)
    return caps


async def _fetch_capabilities(
    user_id: str,
    persona_id: Optional[str] = None,
) -> dict:
    """
    Query the Connections Service for a user's active connections and
    aggregate tool names across all connections (union / deduplicate).

    On ANY error, returns all tools (graceful degradation per D-08).
    """
    try:
        settings = get_settings()
        url = f"{settings.connections_service_url}/connections/{user_id}"
        params: dict[str, str] = {}
        if persona_id is not None:
            params["persona_id"] = persona_id

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()

        data = resp.json()
        connections = data.get("connections", [])

        # Aggregate: union all tool names across connections
        tools_set: set[str] = set()
        for conn in connections:
            conn_caps = conn.get("capabilities", {})
            conn_tools = conn_caps.get("tools", [])
            tools_set.update(conn_tools)

        return {"tools": sorted(tools_set)}

    except Exception as exc:
        logger.warning(
            "CAPABILITIES fetch_error user=%s error=%s",
            user_id[:8] if user_id else "?",
            exc,
        )
        # Graceful degradation: assume all capabilities available
        return {"tools": list(_ALL_TOOLS)}


async def invalidate_capabilities(r: aioredis.Redis, user_id: str) -> None:
    """
    Delete all cached capability entries for a user (all persona variants).

    Uses SCAN to find matching keys without blocking Redis.
    Cleans up both v1 and v2 cache keys.
    """
    deleted = 0
    for prefix in ("capabilities:", "capabilities_v2:"):
        async for key in r.scan_iter(match=f"{prefix}{user_id}:*", count=100):
            await r.delete(key)
            deleted += 1
    logger.info("CAPABILITIES invalidated user=%s keys_deleted=%d", user_id[:8] if user_id else "?", deleted)


def resolve_capability_persona(payload: dict) -> Optional[str]:
    """
    Determine whether to scope the capability query to a specific persona.

    Three-tier rule:
      - Proactive / scheduled sources -> None (query all connections)
      - Shared persona or low confidence (< 0.6) -> None (query all)
      - Otherwise -> the explicit persona_id from the payload
    """
    # Tier 3: unscoped sources (proactive, scheduled, briefing, etc.)
    source = payload.get("source", "")
    if source in _UNSCOPED_SOURCES:
        return None

    # Tier 2: shared persona or low confidence
    persona = payload.get("persona", "")
    confidence = payload.get("persona_confidence", 0.0)
    if persona == "shared" or confidence < 0.6:
        return None

    # Tier 1: explicit persona
    return payload.get("persona_id")

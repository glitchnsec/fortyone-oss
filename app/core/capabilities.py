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
    "slack_read_channels",
    "slack_get_workspace",
    "slack_read_threads",
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
        headers = {"X-Service-Token": settings.service_auth_token} if settings.service_auth_token else {}

        async with httpx.AsyncClient(timeout=5.0, headers=headers) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()

        data = resp.json()
        connections = data.get("connections", [])

        # Aggregate: union all tool names across connections
        tools_set: set[str] = set()
        mcp_tool_connections: dict[str, str] = {}
        for conn in connections:
            conn_caps = conn.get("capabilities", {})
            conn_tools = conn_caps.get("tools", [])
            tools_set.update(conn_tools)

            # MCP connections: build namespaced tool names and map to connection ID
            if conn.get("execution_type") == "mcp" and conn.get("status") == "connected":
                conn_id = conn.get("id", "")
                conn_id_short = conn_id[:8]
                for mcp_tool in conn.get("mcp_tools", []):
                    tool_name = mcp_tool.get("name", "")
                    if tool_name:
                        namespaced = f"mcp_{conn_id_short}_{tool_name}"
                        tools_set.add(namespaced)
                        mcp_tool_connections[namespaced] = conn_id

        return {
            "tools": sorted(tools_set),
            "mcp_tool_connections": mcp_tool_connections,
        }

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


async def find_persona_with_tool(
    user_id: str,
    tool_name: str,
    exclude_persona_id: str | None = None,
) -> str | None:
    """Find which persona has a specific tool, excluding the given persona.

    Returns the persona name (e.g., "Personal") or None if no other persona
    has it. Used to produce actionable error messages when a tool call fails
    due to wrong-persona routing.
    """
    if not user_id:
        return None

    try:
        settings = get_settings()
        url = f"{settings.connections_service_url}/connections/{user_id}"
        headers = {"X-Service-Token": settings.service_auth_token} if settings.service_auth_token else {}

        async with httpx.AsyncClient(timeout=5.0, headers=headers) as client:
            resp = await client.get(url)  # No persona filter = all connections
            resp.raise_for_status()

        data = resp.json()
        connections = data.get("connections", [])

        # Find a connection on a different persona that has the tool
        matching_persona_id = None
        for conn in connections:
            conn_persona_id = conn.get("persona_id")
            # Skip connections on the excluded persona
            if conn_persona_id == exclude_persona_id:
                continue
            if conn.get("status") != "connected":
                continue

            # Check native tools
            conn_tools = conn.get("capabilities", {}).get("tools", [])
            if tool_name in conn_tools:
                matching_persona_id = conn_persona_id
                break

            # Check MCP tools
            if conn.get("execution_type") == "mcp":
                for mcp_tool in conn.get("mcp_tools", []):
                    conn_id_short = conn.get("id", "")[:8]
                    namespaced = f"mcp_{conn_id_short}_{mcp_tool.get('name', '')}"
                    if namespaced == tool_name or mcp_tool.get("name") == tool_name:
                        matching_persona_id = conn_persona_id
                        break
                if matching_persona_id:
                    break

        if not matching_persona_id:
            return None

        # Look up the persona name from the database
        try:
            from app.database import AsyncSessionLocal
            from app.memory.models import Persona
            from sqlalchemy import select as sa_select

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    sa_select(Persona).where(
                        Persona.id == matching_persona_id,
                        Persona.user_id == user_id,
                    )
                )
                persona = result.scalars().first()
                if persona:
                    return persona.name
        except Exception:
            pass

        return None

    except Exception as exc:
        logger.warning(
            "find_persona_with_tool failed user=%s tool=%s error=%s",
            user_id[:8] if user_id else "?",
            tool_name,
            exc,
        )
        return None


async def find_personas_with_tool(
    user_id: str,
    tool_name: str,
    exclude_persona_id: str | None = None,
) -> list[dict]:
    """Find all personas that have a specific tool, excluding the given persona.

    Returns list of {"persona_id": str, "persona_name": str} dicts.
    Empty list if no other persona has the tool.
    """
    if not user_id:
        return []

    try:
        settings = get_settings()
        url = f"{settings.connections_service_url}/connections/{user_id}"
        headers = {"X-Service-Token": settings.service_auth_token} if settings.service_auth_token else {}

        async with httpx.AsyncClient(timeout=5.0, headers=headers) as client:
            resp = await client.get(url)  # No persona filter = all connections
            resp.raise_for_status()

        data = resp.json()
        connections = data.get("connections", [])

        # Collect ALL matching persona_ids (not just the first)
        matching_persona_ids: set[str] = set()
        for conn in connections:
            conn_persona_id = conn.get("persona_id")
            if not conn_persona_id:
                continue
            # Skip connections on the excluded persona
            if conn_persona_id == exclude_persona_id:
                continue
            if conn.get("status") != "connected":
                continue

            # Check native tools
            conn_tools = conn.get("capabilities", {}).get("tools", [])
            if tool_name in conn_tools:
                matching_persona_ids.add(conn_persona_id)
                continue

            # Check MCP tools
            if conn.get("execution_type") == "mcp":
                for mcp_tool in conn.get("mcp_tools", []):
                    conn_id_short = conn.get("id", "")[:8]
                    namespaced = f"mcp_{conn_id_short}_{mcp_tool.get('name', '')}"
                    if namespaced == tool_name or mcp_tool.get("name") == tool_name:
                        matching_persona_ids.add(conn_persona_id)
                        break

        if not matching_persona_ids:
            return []

        # Look up persona names from the database (batch query)
        try:
            from app.database import AsyncSessionLocal
            from app.memory.models import Persona
            from sqlalchemy import select as sa_select

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    sa_select(Persona).where(
                        Persona.id.in_(list(matching_persona_ids)),
                        Persona.user_id == user_id,
                    )
                )
                personas = result.scalars().all()
                return sorted(
                    [
                        {"persona_id": str(p.id), "persona_name": p.name}
                        for p in personas
                    ],
                    key=lambda x: x["persona_name"],
                )
        except Exception:
            # If DB lookup fails, return IDs without names
            return sorted(
                [
                    {"persona_id": pid, "persona_name": "Unknown"}
                    for pid in matching_persona_ids
                ],
                key=lambda x: x["persona_id"],
            )

    except Exception as exc:
        logger.warning(
            "find_personas_with_tool failed user=%s tool=%s error=%s",
            user_id[:8] if user_id else "?",
            tool_name,
            exc,
        )
        return []


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

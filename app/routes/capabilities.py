"""Capabilities and Custom Agents API routes.

Endpoints:
  GET    /api/v1/capabilities          - Subagent capabilities with per-persona connection status
  GET    /api/v1/custom-agents         - List user's custom agents
  POST   /api/v1/custom-agents         - Create a custom agent
  PATCH  /api/v1/custom-agents/{id}    - Update a custom agent
  DELETE /api/v1/custom-agents/{id}    - Delete a custom agent

All routes require a valid Bearer JWT token (get_current_user dependency).
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.tools import get_tool_schemas, load_subagents
from app.database import AsyncSessionLocal
from app.memory.models import CustomAgent, Persona, User
from app.middleware.auth import get_current_user

router = APIRouter(prefix="/api/v1")
logger = logging.getLogger(__name__)


# ─── Dependencies ────────────────────────────────────────────────────────────

async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def _connections_client():
    """Yield a short-lived httpx client pointed at the connections service."""
    s = get_settings()
    headers = {"X-Service-Token": s.service_auth_token} if s.service_auth_token else {}
    async with httpx.AsyncClient(
        base_url=s.connections_service_url, timeout=10.0, headers=headers,
    ) as client:
        yield client


# ─── Provider mapping ────────────────────────────────────────────────────────

_AGENT_PROVIDER_MAP = {
    "email_agent": "google",
    "calendar_agent": "google",
}


# ─── GET /capabilities ───────────────────────────────────────────────────────

@router.get("/capabilities")
async def list_capabilities(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
    client: httpx.AsyncClient = Depends(_connections_client),
):
    """Return all subagent capabilities with per-persona connection status."""
    subagents = load_subagents()

    # Fetch user's personas
    result = await db.execute(
        select(Persona).where(Persona.user_id == user.id)
    )
    personas = result.scalars().all()

    # Fetch connections from connections service (graceful degradation)
    connections = []
    try:
        resp = await client.get(f"/connections/{user.id}")
        resp.raise_for_status()
        connections = resp.json().get("connections", [])
    except Exception as e:
        logger.warning("connections service unavailable user_id=%s error=%s", user.id, e)

    # Build lookup: (persona_id, provider) -> status
    conn_lookup: dict[tuple[str, str], str] = {}
    for conn in connections:
        key = (conn.get("persona_id", ""), conn.get("provider", ""))
        conn_lookup[key] = conn.get("status", "disconnected")

    capabilities = []
    for agent in subagents:
        agent_name = agent.get("name", "")
        required_provider = _AGENT_PROVIDER_MAP.get(agent_name)

        # Build tool list
        tools = []
        for tool in agent.get("tools", []):
            tools.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "risk_level": tool.get("risk_level", "low"),
            })

        # Build per-persona status
        persona_status = []
        for p in personas:
            if required_provider is None:
                status = "no_connection_needed"
            else:
                status = conn_lookup.get((p.id, required_provider), "disconnected")
            persona_status.append({
                "persona_id": p.id,
                "persona_name": p.name,
                "status": status,
            })

        capabilities.append({
            "name": agent_name,
            "description": agent.get("description", ""),
            "tools": tools,
            "persona_status": persona_status,
        })

    # Add MCP connections as dynamic capability entries
    # Group MCP connections by server URL to avoid duplicates across personas
    mcp_servers: dict[str, dict] = {}  # server_url -> {tools, persona_statuses}
    for conn in connections:
        if conn.get("execution_type") != "mcp" or conn.get("provider") != "mcp":
            continue
        server_url = conn.get("mcp_server_url", "")
        display_name = conn.get("display_name", "MCP Server")
        if server_url not in mcp_servers:
            mcp_servers[server_url] = {
                "display_name": display_name,
                "tools": [],
                "persona_ids_connected": set(),
            }
            # Build tool list from mcp_tools
            for tool in conn.get("mcp_tools", []):
                tool_name = tool.get("name", "")
                if tool_name:
                    mcp_servers[server_url]["tools"].append({
                        "name": tool_name,
                        "description": tool.get("description", ""),
                        "risk_level": "medium",
                    })
        if conn.get("status") == "connected":
            mcp_servers[server_url]["persona_ids_connected"].add(conn.get("persona_id", ""))

    for server_url, server_info in mcp_servers.items():
        persona_status = []
        for p in personas:
            status = "connected" if p.id in server_info["persona_ids_connected"] else "disconnected"
            persona_status.append({
                "persona_id": p.id,
                "persona_name": p.name,
                "status": status,
            })
        capabilities.append({
            "name": f"mcp_{server_info['display_name'].lower()}",
            "description": f"MCP Server: {server_info['display_name']} ({server_url})",
            "tools": server_info["tools"],
            "persona_status": persona_status,
        })

    return {"capabilities": capabilities}


# ─── Request / Response models ───────────────────────────────────────────────

class CreateCustomAgent(BaseModel):
    name: str
    description: Optional[str] = None
    agent_type: str
    config: dict
    parameters_schema: Optional[dict] = None
    risk_level: str = "low"

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, v: str) -> str:
        if v not in ("webhook", "prompt", "yaml_script"):
            raise ValueError("agent_type must be one of: webhook, prompt, yaml_script")
        return v

    @field_validator("risk_level")
    @classmethod
    def validate_risk_level(cls, v: str) -> str:
        if v not in ("low", "medium", "high"):
            raise ValueError("risk_level must be one of: low, medium, high")
        return v


class UpdateCustomAgent(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    agent_type: Optional[str] = None
    config: Optional[dict] = None
    parameters_schema: Optional[dict] = None
    risk_level: Optional[str] = None
    enabled: Optional[bool] = None

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("webhook", "prompt", "yaml_script"):
            raise ValueError("agent_type must be one of: webhook, prompt, yaml_script")
        return v

    @field_validator("risk_level")
    @classmethod
    def validate_risk_level(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("low", "medium", "high"):
            raise ValueError("risk_level must be one of: low, medium, high")
        return v


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    """Convert agent name to a slug suitable for tool naming."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return f"custom_{slug}"


_PRIVATE_IP_PATTERNS = [
    re.compile(r"^127\."),
    re.compile(r"^10\."),
    re.compile(r"^172\.(1[6-9]|2[0-9]|3[01])\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^0\."),
    re.compile(r"^localhost$", re.IGNORECASE),
]


def _validate_webhook_url(url: str) -> None:
    """Validate that a webhook URL is HTTPS and not targeting private IP ranges."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HTTPException(400, "Webhook URL must use HTTPS")
    hostname = parsed.hostname or ""
    for pattern in _PRIVATE_IP_PATTERNS:
        if pattern.match(hostname):
            raise HTTPException(400, "Webhook URL must not target private/local IP addresses")


def _serialize_agent(agent: CustomAgent) -> dict:
    """Serialize a CustomAgent row to a JSON-safe dict."""
    config = {}
    if agent.config_json:
        try:
            config = json.loads(agent.config_json)
        except (json.JSONDecodeError, TypeError):
            config = {}

    params_schema = None
    if agent.parameters_schema_json:
        try:
            params_schema = json.loads(agent.parameters_schema_json)
        except (json.JSONDecodeError, TypeError):
            params_schema = None

    return {
        "id": agent.id,
        "name": agent.name,
        "description": agent.description,
        "agent_type": agent.agent_type,
        "config": config,
        "parameters_schema": params_schema,
        "risk_level": agent.risk_level,
        "enabled": agent.enabled,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
    }


# ─── GET /custom-agents ─────────────────────────────────────────────────────

@router.get("/custom-agents")
async def list_custom_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Return all custom agents for the authenticated user."""
    result = await db.execute(
        select(CustomAgent)
        .where(CustomAgent.user_id == user.id)
        .order_by(CustomAgent.created_at.desc())
    )
    agents = result.scalars().all()
    return {"agents": [_serialize_agent(a) for a in agents]}


# ─── POST /custom-agents ────────────────────────────────────────────────────

@router.post("/custom-agents", status_code=201)
async def create_custom_agent(
    body: CreateCustomAgent,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Create a new custom agent with tool name uniqueness and webhook validation."""
    # Generate tool name slug
    tool_slug = _slugify(body.name)

    # Check collision with built-in tools
    builtin_names = {s["function"]["name"] for s in get_tool_schemas()}
    if tool_slug in builtin_names:
        raise HTTPException(
            409, f"Tool name '{tool_slug}' conflicts with a built-in tool"
        )

    # Check collision with user's existing custom agents
    result = await db.execute(
        select(CustomAgent).where(CustomAgent.user_id == user.id)
    )
    existing_agents = result.scalars().all()
    existing_slugs = {_slugify(a.name) for a in existing_agents}
    if tool_slug in existing_slugs:
        raise HTTPException(
            409, f"You already have a custom agent with tool name '{tool_slug}'"
        )

    # Validate webhook URL if type is webhook
    if body.agent_type == "webhook":
        webhook_url = body.config.get("url", "")
        if not webhook_url:
            raise HTTPException(400, "Webhook agents require a 'url' in config")
        _validate_webhook_url(webhook_url)

    agent = CustomAgent(
        user_id=user.id,
        name=body.name,
        description=body.description,
        agent_type=body.agent_type,
        config_json=json.dumps(body.config),
        parameters_schema_json=json.dumps(body.parameters_schema) if body.parameters_schema else None,
        risk_level=body.risk_level,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    return _serialize_agent(agent)


# ─── PATCH /custom-agents/{agent_id} ────────────────────────────────────────

@router.patch("/custom-agents/{agent_id}")
async def update_custom_agent(
    agent_id: str,
    body: UpdateCustomAgent,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Update an existing custom agent (user-scoped)."""
    result = await db.execute(
        select(CustomAgent).where(
            CustomAgent.id == agent_id,
            CustomAgent.user_id == user.id,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Custom agent not found")

    updates = body.model_dump(exclude_unset=True)

    if "name" in updates:
        agent.name = updates["name"]
    if "description" in updates:
        agent.description = updates["description"]
    if "agent_type" in updates:
        agent.agent_type = updates["agent_type"]
    if "config" in updates:
        # Validate webhook URL if switching to or updating webhook type
        effective_type = updates.get("agent_type", agent.agent_type)
        if effective_type == "webhook":
            webhook_url = updates["config"].get("url", "")
            if webhook_url:
                _validate_webhook_url(webhook_url)
        agent.config_json = json.dumps(updates["config"])
    if "parameters_schema" in updates:
        agent.parameters_schema_json = json.dumps(updates["parameters_schema"]) if updates["parameters_schema"] else None
    if "risk_level" in updates:
        agent.risk_level = updates["risk_level"]
    if "enabled" in updates:
        agent.enabled = updates["enabled"]

    agent.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(agent)

    return _serialize_agent(agent)


# ─── DELETE /custom-agents/{agent_id} ────────────────────────────────────────

@router.delete("/custom-agents/{agent_id}", status_code=204)
async def delete_custom_agent(
    agent_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Delete a custom agent (user-scoped)."""
    result = await db.execute(
        select(CustomAgent).where(
            CustomAgent.id == agent_id,
            CustomAgent.user_id == user.id,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Custom agent not found")

    await db.delete(agent)
    await db.commit()

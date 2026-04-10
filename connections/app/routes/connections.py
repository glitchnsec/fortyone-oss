"""List, update, and delete user connections."""
import json
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Connection
from app.providers.base import CapabilityManifest
from app.providers.google import get_provider

router = APIRouter()
logger = logging.getLogger(__name__)


class ConnectionUpdate(BaseModel):
    persona_id: str | None = None  # null means "shared"


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/connections/{user_id}")
async def list_connections(user_id: str, persona_id: str | None = None, db: AsyncSession = Depends(_get_db)):
    query = select(Connection).where(Connection.user_id == user_id)
    if persona_id:
        query = query.where(Connection.persona_id == persona_id)
    result = await db.execute(query)
    conns = result.scalars().all()
    out = []
    for c in conns:
        try:
            p = get_provider(c.provider)
            scopes = c.granted_scopes.split(" ") if c.granted_scopes else []
            manifest = p.capability_manifest(scopes)
        except ValueError:
            manifest = CapabilityManifest(provider=c.provider, tools=[])
        # Include full MCP tool schemas for MCP connections so the main API
        # can build LLM tool definitions without a second round-trip.
        mcp_tools = []
        if c.provider == "mcp" and c.mcp_tools_json:
            try:
                mcp_tools = json.loads(c.mcp_tools_json)
            except (json.JSONDecodeError, TypeError):
                mcp_tools = []

        # Display name: prefer stored name, then derive from URL, then provider
        display_name = c.provider
        if c.provider == "mcp":
            if getattr(c, "display_name", None):
                display_name = c.display_name
            elif c.mcp_server_url:
                from urllib.parse import urlparse
                host = urlparse(c.mcp_server_url).hostname or c.mcp_server_url
                parts = host.replace("mcp.", "").replace("api.", "").split(".")
                display_name = parts[0].capitalize() if parts else "MCP Server"
            else:
                display_name = "MCP Server"

        out.append({
            "id": c.id,
            "provider": c.provider,
            "status": c.status,
            "persona_id": c.persona_id,
            "execution_type": c.execution_type or "native",
            "capabilities": {"tools": manifest.tools},
            "mcp_tools": mcp_tools,
            "mcp_server_url": c.mcp_server_url,
            "display_name": display_name,
        })
    return {"connections": out}


@router.delete("/connections/{conn_id}", status_code=204)
async def delete_connection(conn_id: str, db: AsyncSession = Depends(_get_db)):
    result = await db.execute(select(Connection).where(Connection.id == conn_id))
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")
    await db.delete(conn)
    await db.commit()
    logger.info("Connection deleted id=%s", conn_id)


@router.patch("/connections/{conn_id}")
async def update_connection(
    conn_id: str,
    body: ConnectionUpdate,
    db: AsyncSession = Depends(_get_db),
):
    """Update a connection's persona assignment."""
    result = await db.execute(select(Connection).where(Connection.id == conn_id))
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")
    conn.persona_id = body.persona_id
    conn.updated_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("Connection updated id=%s persona_id=%s", conn_id, body.persona_id)
    return {"id": conn.id, "provider": conn.provider, "persona_id": conn.persona_id}

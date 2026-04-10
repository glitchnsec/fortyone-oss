"""MCP connection management routes — create, rediscover, and execute.

Handles MCP server connections: validates server URLs against the admin allowlist (D-13),
discovers tools via JSON-RPC initialize+tools/list handshake, stores encrypted credentials,
and proxies tool execution via tools/call. User isolation enforced on all operations.
"""
import json
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.crypto import encrypt, decrypt
from app.database import AsyncSessionLocal
from app.models import Connection, OAuthToken
from app.providers.mcp import (
    MCPError,
    build_auth_headers,
    discover_tools,
    mcp_call,
    validate_tool_list,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Request models ─────────────────────────────────────────────────────────

class MCPConnectInput(BaseModel):
    user_id: str
    persona_id: str | None = None
    server_url: str          # URL of the MCP server endpoint
    auth_type: str = "none"  # "oauth", "api_key", "none"
    api_key: str | None = None  # For auth_type="api_key"
    name: str = ""           # User-friendly name for this connection


class MCPExecuteInput(BaseModel):
    user_id: str
    connection_id: str
    tool_name: str
    arguments: dict = {}
    persona_id: str | None = None


# ── DB dependency ──────────────────────────────────────────────────────────

async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ── URL validation ─────────────────────────────────────────────────────────

def _validate_server_url(url: str) -> tuple[bool, str]:
    """Validate MCP server URL. HTTPS required; http allowed for localhost only."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL format"

    if parsed.scheme == "https":
        return True, ""
    if parsed.scheme == "http":
        host = parsed.hostname or ""
        if host in ("localhost", "127.0.0.1", "::1"):
            return True, ""
        return False, "HTTP is only allowed for localhost. Use HTTPS for remote servers."
    return False, f"Unsupported URL scheme: {parsed.scheme}. Use HTTPS."


def _check_allowlist(url: str, allowlist: str) -> bool:
    """Check if URL matches the admin allowlist (D-13).

    Empty allowlist = allow all. Comma-separated URL prefixes.
    """
    if not allowlist.strip():
        return True
    patterns = [p.strip() for p in allowlist.split(",") if p.strip()]
    return any(url.startswith(p) for p in patterns)


# ── Routes ─────────────────────────────────────────────────────────────────

@router.post("/mcp/connect")
async def create_mcp_connection(
    body: MCPConnectInput,
    db: AsyncSession = Depends(_get_db),
):
    """Create an MCP server connection with tool discovery."""
    # Validate server URL
    url_ok, url_err = _validate_server_url(body.server_url)
    if not url_ok:
        raise HTTPException(400, {"error": "invalid_url", "detail": url_err})

    # Check admin allowlist (D-13)
    settings = get_settings()
    if not _check_allowlist(body.server_url, settings.mcp_allowlist):
        raise HTTPException(403, {
            "error": "server_not_allowed",
            "detail": "This MCP server URL is not on the admin allowlist",
        })

    # Build auth headers
    auth_headers = build_auth_headers(body.auth_type, body.api_key)

    # Discover tools from MCP server (D-08)
    try:
        raw_tools = await discover_tools(body.server_url, auth_headers=auth_headers)
    except MCPError as exc:
        raise HTTPException(400, {"error": "discovery_failed", "detail": str(exc)})
    except Exception as exc:
        logger.error("MCP discovery failed url=%s error=%s", body.server_url, exc, exc_info=True)
        raise HTTPException(400, {"error": "discovery_failed", "detail": str(exc)})

    # Validate tool names (D-14)
    valid_tools, errors = validate_tool_list(raw_tools)
    if errors and not valid_tools:
        raise HTTPException(400, {
            "error": "tool_validation_failed",
            "detail": errors,
        })

    # Upsert: remove existing connection for same user+provider+persona+url
    old_result = await db.execute(
        select(Connection).where(
            Connection.user_id == body.user_id,
            Connection.provider == "mcp",
            Connection.persona_id == body.persona_id,
            Connection.mcp_server_url == body.server_url,
        )
    )
    old_conn = old_result.scalar_one_or_none()
    if old_conn:
        await db.delete(old_conn)
        await db.commit()

    # Create connection
    conn = Connection(
        user_id=body.user_id,
        provider="mcp",
        execution_type="mcp",
        status="connected",
        mcp_server_url=body.server_url,
        mcp_tools_json=json.dumps(valid_tools),
        granted_scopes="",
        persona_id=body.persona_id,
    )
    db.add(conn)
    await db.flush()

    # Store encrypted API key if provided (reuse OAuthToken model)
    if body.auth_type == "api_key" and body.api_key:
        db.add(OAuthToken(
            connection_id=conn.id,
            access_token_enc=encrypt(body.api_key),
            refresh_token_enc=None,
            expires_at=None,
        ))

    await db.commit()

    tool_names = [t.get("name", "") for t in valid_tools]
    logger.info(
        "MCP connected user_id=%s url=%s tools=%d",
        body.user_id, body.server_url, len(tool_names),
    )
    result = {
        "id": conn.id,
        "provider": "mcp",
        "tools": tool_names,
        "status": "connected",
    }
    if errors:
        result["warnings"] = errors
    return result


@router.post("/mcp/rediscover/{connection_id}")
async def rediscover_tools(
    connection_id: str,
    db: AsyncSession = Depends(_get_db),
):
    """Re-run tool discovery on an existing MCP connection."""
    result = await db.execute(
        select(Connection).where(Connection.id == connection_id)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")
    if conn.execution_type != "mcp":
        raise HTTPException(400, "Not an MCP connection")

    # Get credentials if stored
    auth_headers = await _get_connection_auth_headers(conn, db)

    try:
        raw_tools = await discover_tools(conn.mcp_server_url, auth_headers=auth_headers)
    except Exception as exc:
        raise HTTPException(400, {"error": "discovery_failed", "detail": str(exc)})

    valid_tools, errors = validate_tool_list(raw_tools)
    conn.mcp_tools_json = json.dumps(valid_tools)
    await db.commit()

    tool_names = [t.get("name", "") for t in valid_tools]
    logger.info(
        "MCP rediscovered connection_id=%s tools=%d",
        connection_id, len(tool_names),
    )
    result_data = {"tools": tool_names}
    if errors:
        result_data["warnings"] = errors
    return result_data


@router.post("/tools/mcp/execute")
async def execute_mcp_tool(
    body: MCPExecuteInput,
    db: AsyncSession = Depends(_get_db),
):
    """Execute a tool on a remote MCP server. Enforces user isolation."""
    # Look up connection with user_id enforcement
    result = await db.execute(
        select(Connection).where(
            Connection.id == body.connection_id,
            Connection.user_id == body.user_id,
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")
    if conn.execution_type != "mcp":
        raise HTTPException(400, "Not an MCP connection")
    if conn.status != "connected":
        raise HTTPException(400, f"Connection status is '{conn.status}', expected 'connected'")

    # Get credentials if stored
    auth_headers = await _get_connection_auth_headers(conn, db)

    try:
        tool_result = await mcp_call(
            conn.mcp_server_url,
            "tools/call",
            params={"name": body.tool_name, "arguments": body.arguments},
            headers=auth_headers,
        )
    except MCPError as exc:
        return {"error": "mcp_execution_failed", "detail": str(exc)}
    except Exception as exc:
        logger.error(
            "MCP execution failed connection_id=%s tool=%s error=%s",
            body.connection_id, body.tool_name, exc, exc_info=True,
        )
        return {"error": "mcp_execution_failed", "detail": str(exc)}

    return {"result": tool_result}


# ── Helpers ────────────────────────────────────────────────────────────────

async def _get_connection_auth_headers(
    conn: Connection,
    db: AsyncSession,
) -> dict | None:
    """Retrieve and decrypt auth headers for an MCP connection."""
    token_result = await db.execute(
        select(OAuthToken).where(OAuthToken.connection_id == conn.id)
    )
    token = token_result.scalar_one_or_none()
    if not token:
        return None

    try:
        decrypted = decrypt(token.access_token_enc)
        return build_auth_headers("api_key", decrypted)
    except Exception as exc:
        logger.warning(
            "Failed to decrypt credentials connection_id=%s error=%s",
            conn.id, exc,
        )
        return None

"""MCP connection management routes — create, oauth, rediscover, and execute."""
import json
import logging
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.crypto import decrypt, encrypt
from app.database import AsyncSessionLocal
from app.models import Connection, OAuthState, OAuthToken
from app.providers.mcp import (
    MCPError,
    build_auth_headers,
    canonicalize_resource_url,
    compute_expires_at,
    discover_oauth_metadata,
    discover_tools,
    exchange_oauth_code,
    generate_pkce_pair,
    mcp_call,
    refresh_oauth_token,
    register_oauth_client,
    validate_tool_list,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class MCPConnectInput(BaseModel):
    user_id: str
    persona_id: str | None = None
    server_url: str
    auth_type: str = "none"
    api_key: str | None = None
    name: str = ""


class MCPExecuteInput(BaseModel):
    user_id: str
    connection_id: str
    tool_name: str
    arguments: dict = {}
    persona_id: str | None = None


class MCPOAuthInitiateInput(BaseModel):
    user_id: str
    persona_id: str | None = None
    server_url: str
    name: str | None = None


class MCPOAuthCallbackInput(BaseModel):
    code: str
    state: str


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


def _validate_server_url(url: str) -> tuple[bool, str]:
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
    if not allowlist.strip():
        return True
    patterns = [p.strip() for p in allowlist.split(",") if p.strip()]
    return any(url.startswith(p) for p in patterns)


async def _replace_existing_connection(
    db: AsyncSession,
    *,
    user_id: str,
    persona_id: str | None,
    server_url: str,
) -> None:
    old_result = await db.execute(
        select(Connection).where(
            Connection.user_id == user_id,
            Connection.provider == "mcp",
            Connection.persona_id == persona_id,
            Connection.mcp_server_url == server_url,
        )
    )
    old_conn = old_result.scalar_one_or_none()
    if old_conn:
        await db.delete(old_conn)
        await db.commit()


@router.post("/mcp/connect")
async def create_mcp_connection(body: MCPConnectInput, db: AsyncSession = Depends(_get_db)):
    url_ok, url_err = _validate_server_url(body.server_url)
    if not url_ok:
        raise HTTPException(400, {"error": "invalid_url", "detail": url_err})

    settings = get_settings()
    if not _check_allowlist(body.server_url, settings.mcp_allowlist):
        raise HTTPException(403, {
            "error": "server_not_allowed",
            "detail": "This MCP server URL is not on the admin allowlist",
        })
    if body.auth_type == "oauth":
        raise HTTPException(400, {
            "error": "oauth_requires_browser_flow",
            "detail": "Use the MCP OAuth initiate endpoint",
        })

    auth_headers = build_auth_headers(body.auth_type, body.api_key)
    try:
        raw_tools = await discover_tools(body.server_url, auth_headers=auth_headers)
    except MCPError as exc:
        raise HTTPException(400, {"error": "discovery_failed", "detail": str(exc)})
    except Exception as exc:
        logger.error("MCP discovery failed url=%s error=%s", body.server_url, exc, exc_info=True)
        raise HTTPException(400, {"error": "discovery_failed", "detail": str(exc)})

    valid_tools, errors = validate_tool_list(raw_tools)
    if errors and not valid_tools:
        raise HTTPException(400, {"error": "tool_validation_failed", "detail": errors})

    await _replace_existing_connection(
        db, user_id=body.user_id, persona_id=body.persona_id, server_url=body.server_url
    )

    tool_names = [t.get("name", "") for t in valid_tools if t.get("name")]
    conn = Connection(
        user_id=body.user_id,
        provider="mcp",
        execution_type="mcp",
        status="connected",
        mcp_server_url=body.server_url,
        mcp_tools_json=json.dumps(valid_tools),
        granted_scopes=" ".join(tool_names),
        persona_id=body.persona_id,
    )
    db.add(conn)
    await db.flush()

    if body.auth_type == "api_key" and body.api_key:
        db.add(OAuthToken(
            connection_id=conn.id,
            access_token_enc=encrypt(body.api_key),
            refresh_token_enc=None,
            expires_at=None,
        ))

    await db.commit()
    result = {"id": conn.id, "provider": "mcp", "tools": tool_names, "status": "connected"}
    if errors:
        result["warnings"] = errors
    return result


@router.post("/mcp/oauth/initiate")
async def initiate_mcp_oauth(body: MCPOAuthInitiateInput, db: AsyncSession = Depends(_get_db)):
    url_ok, url_err = _validate_server_url(body.server_url)
    if not url_ok:
        raise HTTPException(400, {"error": "invalid_url", "detail": url_err})

    settings = get_settings()
    if not _check_allowlist(body.server_url, settings.mcp_allowlist):
        raise HTTPException(403, {
            "error": "server_not_allowed",
            "detail": "This MCP server URL is not on the admin allowlist",
        })

    try:
        oauth_metadata = await discover_oauth_metadata(body.server_url)
        client_id: str | None = None
        client_secret: str | None = None

        if oauth_metadata.get("client_id_metadata_document_supported"):
            client_id = f"{settings.dashboard_url}/api/v1/mcp/oauth/client-metadata"
        elif oauth_metadata.get("registration_endpoint"):
            registration = await register_oauth_client(
                oauth_metadata["registration_endpoint"],
                settings.mcp_oauth_redirect_uri,
            )
            client_id = registration["client_id"]
            client_secret = registration.get("client_secret")

        if not client_id:
            raise MCPError(-1, "Authorization server does not support client metadata or registration")

        state = secrets.token_urlsafe(32)
        code_verifier, code_challenge = generate_pkce_pair()
        resource = canonicalize_resource_url(body.server_url)
        flow_metadata = {
            "server_url": body.server_url,
            "name": body.name or "",
            "resource": resource,
            "oauth_metadata": oauth_metadata,
            "redirect_uri": settings.mcp_oauth_redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": code_verifier,
        }
        db.add(OAuthState(
            state=state,
            user_id=body.user_id,
            persona_id=body.persona_id,
            metadata_json=json.dumps(flow_metadata),
        ))
        await db.commit()

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": settings.mcp_oauth_redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "resource": resource,
        }
        scopes = oauth_metadata.get("scopes_supported") or []
        if scopes:
            params["scope"] = " ".join(scopes)
        return {"auth_url": f"{oauth_metadata['authorization_endpoint']}?{urlencode(params)}"}
    except MCPError as exc:
        raise HTTPException(400, {"error": "oauth_discovery_failed", "detail": str(exc)})
    except Exception as exc:
        logger.error("MCP OAuth initiate failed url=%s error=%s", body.server_url, exc, exc_info=True)
        raise HTTPException(400, {"error": "oauth_discovery_failed", "detail": str(exc)})


@router.post("/mcp/oauth/callback")
async def complete_mcp_oauth(body: MCPOAuthCallbackInput, db: AsyncSession = Depends(_get_db)):
    result = await db.execute(select(OAuthState).where(OAuthState.state == body.state))
    state_row = result.scalar_one_or_none()
    if not state_row:
        raise HTTPException(400, "Invalid OAuth state")

    try:
        metadata = json.loads(state_row.metadata_json or "{}")
        oauth_metadata = metadata.get("oauth_metadata") or {}
        token_data = await exchange_oauth_code(
            token_endpoint=oauth_metadata["token_endpoint"],
            code=body.code,
            redirect_uri=metadata["redirect_uri"],
            client_id=metadata["client_id"],
            code_verifier=metadata["code_verifier"],
            resource=metadata["resource"],
            client_secret=metadata.get("client_secret"),
        )
        access_token = token_data.get("access_token", "")
        if not access_token:
            raise MCPError(-1, "Token exchange did not return access_token")

        raw_tools = await discover_tools(
            metadata["server_url"], auth_headers=build_auth_headers("bearer", access_token)
        )
        valid_tools, errors = validate_tool_list(raw_tools)
        if errors and not valid_tools:
            raise HTTPException(400, {"error": "tool_validation_failed", "detail": errors})

        await _replace_existing_connection(
            db,
            user_id=state_row.user_id,
            persona_id=state_row.persona_id,
            server_url=metadata["server_url"],
        )

        tool_names = [t.get("name", "") for t in valid_tools if t.get("name")]
        conn = Connection(
            user_id=state_row.user_id,
            provider="mcp",
            execution_type="mcp",
            status="connected",
            persona_id=state_row.persona_id,
            mcp_server_url=metadata["server_url"],
            mcp_tools_json=json.dumps(valid_tools),
            granted_scopes=" ".join(tool_names),
        )
        db.add(conn)
        await db.flush()

        refresh_payload = None
        if token_data.get("refresh_token"):
            refresh_payload = json.dumps({
                "refresh_token": token_data["refresh_token"],
                "token_endpoint": oauth_metadata["token_endpoint"],
                "client_id": metadata["client_id"],
                "client_secret": metadata.get("client_secret"),
                "resource": metadata["resource"],
            })
        db.add(OAuthToken(
            connection_id=conn.id,
            access_token_enc=encrypt(access_token),
            refresh_token_enc=encrypt(refresh_payload) if refresh_payload else None,
            expires_at=compute_expires_at(token_data.get("expires_in")),
        ))
        await db.execute(delete(OAuthState).where(OAuthState.state == body.state))
        await db.commit()

        result = {
            "id": conn.id,
            "provider": "mcp",
            "tools": tool_names,
            "status": "connected",
            "persona_id": state_row.persona_id,
        }
        if errors:
            result["warnings"] = errors
        return result
    except HTTPException:
        await db.execute(delete(OAuthState).where(OAuthState.state == body.state))
        await db.commit()
        raise
    except Exception as exc:
        await db.execute(delete(OAuthState).where(OAuthState.state == body.state))
        await db.commit()
        logger.error("MCP OAuth callback failed state=%s error=%s", body.state, exc, exc_info=True)
        raise HTTPException(400, {"error": "token_exchange_failed", "detail": str(exc)})


@router.post("/mcp/rediscover/{connection_id}")
async def rediscover_tools(connection_id: str, db: AsyncSession = Depends(_get_db)):
    result = await db.execute(select(Connection).where(Connection.id == connection_id))
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")
    if conn.execution_type != "mcp":
        raise HTTPException(400, "Not an MCP connection")

    auth_headers = await _get_connection_auth_headers(conn, db)
    try:
        raw_tools = await discover_tools(conn.mcp_server_url, auth_headers=auth_headers)
    except Exception as exc:
        raise HTTPException(400, {"error": "discovery_failed", "detail": str(exc)})

    valid_tools, errors = validate_tool_list(raw_tools)
    tool_names = [t.get("name", "") for t in valid_tools if t.get("name")]
    conn.mcp_tools_json = json.dumps(valid_tools)
    conn.granted_scopes = " ".join(tool_names)
    await db.commit()

    result_data = {"tools": tool_names}
    if errors:
        result_data["warnings"] = errors
    return result_data


@router.post("/tools/mcp/execute")
async def execute_mcp_tool(body: MCPExecuteInput, db: AsyncSession = Depends(_get_db)):
    result = await db.execute(
        select(Connection).where(Connection.id == body.connection_id, Connection.user_id == body.user_id)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(404, "Connection not found")
    if conn.execution_type != "mcp":
        raise HTTPException(400, "Not an MCP connection")
    if conn.status != "connected":
        raise HTTPException(400, f"Connection status is '{conn.status}', expected 'connected'")

    auth_headers = await _get_connection_auth_headers(conn, db)
    try:
        tool_result = await mcp_call(
            conn.mcp_server_url,
            "tools/call",
            params={"name": body.tool_name, "arguments": body.arguments},
            headers=auth_headers,
        )
    except MCPError as exc:
        logger.warning(
            "MCP tool error connection_id=%s tool=%s error=%s",
            body.connection_id, body.tool_name, exc,
        )
        return {
            "error": "mcp_execution_failed",
            "detail": str(exc),
            "user_message": f"The MCP server returned an error for '{body.tool_name}': {exc.message}",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "MCP execution failed connection_id=%s tool=%s error=%s",
            body.connection_id, body.tool_name, exc, exc_info=True,
        )
        return {
            "error": "mcp_execution_failed",
            "detail": str(exc),
            "user_message": f"Something went wrong while running '{body.tool_name}'. Please try again.",
        }
    return {"result": tool_result}


async def _get_connection_auth_headers(conn: Connection, db: AsyncSession) -> dict | None:
    token_result = await db.execute(select(OAuthToken).where(OAuthToken.connection_id == conn.id))
    token = token_result.scalar_one_or_none()
    if not token:
        return None

    if conn.provider == "mcp" and token.refresh_token_enc:
        access_token = await _get_fresh_mcp_token(conn, token, db)
        return build_auth_headers("bearer", access_token)

    try:
        decrypted = decrypt(token.access_token_enc)
        return build_auth_headers("api_key", decrypted)
    except Exception as exc:
        logger.warning("Failed to decrypt credentials connection_id=%s error=%s", conn.id, exc)
        return None


async def _get_fresh_mcp_token(conn: Connection, token: OAuthToken, db: AsyncSession) -> str:
    now = datetime.now(timezone.utc)
    expires_at = token.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if not expires_at or expires_at > now:
        return decrypt(token.access_token_enc)

    refresh_meta = json.loads(decrypt(token.refresh_token_enc))
    if not refresh_meta.get("refresh_token"):
        conn.status = "needs_reauth"
        await db.commit()
        raise HTTPException(401, "MCP connection needs reauthorization")

    try:
        refreshed = await refresh_oauth_token(
            token_endpoint=refresh_meta["token_endpoint"],
            refresh_token=refresh_meta["refresh_token"],
            client_id=refresh_meta["client_id"],
            resource=refresh_meta["resource"],
            client_secret=refresh_meta.get("client_secret"),
        )
        token.access_token_enc = encrypt(refreshed["access_token"])
        token.expires_at = compute_expires_at(refreshed.get("expires_in"))
        if refreshed.get("refresh_token"):
            refresh_meta["refresh_token"] = refreshed["refresh_token"]
            token.refresh_token_enc = encrypt(json.dumps(refresh_meta))
        conn.status = "connected"
        await db.commit()
        return refreshed["access_token"]
    except Exception as exc:
        logger.error("MCP token refresh failed conn=%s error=%s", conn.id, exc)
        conn.status = "needs_reauth"
        await db.commit()
        raise HTTPException(401, "Your MCP connection needs reauthorization.")

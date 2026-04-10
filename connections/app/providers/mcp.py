"""
MCP Provider: JSON-RPC 2.0 client, OAuth discovery, and validation.

Implements the MCP (Model Context Protocol) provider for connecting to remote
MCP servers. Handles the initialization handshake, OAuth 2.1 metadata
discovery, PKCE helpers, tool discovery via tools/list, tool execution via
tools/call, and tool name validation per D-14 rules.

Auth modes: Bearer token (OAuth), API key (sent as Bearer), or no auth.
Limits: 30s timeout (D-12), 1MB response (D-12), 20 tools per connection (D-14).
"""
import base64
import json
import hashlib
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, List
from urllib.parse import urlparse

import httpx

from app.providers.base import AbstractProvider, CapabilityManifest

logger = logging.getLogger(__name__)

MAX_TOOLS_PER_CONNECTION = 20
MAX_TOOL_NAME_LENGTH = 64
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

RESERVED_TOOL_NAMES: frozenset[str] = frozenset({
    "web_search",
    "read_emails",
    "send_email",
    "list_events",
    "create_event",
    "create_reminder",
    "list_tasks",
    "upsert_profile",
    "update_user_field",
    "create_goal",
    "update_goal",
    "list_goals",
    "update_setting",
})

MAX_RESPONSE_BYTES = 1_048_576
MCP_TIMEOUT = 30.0

_request_id = 0


def _next_id() -> int:
    global _request_id
    _request_id += 1
    return _request_id


class MCPError(Exception):
    """Error returned by an MCP server or its OAuth metadata endpoints."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"MCP error {code}: {message}")


class MCPProvider(AbstractProvider):
    """Provider for remote MCP servers."""

    name = "mcp"
    auth_url = ""
    token_url = ""
    scopes: List[str] = []

    def capability_manifest(self, granted_scopes: List[str]) -> CapabilityManifest:
        return CapabilityManifest(provider="mcp", tools=list(granted_scopes))


def canonicalize_resource_url(server_url: str) -> str:
    """Return the canonical resource indicator for the MCP server URL."""
    parsed = urlparse(server_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid MCP server URL")
    path = parsed.path or ""
    if path == "/":
        path = ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def validate_tool_name(name: str) -> tuple[bool, str]:
    if not name:
        return False, "Tool name is empty"
    if len(name) > MAX_TOOL_NAME_LENGTH:
        return False, f"Tool name exceeds {MAX_TOOL_NAME_LENGTH} characters"
    if not TOOL_NAME_PATTERN.match(name):
        return False, "Tool name contains invalid characters (only A-Z, a-z, 0-9, _, - allowed)"
    if name in RESERVED_TOOL_NAMES:
        return False, f"Tool name '{name}' conflicts with built-in tool"
    return True, ""


def validate_tool_list(tools: list[dict]) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    valid: list[dict] = []

    if len(tools) > MAX_TOOLS_PER_CONNECTION:
        errors.append(
            f"Server exposes {len(tools)} tools, maximum is {MAX_TOOLS_PER_CONNECTION}"
        )
        tools = tools[:MAX_TOOLS_PER_CONNECTION]

    seen: set[str] = set()
    for tool in tools:
        name = tool.get("name", "")
        ok, reason = validate_tool_name(name)
        if not ok:
            errors.append(f"Tool '{name}': {reason}")
            continue
        if name in seen:
            errors.append(f"Tool '{name}': duplicate name within this connection")
            continue
        seen.add(name)
        valid.append(tool)

    return valid, errors


def generate_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    return verifier, challenge


def _well_known_urls(base_url: str, suffix: str) -> list[str]:
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = (parsed.path or "").strip("/")
    urls: list[str] = []
    if path:
        urls.append(f"{origin}/.well-known/{suffix}/{path}")
    urls.append(f"{origin}/.well-known/{suffix}")
    return urls


async def _fetch_metadata_document(candidates: list[str]) -> dict[str, Any]:
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=MCP_TIMEOUT, follow_redirects=True) as client:
        for url in candidates:
            try:
                resp = await client.get(url, headers={"Accept": "application/json"})
                if resp.status_code >= 400:
                    last_error = httpx.HTTPStatusError(
                        f"{resp.status_code} for {url}", request=resp.request, response=resp
                    )
                    continue
                data = resp.json()
                if isinstance(data, dict):
                    return data
                last_error = ValueError(f"Metadata at {url} was not a JSON object")
            except Exception as exc:
                last_error = exc
    raise MCPError(-1, f"OAuth metadata discovery failed: {last_error or 'no metadata found'}")


async def discover_oauth_metadata(server_url: str) -> dict[str, Any]:
    resource_metadata = await _fetch_metadata_document(
        _well_known_urls(server_url, "oauth-protected-resource")
    )
    auth_servers = resource_metadata.get("authorization_servers") or []
    issuer = auth_servers[0] if auth_servers else canonicalize_resource_url(server_url)

    metadata = await _fetch_metadata_document(
        _well_known_urls(issuer, "oauth-authorization-server")
        + _well_known_urls(issuer, "openid-configuration")
    )

    methods = metadata.get("code_challenge_methods_supported")
    if not isinstance(methods, list) or "S256" not in methods:
        raise MCPError(-1, "Authorization server does not advertise PKCE S256 support")

    auth_endpoint = metadata.get("authorization_endpoint")
    token_endpoint = metadata.get("token_endpoint")
    if not auth_endpoint or not token_endpoint:
        raise MCPError(-1, "Authorization server metadata missing authorization or token endpoint")

    return {
        "issuer": metadata.get("issuer") or issuer,
        "authorization_endpoint": auth_endpoint,
        "token_endpoint": token_endpoint,
        "registration_endpoint": metadata.get("registration_endpoint"),
        "client_id_metadata_document_supported": bool(
            metadata.get("client_id_metadata_document_supported")
        ),
        "scopes_supported": metadata.get("scopes_supported") or [],
    }


async def register_oauth_client(registration_endpoint: str, redirect_uri: str) -> dict[str, Any]:
    payload = {
        "client_name": "Operator MCP Client",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    async with httpx.AsyncClient(timeout=MCP_TIMEOUT, follow_redirects=True) as client:
        resp = await client.post(registration_endpoint, json=payload)
        resp.raise_for_status()
        data = resp.json()
    if not data.get("client_id"):
        raise MCPError(-1, "Dynamic client registration succeeded without returning client_id")
    return data


async def exchange_oauth_code(
    token_endpoint: str,
    code: str,
    redirect_uri: str,
    client_id: str,
    code_verifier: str,
    resource: str,
    client_secret: str | None = None,
) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
        "resource": resource,
    }
    if client_secret:
        data["client_secret"] = client_secret
    async with httpx.AsyncClient(timeout=MCP_TIMEOUT, follow_redirects=True) as client:
        resp = await client.post(token_endpoint, data=data)
        resp.raise_for_status()
        return resp.json()


async def refresh_oauth_token(
    token_endpoint: str,
    refresh_token: str,
    client_id: str,
    resource: str,
    client_secret: str | None = None,
) -> dict[str, Any]:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "resource": resource,
    }
    if client_secret:
        data["client_secret"] = client_secret
    async with httpx.AsyncClient(timeout=MCP_TIMEOUT, follow_redirects=True) as client:
        resp = await client.post(token_endpoint, data=data)
        resp.raise_for_status()
        return resp.json()


def compute_expires_at(expires_in: int | None) -> datetime | None:
    if not expires_in:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=expires_in)


async def mcp_call(
    url: str,
    method: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = MCP_TIMEOUT,
    session_id: str | None = None,
    return_session_id: bool = False,
) -> dict | tuple[dict, str | None]:
    req_id = _next_id()
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        payload["params"] = params

    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if headers:
        req_headers.update(headers)
    if session_id:
        req_headers["Mcp-Session-Id"] = session_id

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=req_headers)

    content_length = len(resp.content)
    if content_length > MAX_RESPONSE_BYTES:
        raise MCPError(-1, f"Response too large: {content_length} bytes (max {MAX_RESPONSE_BYTES})")

    # Extract error details from response body before raising on HTTP errors.
    # MCP servers often return JSON error details in 4xx/5xx responses.
    if resp.status_code >= 400:
        error_detail = f"HTTP {resp.status_code}"
        try:
            err_body = resp.json()
            if isinstance(err_body, dict):
                # JSON-RPC error or plain error object
                err_msg = (
                    err_body.get("error", {}).get("message")
                    if isinstance(err_body.get("error"), dict)
                    else err_body.get("message") or err_body.get("error") or str(err_body)
                )
                if err_msg:
                    error_detail = f"HTTP {resp.status_code}: {str(err_msg)[:200]}"
        except Exception:
            if resp.text:
                error_detail = f"HTTP {resp.status_code}: {resp.text[:200]}"
        raise MCPError(resp.status_code, error_detail)
    response_session_id = resp.headers.get("Mcp-Session-Id")
    data: dict[str, Any] = {}
    if resp.content:
        try:
            parsed = resp.json()
        except Exception as exc:
            content_type = resp.headers.get("Content-Type", "")
            if "text/event-stream" in content_type:
                for line in resp.text.splitlines():
                    if not line.startswith("data:"):
                        continue
                    payload_text = line[len("data:"):].strip()
                    if not payload_text:
                        continue
                    try:
                        maybe_json = json.loads(payload_text)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(maybe_json, dict):
                        parsed = maybe_json
                        break
                else:
                    raise MCPError(-1, f"Invalid event-stream response from MCP server: {exc}") from exc
            else:
                raise MCPError(-1, f"Invalid JSON response from MCP server: {exc}") from exc
        if not isinstance(parsed, dict):
            raise MCPError(-1, "MCP response body must be a JSON object")
        data = parsed
    if "error" in data:
        err = data["error"]
        raise MCPError(err.get("code", -1), err.get("message", "Unknown error"))
    result = data.get("result", {})
    if return_session_id:
        return result, response_session_id
    return result


async def mcp_notify(
    url: str,
    method: str,
    params: dict | None = None,
    headers: dict | None = None,
    session_id: str | None = None,
) -> None:
    payload = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params

    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if headers:
        req_headers.update(headers)
    if session_id:
        req_headers["Mcp-Session-Id"] = session_id

    async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
        resp = await client.post(url, json=payload, headers=req_headers)
        resp.raise_for_status()


async def discover_tools(server_url: str, auth_headers: dict | None = None) -> list[dict]:
    t0 = time.monotonic()
    session_id: str | None = None
    try:
        _, session_id = await mcp_call(
            server_url,
            "initialize",
            params={
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "Operator", "version": "1.0.0"},
            },
            headers=auth_headers,
            return_session_id=True,
        )
        await mcp_notify(
            server_url,
            "notifications/initialized",
            headers=auth_headers,
            session_id=session_id,
        )
    except Exception as exc:
        logger.warning(
            "MCP init handshake failed url=%s error=%s — trying tools/list directly",
            server_url,
            exc,
        )

    result = await mcp_call(server_url, "tools/list", headers=auth_headers, session_id=session_id)
    tools = result.get("tools", [])
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info("MCP discovery url=%s tools=%d latency_ms=%d", server_url, len(tools), elapsed_ms)
    return tools


def build_auth_headers(auth_type: str, credentials: str | None = None) -> dict:
    if auth_type == "bearer" and credentials:
        return {"Authorization": f"Bearer {credentials}"}
    if auth_type == "api_key" and credentials:
        return {"Authorization": f"Bearer {credentials}"}
    return {}

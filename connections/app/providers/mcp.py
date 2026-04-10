"""
MCP Provider: JSON-RPC 2.0 client, tool discovery, and validation.

Implements the MCP (Model Context Protocol) provider for connecting to remote
MCP servers. Handles the initialization handshake, tool discovery via tools/list,
tool execution via tools/call, and tool name validation per D-14 rules.

Auth modes: Bearer token (OAuth), API key (sent as Bearer), or no auth.
Limits: 30s timeout (D-12), 1MB response (D-12), 20 tools per connection (D-14).
"""
import json
import logging
import re
import time
from typing import List

import httpx

from app.providers.base import AbstractProvider, CapabilityManifest

logger = logging.getLogger(__name__)

# ── Validation constants (D-12, D-14) ──────────────────────────────────────

MAX_TOOLS_PER_CONNECTION = 20
MAX_TOOL_NAME_LENGTH = 64
TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")

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

MAX_RESPONSE_BYTES = 1_048_576  # 1MB per D-12
MCP_TIMEOUT = 30.0              # seconds per D-12

# ── JSON-RPC request counter ───────────────────────────────────────────────

_request_id = 0


def _next_id() -> int:
    global _request_id
    _request_id += 1
    return _request_id


# ── Exceptions ─────────────────────────────────────────────────────────────

class MCPError(Exception):
    """Error returned by an MCP server via JSON-RPC error response."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"MCP error {code}: {message}")


# ── MCPProvider ────────────────────────────────────────────────────────────

class MCPProvider(AbstractProvider):
    """Provider for remote MCP servers."""

    name = "mcp"
    auth_url = ""      # Set dynamically per-server
    token_url = ""     # Set dynamically per-server
    scopes: List[str] = []

    def capability_manifest(self, granted_scopes: List[str]) -> CapabilityManifest:
        """For MCP, granted_scopes contains stored tool names."""
        return CapabilityManifest(provider="mcp", tools=list(granted_scopes))


# ── Tool name validation (D-14) ───────────────────────────────────────────

def validate_tool_name(name: str) -> tuple[bool, str]:
    """Validate a single tool name per D-14 rules.

    Returns (True, "") on success, (False, reason) on failure.
    """
    if not name:
        return False, "Tool name is empty"
    if len(name) > MAX_TOOL_NAME_LENGTH:
        return False, f"Tool name exceeds {MAX_TOOL_NAME_LENGTH} characters"
    if not TOOL_NAME_PATTERN.match(name):
        return False, "Tool name contains invalid characters (only A-Z, a-z, 0-9, _ allowed)"
    if name in RESERVED_TOOL_NAMES:
        return False, f"Tool name '{name}' conflicts with built-in tool"
    return True, ""


def validate_tool_list(tools: list[dict]) -> tuple[list[dict], list[str]]:
    """Validate a list of tool dicts from MCP tools/list response.

    Returns (valid_tools, errors). Enforces MAX_TOOLS_PER_CONNECTION limit.
    """
    errors: list[str] = []
    valid: list[dict] = []

    if len(tools) > MAX_TOOLS_PER_CONNECTION:
        errors.append(
            f"Server exposes {len(tools)} tools, maximum is {MAX_TOOLS_PER_CONNECTION}"
        )
        tools = tools[:MAX_TOOLS_PER_CONNECTION]

    for tool in tools:
        name = tool.get("name", "")
        ok, reason = validate_tool_name(name)
        if ok:
            valid.append(tool)
        else:
            errors.append(f"Tool '{name}': {reason}")

    return valid, errors


# ── JSON-RPC 2.0 client ───────────────────────────────────────────────────

async def mcp_call(
    url: str,
    method: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = MCP_TIMEOUT,
) -> dict:
    """Send a JSON-RPC 2.0 request and return the result.

    Raises MCPError on JSON-RPC error responses, httpx errors on transport failure.
    """
    req_id = _next_id()
    payload = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params

    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if headers:
        req_headers.update(headers)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=req_headers)

    # Check response size before parsing
    content_length = len(resp.content)
    if content_length > MAX_RESPONSE_BYTES:
        raise MCPError(-1, f"Response too large: {content_length} bytes (max {MAX_RESPONSE_BYTES})")

    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        err = data["error"]
        raise MCPError(err.get("code", -1), err.get("message", "Unknown error"))

    return data.get("result", {})


async def mcp_notify(
    url: str,
    method: str,
    params: dict | None = None,
    headers: dict | None = None,
) -> None:
    """Send a JSON-RPC 2.0 notification (no id, no response expected)."""
    payload = {
        "jsonrpc": "2.0",
        "method": method,
    }
    if params is not None:
        payload["params"] = params

    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if headers:
        req_headers.update(headers)

    async with httpx.AsyncClient(timeout=MCP_TIMEOUT) as client:
        await client.post(url, json=payload, headers=req_headers)


# ── Tool discovery with initialization handshake ───────────────────────────

async def discover_tools(
    server_url: str,
    auth_headers: dict | None = None,
) -> list[dict]:
    """Perform MCP initialization handshake and discover available tools.

    Steps:
    1. POST initialize with protocol version and client info
    2. POST notifications/initialized notification
    3. POST tools/list to get available tools

    If initialization fails, falls back to calling tools/list directly.
    Returns list of tool dicts (name, description, inputSchema).
    """
    t0 = time.monotonic()

    try:
        # Step 1: Initialize
        await mcp_call(
            server_url,
            "initialize",
            params={
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "Operator", "version": "1.0.0"},
            },
            headers=auth_headers,
        )

        # Step 2: Notify initialized
        await mcp_notify(
            server_url,
            "notifications/initialized",
            headers=auth_headers,
        )
    except Exception as exc:
        logger.warning(
            "MCP init handshake failed url=%s error=%s — trying tools/list directly",
            server_url, exc,
        )

    # Step 3: List tools
    result = await mcp_call(
        server_url,
        "tools/list",
        headers=auth_headers,
    )

    tools = result.get("tools", [])
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "MCP discovery url=%s tools=%d latency_ms=%d",
        server_url, len(tools), elapsed_ms,
    )
    return tools


# ── Auth header builder ────────────────────────────────────────────────────

def build_auth_headers(auth_type: str, credentials: str | None = None) -> dict:
    """Build HTTP headers for MCP server authentication.

    auth_type: "bearer", "api_key", or "none"
    credentials: the token or API key value (decrypted)
    """
    if auth_type == "bearer" and credentials:
        return {"Authorization": f"Bearer {credentials}"}
    if auth_type == "api_key" and credentials:
        return {"Authorization": f"Bearer {credentials}"}
    return {}

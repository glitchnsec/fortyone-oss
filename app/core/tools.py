"""
Tool registry — loads subagent definitions from config/subagents.yaml.

Provides:
  - load_subagents(): parsed YAML config
  - get_tool_schemas(): OpenAI-format tool definitions for LLM tool calling
  - get_tool_handler_path(name): resolve dotted handler path
  - get_tool_risk(name): risk level lookup (low/medium/high)
  - TOOL_RISK: dict mapping tool name -> risk level

Per D-14: subagents are config-driven. New capabilities added by YAML entry,
no code changes required.
"""
import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "subagents.yaml"

# Module-level cache
_subagents: list[dict] | None = None
_tool_schemas: list[dict] | None = None
_tool_handlers: dict[str, str] | None = None

# Risk classification per tool (populated on first load)
TOOL_RISK: dict[str, str] = {}

# update_setting requires confirmation (D-10)
TOOL_RISK["update_setting"] = "medium"

# Text-based settings tool (D-10, D-11) -- added directly (not from YAML)
# because this is a core system tool, not a subagent capability.
UPDATE_SETTING_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_setting",
        "description": (
            "Update a user setting or perform a task/goal action via text. "
            "Covers: proactive preferences (quiet_hours, max_daily_messages, category toggles), "
            "task management (complete, archive, delete a task by name/description), "
            "goal management (update status to completed/archived), "
            "user profile fields (name, timezone, assistant_name, personality_notes), "
            "assistant profile (name, personality). "
            "For complex settings like OAuth connections or persona creation, "
            "return a dashboard link instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["proactive", "task", "goal", "profile", "assistant"],
                    "description": "Which domain this setting belongs to",
                },
                "action": {
                    "type": "string",
                    "enum": ["update", "complete", "archive", "delete", "enable", "disable"],
                    "description": "What to do",
                },
                "target": {
                    "type": "string",
                    "description": (
                        "Setting key or item identifier. "
                        "For proactive scope: 'enabled' (global on/off toggle), "
                        "'quiet_hours_start', 'quiet_hours_end', 'max_daily_messages', "
                        "'preferred_channel', or a category name. "
                        "Use target='enabled' with action='disable' to turn off ALL proactive messages. "
                        "For task/goal scope: the task or goal title to match."
                    ),
                },
                "value": {
                    "description": "New value. Type depends on setting: number for hours, string for names, boolean for toggles. Omit for complete/archive/delete actions.",
                },
            },
            "required": ["scope", "action", "target"],
        },
    },
}


def load_subagents() -> list[dict]:
    """Load and cache subagent definitions from YAML config."""
    global _subagents
    if _subagents is not None:
        return _subagents

    config_path = os.environ.get("SUBAGENT_CONFIG", str(_CONFIG_PATH))
    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
        _subagents = data.get("subagents", [])
        logger.info("Loaded %d subagents from %s", len(_subagents), config_path)
    except FileNotFoundError:
        logger.warning("Subagent config not found at %s — no tools available", config_path)
        _subagents = []

    # Populate TOOL_RISK on load
    for agent in _subagents:
        for tool in agent.get("tools", []):
            TOOL_RISK[tool["name"]] = tool.get("risk_level", "low")

    # Always ensure built-in tool risks are set (survives cache clears in tests)
    TOOL_RISK.setdefault("update_setting", "medium")

    return _subagents


def get_tool_schemas() -> list[dict]:
    """Return OpenAI-format tool definitions for all registered tools."""
    global _tool_schemas
    if _tool_schemas is not None:
        return _tool_schemas

    subagents = load_subagents()
    schemas: list[dict[str, Any]] = []
    for agent in subagents:
        for tool in agent.get("tools", []):
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                },
            })

    # Append core system tools not defined in YAML
    schemas.append(UPDATE_SETTING_SCHEMA)

    _tool_schemas = schemas
    logger.info("Generated %d tool schemas", len(schemas))
    return schemas


def get_tool_handler_path(tool_name: str) -> str | None:
    """Return the dotted module path for a tool's handler function."""
    global _tool_handlers
    if _tool_handlers is None:
        _tool_handlers = {}
        for agent in load_subagents():
            for tool in agent.get("tools", []):
                _tool_handlers[tool["name"]] = tool.get("handler", "")

    return _tool_handlers.get(tool_name)


def get_tool_risk(tool_name: str) -> str:
    """Return risk level for a tool: low, medium, or high."""
    if not TOOL_RISK:
        load_subagents()
    return TOOL_RISK.get(tool_name, "high")  # Default to high if unknown


async def get_mcp_tool_schemas(user_id: str, persona_id: str | None = None) -> list[dict]:
    """Return OpenAI-format tool schemas for a user's MCP connections.

    Fetches MCP connections from the connections service, parses the
    mcp_tools list from each, and builds namespaced tool definitions
    so the LLM can invoke MCP tools alongside built-in ones.

    Tool names are namespaced as ``mcp_{connection_id_short}_{tool_name}``
    to avoid collisions between connections.  Each MCP tool is registered
    as "medium" risk in TOOL_RISK (external tools).

    On any error (connections service down, parse failure) returns an empty
    list — graceful degradation.
    """
    import httpx
    from app.config import get_settings

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

        schemas: list[dict] = []
        for conn in connections:
            if conn.get("execution_type") != "mcp":
                continue
            if conn.get("status") != "connected":
                continue

            conn_id = conn.get("id", "")
            conn_id_short = conn_id[:8]
            mcp_tools = conn.get("mcp_tools", [])

            for tool in mcp_tools:
                tool_name = tool.get("name", "")
                if not tool_name:
                    continue
                namespaced_name = f"mcp_{conn_id_short}_{tool_name}"

                schemas.append({
                    "type": "function",
                    "function": {
                        "name": namespaced_name,
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                    },
                })

                # Register MCP tools as medium risk (external tools)
                TOOL_RISK[namespaced_name] = "medium"

        return schemas

    except Exception as exc:
        logger.warning("get_mcp_tool_schemas failed user=%s error=%s", user_id[:8] if user_id else "?", exc)
        return []


async def get_cross_persona_tool_hints(
    user_id: str,
    current_persona_id: str | None,
    current_persona_name: str = "shared",
) -> str:
    """Return a text block listing tools available on OTHER personas.

    Used to inject into the system prompt so the LLM can suggest persona
    switching when the user asks for a capability that exists elsewhere.
    Returns empty string if no cross-persona tools exist.
    """
    import httpx
    from app.config import get_settings

    if not user_id:
        return ""

    try:
        settings = get_settings()
        url = f"{settings.connections_service_url}/connections/{user_id}"

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)  # No persona filter = all connections
            resp.raise_for_status()

        data = resp.json()
        connections = data.get("connections", [])

        # Group tools by persona, excluding current persona
        other_persona_tools: dict[str, list[str]] = {}  # persona_id -> tool descriptions
        persona_names: dict[str, str] = {}  # persona_id -> display name

        for conn in connections:
            conn_persona_id = conn.get("persona_id")
            # Skip connections on the current persona (already available)
            if conn_persona_id == current_persona_id:
                continue
            if conn.get("status") != "connected":
                continue

            provider = conn.get("provider", "unknown")
            tools_on_conn: list[str] = []

            if conn.get("execution_type") == "mcp":
                for tool in conn.get("mcp_tools", []):
                    name = tool.get("name", "")
                    desc = tool.get("description", "")
                    if name:
                        label = name.replace("_", " ").replace("-", " ")
                        tools_on_conn.append(f"{label}" + (f" — {desc[:60]}" if desc else ""))
            else:
                # Native connections (Google etc.)
                cap_tools = conn.get("capabilities", {}).get("tools", [])
                for t in cap_tools:
                    tools_on_conn.append(t.replace("_", " "))

            if tools_on_conn:
                pid = conn_persona_id or "shared"
                other_persona_tools.setdefault(pid, []).extend(tools_on_conn)

        if not other_persona_tools:
            return ""

        # Build the hint text — we need persona names. Fetch them.
        try:
            from app.database import AsyncSessionLocal
            from app.memory.models import Persona
            from sqlalchemy import select as sa_select
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    sa_select(Persona).where(Persona.user_id == user_id)
                )
                for p in result.scalars().all():
                    persona_names[str(p.id)] = p.name
        except Exception:
            pass

        lines = [
            "CROSS-PERSONA TOOLS: The following tools are available on OTHER personas "
            "(not the current one). If the user's request matches one of these, suggest "
            "they switch persona — e.g., 'That tool is available on your Personal persona. "
            "Want me to use that one instead?'",
        ]
        for pid, tool_list in other_persona_tools.items():
            name = persona_names.get(pid, pid)
            tools_str = "; ".join(tool_list[:8])  # Cap at 8 to save tokens
            lines.append(f"  • {name} persona: {tools_str}")

        return "\n".join(lines)

    except Exception as exc:
        logger.warning(
            "cross_persona_hints failed user=%s error=%s",
            user_id[:8] if user_id else "?", exc,
        )
        return ""


async def get_custom_agent_schemas(user_id: str) -> list[dict]:
    """Return OpenAI-format tool schemas for a user's enabled custom agents.

    Called per-request in the worker — not cached globally since custom agents
    are per-user. Tool names are prefixed with 'custom_' to avoid collisions
    with built-in tools.
    """
    from app.database import AsyncSessionLocal
    from app.memory.models import CustomAgent
    from sqlalchemy import select
    import json

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CustomAgent).where(
                CustomAgent.user_id == user_id,
                CustomAgent.enabled == True,
            )
        )
        agents = result.scalars().all()

    schemas = []
    for agent in agents:
        # Tool name: custom_{slugified_name}
        tool_name = f"custom_{agent.name.lower().replace(' ', '_').replace('-', '_')}"

        # Parse parameters schema or use empty object
        params: dict = {"type": "object", "properties": {}}
        if agent.parameters_schema_json:
            try:
                params = json.loads(agent.parameters_schema_json)
            except json.JSONDecodeError:
                pass

        schemas.append({
            "type": "function",
            "function": {
                "name": tool_name,
                "description": agent.description or f"Custom agent: {agent.name}",
                "parameters": params,
            },
        })

        # Register risk level so get_tool_risk works for custom tools
        TOOL_RISK[tool_name] = agent.risk_level or "low"

    return schemas

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

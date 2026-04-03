"""
TDD tests for Phase 4: Proactive Agent + Goal Alignment.

Written BEFORE execution — these tests define the contract that
/gsd:execute-phase 4 must satisfy. They will FAIL (RED) until
the phase is implemented, then PASS (GREEN).

Tests verify:
  - LLM intent classification (AGENT-07): NEEDS_MANAGER routing
  - Tool registry (D-14): subagent YAML config loading + risk levels
  - Data models (AGENT-03, AGENT-06): Goal, ActionLog, PendingAction, UserProfile
  - Safety rails (AGENT-05): rate limit constants, dead man switch
  - Manager dispatch (D-05): MAX_TOOL_ROUNDS, _format_action_description
  - Proactive handlers (AGENT-01, AGENT-04, D-10): importability
  - Scheduler utilities: cron computation

No database or Redis required — pure unit tests.
"""
import pytest


# ─── 1-4. Intent Classification (AGENT-07) ─────────────────────────────────

def test_intent_has_needs_manager():
    """IntentType enum includes NEEDS_MANAGER after Phase 4."""
    from app.core.intent import IntentType
    assert hasattr(IntentType, "NEEDS_MANAGER"), (
        "IntentType missing NEEDS_MANAGER — Plan 04-02 must add it"
    )
    assert IntentType.NEEDS_MANAGER.value == "needs_manager"


def test_greeting_still_regex_fast_path():
    """GREETING intent preserved via regex — zero LLM latency."""
    from app.core.intent import classify_intent, IntentType
    result = classify_intent("hello")
    assert result.type == IntentType.GREETING, (
        f"Expected GREETING, got {result.type}"
    )
    assert result.requires_worker is False


def test_identity_still_regex_fast_path():
    """IDENTITY intent preserved via regex — zero LLM latency."""
    from app.core.intent import classify_intent, IntentType
    result = classify_intent("who are you")
    assert result.type == IntentType.IDENTITY, (
        f"Expected IDENTITY, got {result.type}"
    )


def test_general_messages_route_to_needs_manager():
    """Non-greeting/identity messages route to NEEDS_MANAGER (Plan 04-02)."""
    from app.core.intent import classify_intent, IntentType
    for msg in [
        "remind me to buy milk tomorrow",
        "what is the weather in Austin",
        "schedule a meeting for tomorrow at 3pm",
        "search for good restaurants nearby",
    ]:
        result = classify_intent(msg)
        assert result.type == IntentType.NEEDS_MANAGER, (
            f"Message '{msg}' expected NEEDS_MANAGER, got {result.type}"
        )
        assert result.requires_worker is True


# ─── 5-7. Tool Registry (D-14) ─────────────────────────────────────────────

def test_tool_registry_loads_subagents():
    """load_subagents() returns list of subagent dicts from YAML config."""
    from app.core.tools import load_subagents
    agents = load_subagents()
    assert isinstance(agents, list)
    assert len(agents) >= 4, (
        f"Expected >= 4 subagents (search, email, calendar, task), got {len(agents)}"
    )


def test_tool_schemas_openai_format():
    """get_tool_schemas() returns OpenAI-format tool definitions."""
    from app.core.tools import get_tool_schemas
    schemas = get_tool_schemas()
    assert len(schemas) >= 7, (
        f"Expected >= 7 tool schemas, got {len(schemas)}"
    )
    for s in schemas:
        assert s["type"] == "function", f"Schema type should be 'function': {s}"
        assert "function" in s
        assert "name" in s["function"]
        assert "parameters" in s["function"]


def test_tool_risk_levels():
    """TOOL_RISK maps tool names to correct risk levels (D-04)."""
    from app.core.tools import get_tool_risk
    assert get_tool_risk("web_search") == "low"
    assert get_tool_risk("send_email") == "high"
    assert get_tool_risk("create_event") == "medium"
    assert get_tool_risk("read_emails") == "low"
    # Unknown tools default to high (safety first)
    assert get_tool_risk("nonexistent_tool") == "high"


# ─── 8-11. Data Models (AGENT-03, AGENT-06) ────────────────────────────────

def test_goal_model_importable():
    """Goal model exists with expected columns (Plan 04-01)."""
    from app.memory.models import Goal
    assert hasattr(Goal, "__tablename__")
    assert Goal.__tablename__ == "goals"
    # Check key columns exist
    columns = {c.name for c in Goal.__table__.columns}
    for col in ("id", "user_id", "framework", "title", "status", "version"):
        assert col in columns, f"Goal model missing column: {col}"


def test_action_log_model_importable():
    """ActionLog model exists with expected columns (Plan 04-01)."""
    from app.memory.models import ActionLog
    assert ActionLog.__tablename__ == "action_log"
    columns = {c.name for c in ActionLog.__table__.columns}
    for col in ("id", "user_id", "action_type", "description", "outcome", "trigger"):
        assert col in columns, f"ActionLog model missing column: {col}"


def test_pending_action_model_importable():
    """PendingAction model exists with expected columns (Plan 04-01)."""
    from app.memory.models import PendingAction
    assert PendingAction.__tablename__ == "pending_actions"
    columns = {c.name for c in PendingAction.__table__.columns}
    for col in ("id", "user_id", "action_type", "risk_level", "status", "expires_at"):
        assert col in columns, f"PendingAction model missing column: {col}"


def test_user_profile_model_importable():
    """UserProfile model exists with TELOS sections (Plan 04-01, D-08)."""
    from app.memory.models import UserProfile
    assert UserProfile.__tablename__ == "user_profiles"
    columns = {c.name for c in UserProfile.__table__.columns}
    for col in ("id", "user_id", "section", "label", "content"):
        assert col in columns, f"UserProfile model missing column: {col}"


# ─── 12-13. Manager Dispatch (D-05) ────────────────────────────────────────

def test_manager_max_tool_rounds():
    """Manager enforces a hard limit on tool-calling rounds (Pitfall 2)."""
    from app.tasks.manager import MAX_TOOL_ROUNDS
    assert MAX_TOOL_ROUNDS == 3, (
        f"Expected MAX_TOOL_ROUNDS == 3, got {MAX_TOOL_ROUNDS}"
    )


def test_format_action_description_send_email():
    """_format_action_description formats send_email for confirmation prompt."""
    from app.tasks.manager import _format_action_description
    import json
    desc = _format_action_description(
        "send_email",
        json.dumps({"to": "alice@example.com", "subject": "Meeting notes"}),
    )
    assert "alice@example.com" in desc
    assert "Meeting notes" in desc


def test_format_action_description_create_event():
    """_format_action_description formats create_event for confirmation prompt."""
    from app.tasks.manager import _format_action_description
    import json
    desc = _format_action_description(
        "create_event",
        json.dumps({"summary": "Team standup", "start_time": "2026-04-04T09:00:00"}),
    )
    assert "Team standup" in desc


# ─── 14-17. Safety Rails (AGENT-05, D-11) ──────────────────────────────────

def test_dead_man_switch_threshold():
    """Dead man switch triggers at 5 per hour."""
    from app.core.throttle import DEAD_MAN_SWITCH_THRESHOLD
    assert DEAD_MAN_SWITCH_THRESHOLD == 5


def test_default_max_per_hour():
    """Default proactive rate limit: 2 per hour."""
    from app.core.throttle import DEFAULT_MAX_PER_HOUR
    assert DEFAULT_MAX_PER_HOUR == 2


def test_default_max_per_day():
    """Default proactive rate limit: 5 per day."""
    from app.core.throttle import DEFAULT_MAX_PER_DAY
    assert DEFAULT_MAX_PER_DAY == 5


def test_throttle_functions_importable():
    """All throttle functions exist and are async."""
    import inspect
    from app.core.throttle import (
        check_rate_limit,
        check_idempotency,
        record_proactive_send,
        check_dead_man_switch,
    )
    assert inspect.iscoroutinefunction(check_rate_limit)
    assert inspect.iscoroutinefunction(check_idempotency)
    assert inspect.iscoroutinefunction(record_proactive_send)
    assert inspect.iscoroutinefunction(check_dead_man_switch)


# ─── 18. Proactive Handlers (AGENT-01, AGENT-04, D-10) ─────────────────────

def test_proactive_handlers_importable():
    """All proactive handlers exist and are async (including weekly_digest D-10)."""
    import inspect
    from app.tasks.proactive import (
        handle_morning_briefing,
        handle_evening_recap,
        handle_goal_checkin,
        handle_weekly_digest,
    )
    assert inspect.iscoroutinefunction(handle_morning_briefing)
    assert inspect.iscoroutinefunction(handle_evening_recap)
    assert inspect.iscoroutinefunction(handle_goal_checkin)
    assert inspect.iscoroutinefunction(handle_weekly_digest)


# ─── 19-20. Scheduler Utilities ────────────────────────────────────────────

def test_compute_next_run_returns_float():
    """compute_next_run returns a Unix timestamp (float) for valid cron."""
    from app.core.scheduler_utils import compute_next_run
    result = compute_next_run("0 8 * * *", "America/New_York")
    assert result is not None, "compute_next_run returned None for valid cron"
    assert isinstance(result, float), f"Expected float, got {type(result)}"
    assert result > 0


def test_schedule_user_briefings_returns_morning_and_evening():
    """schedule_user_briefings returns morning + evening job payloads (D-02)."""
    from app.core.scheduler_utils import schedule_user_briefings
    jobs = schedule_user_briefings("test-user-id", "America/New_York")
    assert len(jobs) == 2, f"Expected 2 briefing jobs, got {len(jobs)}"

    types = {j["payload"]["type"] for j in jobs}
    assert "morning_briefing" in types, "Missing morning_briefing job"
    assert "evening_recap" in types, "Missing evening_recap job"

    for j in jobs:
        assert "scheduled_at" in j
        assert isinstance(j["scheduled_at"], float)
        assert j["payload"]["source"] == "scheduler"
        assert j["payload"]["reschedule_at"] is True


# ─── 21-23. Contract Tests (regression guards for bugfixes) ────────────────

def test_build_system_prompt_with_real_context_shape():
    """
    Regression: _build_system_prompt must handle the actual dict shape
    returned by MemoryStore.get_context_standard — memories is {key: value},
    not a list of dicts.

    Bug: commit 398c8d7 — TypeError: unhashable type: 'slice' on memories[:10]
    """
    from app.tasks.manager import _build_system_prompt

    # This is the exact shape get_context_standard returns
    realistic_context = {
        "user": {
            "id": "test-user-123",
            "name": "KC",
            "timezone": "America/New_York",
            "phone": "+15551234567",
            "assistant_name": "Marcus",
            "personality_notes": "Friendly and direct",
        },
        "recent_messages": [
            {"direction": "inbound", "body": "hello", "at": "2026-04-03T10:00:00", "intent": "greeting"},
            {"direction": "outbound", "body": "Hey KC!", "at": "2026-04-03T10:00:01", "intent": None},
        ],
        "memories": {"name": "KC", "timezone": "America/New_York", "work": "software engineer"},
        "active_tasks": [
            {"id": "t1", "title": "Buy groceries", "due_at": None, "type": "reminder"},
        ],
        "message_count": 2,
        "profile_traits": [
            {"section": "preferences", "label": "style", "content": "concise messages"},
        ],
    }

    payload = {
        "context": realistic_context,
        "persona": "shared",
    }

    result = _build_system_prompt(payload)

    assert isinstance(result, str), f"Expected string, got {type(result)}"
    assert "KC" in result, "User name should appear in prompt"
    assert "software engineer" in result, "Memory value should appear in prompt"
    assert "concise messages" in result, "Profile trait should appear in prompt"


def test_build_system_prompt_with_empty_context():
    """_build_system_prompt handles empty/missing context without errors."""
    from app.tasks.manager import _build_system_prompt

    # Minimal payload — no context at all
    result = _build_system_prompt({"context": {}, "persona": "shared"})
    assert isinstance(result, str)
    assert "personal assistant" in result.lower()

    # Context with empty memories dict
    result2 = _build_system_prompt({
        "context": {"memories": {}, "recent_messages": [], "profile_traits": []},
        "persona": "work",
    })
    assert isinstance(result2, str)
    assert "work" in result2.lower()


def test_llm_tools_return_includes_type_field():
    """
    Regression: llm_tools must include "type": "function" in serialized
    tool_calls. Without it, OpenRouter/Anthropic rejects the multi-turn
    tool conversation with "tool_result blocks must have a corresponding
    tool_use block."

    Bug: commit 29d9261 — 400 from OpenRouter when feeding tool results back.

    This test verifies the return shape contract by checking the mock path
    (no LLM key needed).
    """
    import asyncio
    from unittest.mock import patch, MagicMock, AsyncMock

    from app.tasks._llm import llm_tools

    # Create a mock response that simulates an LLM returning tool calls
    mock_choice = MagicMock()
    mock_choice.content = "Let me search for that."
    mock_tc = MagicMock()
    mock_tc.id = "call_abc123"
    mock_tc.function.name = "web_search"
    mock_tc.function.arguments = '{"query": "auto body shops"}'
    mock_choice.tool_calls = [mock_tc]

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=mock_choice)]
    mock_resp.usage = MagicMock(completion_tokens=50)

    async def run():
        with patch("app.tasks._llm._client") as mock_client, \
             patch("app.tasks._llm.get_settings") as mock_settings:
            s = MagicMock()
            s.has_llm = True
            s.llm_model_capable = "test-model"
            mock_settings.return_value = s

            mock_create = AsyncMock(return_value=mock_resp)
            mock_client.return_value.chat.completions.create = mock_create

            result = await llm_tools(
                messages=[{"role": "user", "content": "find auto body shops"}],
                tools=[{"type": "function", "function": {"name": "web_search", "parameters": {}}}],
                mock_text="fallback",
                timeout_s=5.0,
            )

            # Verify tool_calls have the required "type" field
            assert result["tool_calls"] is not None, "Expected tool_calls in response"
            for tc in result["tool_calls"]:
                assert "type" in tc, f"tool_call missing 'type' field: {tc}"
                assert tc["type"] == "function", f"Expected type='function', got {tc['type']}"
                assert "id" in tc
                assert "function" in tc
                assert "name" in tc["function"]
                assert "arguments" in tc["function"]

    asyncio.run(run())

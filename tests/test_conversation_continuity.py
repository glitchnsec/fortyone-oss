"""
Integration tests for Phase 9.1: Tool-Aware Conversation Continuity.

Tests cover:
- CONV-01: Cross-message tool context (reconstruction + sliding window)
- CONV-02: Intermediate content preservation (accumulator logic)
- CONV-03: Task intent persistence (session detection + lifecycle)
"""
import json
import pytest
from datetime import datetime, timezone, timedelta


# ─── CONV-01: Context Reconstruction ────────────────────────────────

class TestReconstructToolMessages:
    """Test _reconstruct_tool_messages converts stored history to OpenAI format."""

    def test_simple_messages_no_metadata(self):
        from app.tasks.manager import _reconstruct_tool_messages
        msgs = [
            {"direction": "inbound", "body": "hello", "metadata": None},
            {"direction": "outbound", "body": "hi", "metadata": None},
        ]
        result = _reconstruct_tool_messages(msgs)
        assert result == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    def test_tool_call_message_expands(self):
        from app.tasks.manager import _reconstruct_tool_messages
        msgs = [
            {"direction": "inbound", "body": "search X", "metadata": None},
            {"direction": "outbound", "body": "Found X", "metadata": {
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}],
                "tool_results": [{"tool_call_id": "c1", "content": "X is at 123 Main St"}],
            }},
        ]
        result = _reconstruct_tool_messages(msgs)
        assert len(result) == 3
        assert result[0] == {"role": "user", "content": "search X"}
        assert result[1]["role"] == "assistant"
        assert "tool_calls" in result[1]
        assert result[1]["content"] == "Found X"
        assert result[2]["role"] == "tool"
        assert result[2]["tool_call_id"] == "c1"

    def test_empty_metadata_treated_as_no_tools(self):
        from app.tasks.manager import _reconstruct_tool_messages
        msgs = [
            {"direction": "outbound", "body": "plain reply", "metadata": {}},
        ]
        result = _reconstruct_tool_messages(msgs)
        assert result == [{"role": "assistant", "content": "plain reply"}]


class TestTokenBudget:
    """Test _apply_token_budget drops whole turns only (D-05)."""

    def test_keeps_all_within_budget(self):
        from app.tasks.manager import _apply_token_budget
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        kept, dropped = _apply_token_budget(msgs, budget=10000)
        assert len(kept) == 2
        assert len(dropped) == 0

    def test_drops_oldest_turn_first(self):
        from app.tasks.manager import _apply_token_budget
        # Two turns, tiny budget that fits only one
        msgs = [
            {"role": "user", "content": "first question " * 50},
            {"role": "assistant", "content": "first answer " * 50},
            {"role": "user", "content": "second"},
            {"role": "assistant", "content": "reply"},
        ]
        kept, dropped = _apply_token_budget(msgs, budget=100)
        # Should keep the most recent turn
        assert kept[-1]["content"] == "reply"
        assert len(dropped) > 0

    def test_never_orphans_tool_calls(self):
        from app.tasks.manager import _apply_token_budget
        # A tool turn must stay atomic
        msgs = [
            {"role": "user", "content": "search"},
            {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
            {"role": "user", "content": "thanks"},
            {"role": "assistant", "content": "welcome"},
        ]
        kept, dropped = _apply_token_budget(msgs, budget=10000)
        # All should be kept with big budget
        assert len(kept) == 5
        # With tiny budget, the tool turn is dropped as a unit
        kept2, dropped2 = _apply_token_budget(msgs, budget=80)
        # Verify no orphaned tool_calls: if assistant with tool_calls is in kept,
        # matching tool result must also be in kept
        for i, m in enumerate(kept2):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                # Next message must be tool result
                assert i + 1 < len(kept2)
                assert kept2[i + 1]["role"] == "tool"


# ─── CONV-02: Intermediate Content Preservation ─────────────────────

class TestIntermediateContent:
    """Test intermediate text accumulation logic."""

    def test_dedup_skips_contained_text(self):
        # If final response already contains intermediate text, don't prepend
        intermediate = "Riley's Fish + Steak is at 130 Wellington St"
        final = "Here are the addresses:\n\nRiley's Fish + Steak is at 130 Wellington St\n\nPasta Privato is at 45 King St"
        # The dedup logic: skip if intermediate is substring of final
        unique = [t for t in [intermediate] if t not in final]
        assert len(unique) == 0, "Intermediate already in final -- should be skipped"

    def test_unique_intermediate_prepended(self):
        intermediate = "I found Riley's address: 130 Wellington St"
        final = "Pasta Privato is at 45 King St"
        unique = [t for t in [intermediate] if t not in final]
        assert len(unique) == 1
        combined = "\n\n".join(unique) + "\n\n" + final
        assert "Riley's" in combined
        assert "Pasta Privato" in combined


# ─── CONV-03: Task Intent Persistence ────────────────────────────────

class TestSessionContinuation:
    """Test continues_active_session heuristic (D-12)."""

    def test_recent_short_followup_continues(self):
        from app.core.intent import continues_active_session
        now = datetime.now(timezone.utc)
        recent = now - timedelta(minutes=2)
        assert continues_active_session("put it under Getting Started", recent, "create doc") is True

    def test_new_intent_signal_breaks_session(self):
        from app.core.intent import continues_active_session
        now = datetime.now(timezone.utc)
        recent = now - timedelta(minutes=1)
        assert continues_active_session("remind me to call mom at 5pm", recent, "create doc") is False
        assert continues_active_session("search for restaurants in Toronto", recent, "create doc") is False
        assert continues_active_session("what's the weather today", recent, "create doc") is False

    def test_stale_session_does_not_continue(self):
        from app.core.intent import continues_active_session
        now = datetime.now(timezone.utc)
        old = now - timedelta(minutes=15)
        assert continues_active_session("yes do it", old, "create doc") is False

    def test_no_session_does_not_continue(self):
        from app.core.intent import continues_active_session
        assert continues_active_session("hello", None, None) is False

    def test_very_recent_long_message_continues(self):
        from app.core.intent import continues_active_session
        now = datetime.now(timezone.utc)
        very_recent = now - timedelta(minutes=1)
        long_msg = "I actually want to put the document under the Getting Started section and title it AI Policy for the whole team"
        assert continues_active_session(long_msg, very_recent, "create doc") is True


class TestSummarizeDroppedTurns:
    """Test _summarize_dropped_turns template generation (D-06)."""

    def test_extracts_tool_names(self):
        from app.tasks.manager import _summarize_dropped_turns
        dropped = [
            {"role": "user", "content": "search"},
            {"role": "assistant", "tool_calls": [{"function": {"name": "web_search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "some result"},
        ]
        summary = _summarize_dropped_turns(dropped)
        assert "web_search" in summary
        assert "some result" in summary

    def test_empty_dropped_returns_empty(self):
        from app.tasks.manager import _summarize_dropped_turns
        assert _summarize_dropped_turns([]) == ""


class TestSystemPromptEnvelope:
    """Test _build_system_prompt with and without active session."""

    def test_no_session_no_envelope(self):
        from app.tasks.manager import _build_system_prompt
        prompt = _build_system_prompt(
            {"context": {}, "persona": "shared", "channel": "sms"},
            active_session=None,
        )
        assert "Active Task Session" not in prompt

    def test_with_session_has_envelope(self):
        from app.tasks.manager import _build_system_prompt

        class FakeSession:
            original_intent = "Create AI Policy doc in Notion"
            gathered_context = '["Found workspace pages"]'
            tools_called = '["notion_search"]'
            pending_action = "Create the page"

        prompt = _build_system_prompt(
            {"context": {}, "persona": "shared", "channel": "sms"},
            active_session=FakeSession(),
        )
        assert "Active Task Session" in prompt
        assert "Create AI Policy doc in Notion" in prompt
        assert "notion_search" in prompt
        assert "Create the page" in prompt

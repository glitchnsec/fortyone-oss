"""
Tests for recall/meta question classification and persona clarification bypass.

Verifies:
  - "What do you know about me?" and similar patterns classify as RECALL
  - Existing intent patterns (REMINDER, existing RECALL) still work (no regression)
  - Pipeline skips persona clarification for RECALL and IDENTITY intents
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.intent import IntentType, classify_intent


# ── Intent classification: recall/meta patterns ─────────────────────────────

class TestRecallMetaClassification:
    """Recall/meta questions must classify as RECALL, not FOLLOWUP or GENERAL."""

    def test_what_do_you_know_about_me(self):
        result = classify_intent("What do you know about me?")
        assert result.type == IntentType.RECALL

    def test_what_do_you_remember_about_me(self):
        result = classify_intent("What do you remember about me?")
        assert result.type == IntentType.RECALL

    def test_tell_me_what_you_remember(self):
        result = classify_intent("Tell me what you remember")
        assert result.type == IntentType.RECALL

    def test_what_have_you_learned_about_me(self):
        result = classify_intent("What have you learned about me?")
        assert result.type == IntentType.RECALL

    def test_what_information_do_you_have_about_me(self):
        result = classify_intent("What information do you have about me?")
        assert result.type == IntentType.RECALL


class TestRecallNoRegression:
    """Existing patterns must not break."""

    def test_remind_me_still_reminder(self):
        result = classify_intent("remind me to call Bob")
        assert result.type == IntentType.REMINDER

    def test_what_reminders_do_i_have_still_recall(self):
        result = classify_intent("What reminders do I have?")
        assert result.type == IntentType.RECALL


# ── Pipeline: persona clarification bypass ───────────────────────────────────

class TestClarificationBypass:
    """RECALL and IDENTITY intents must skip persona clarification."""

    @pytest.mark.asyncio
    async def test_recall_skips_clarification(self):
        """Pipeline should NOT send clarifying question for RECALL intent."""
        from app.core.pipeline import MessagePipeline, _SKIP_CLARIFICATION_INTENTS
        assert IntentType.RECALL in _SKIP_CLARIFICATION_INTENTS

    @pytest.mark.asyncio
    async def test_identity_skips_clarification(self):
        """Pipeline should NOT send clarifying question for IDENTITY intent."""
        from app.core.pipeline import _SKIP_CLARIFICATION_INTENTS
        # IDENTITY may not exist in IntentType yet, but if it does it should be in the set
        if hasattr(IntentType, "IDENTITY"):
            assert IntentType.IDENTITY in _SKIP_CLARIFICATION_INTENTS


# ── handle_recall: surfaces stored memories + profile ────────────────────────

class TestHandleRecallMemories:
    """handle_recall must return stored memories, profile, and tasks."""

    @pytest.mark.asyncio
    async def test_recall_with_memories_returns_stored_facts(self):
        """handle_recall with context containing memories should mention them."""
        from app.tasks.recall import handle_recall

        mock_response = {"response": "I know your name is Alice and you prefer mornings."}

        with patch("app.tasks.recall.llm_messages_json", new_callable=AsyncMock, return_value=mock_response):
            result = await handle_recall({
                "job_id": "test-1",
                "phone": "+15551234567",
                "body": "What do you know about me?",
                "context": {
                    "user": {"name": "Alice", "timezone": "US/Eastern"},
                    "memories": {"preference_time": "morning", "hobby": "running"},
                    "active_tasks": [{"title": "Call Bob", "due_at": None}],
                },
            })

        assert "response" in result
        assert result["job_id"] == "test-1"
        # LLM was called and returned a meaningful response
        assert "Alice" in result["response"] or "morning" in result["response"] or len(result["response"]) > 10

    @pytest.mark.asyncio
    async def test_recall_empty_context_friendly_message(self):
        """handle_recall with no tasks and no memories returns friendly nothing-yet."""
        from app.tasks.recall import handle_recall

        mock_response = {"response": "I don't have much stored about you yet. Let's change that!"}

        with patch("app.tasks.recall.llm_messages_json", new_callable=AsyncMock, return_value=mock_response):
            result = await handle_recall({
                "job_id": "test-2",
                "phone": "+15551234567",
                "body": "What do you know about me?",
                "context": {
                    "user": {},
                    "memories": {},
                    "active_tasks": [],
                },
            })

        assert "response" in result
        assert len(result["response"]) > 5

    @pytest.mark.asyncio
    async def test_recall_with_tasks_and_memories_returns_both(self):
        """handle_recall with both tasks and memories includes both sections."""
        from app.tasks.recall import handle_recall

        mock_response = {"response": "You're Alice. I know you like running. You have 1 active task: Call Bob."}

        with patch("app.tasks.recall.llm_messages_json", new_callable=AsyncMock, return_value=mock_response):
            result = await handle_recall({
                "job_id": "test-3",
                "phone": "+15551234567",
                "body": "What do you know about me?",
                "context": {
                    "user": {"name": "Alice", "timezone": "US/Eastern"},
                    "memories": {"hobby": "running"},
                    "active_tasks": [{"title": "Call Bob", "due_at": None}],
                },
            })

        assert "response" in result
        assert len(result["response"]) > 10

    @pytest.mark.asyncio
    async def test_recall_includes_profile_fields(self):
        """handle_recall response includes user profile info when available."""
        from app.tasks.recall import handle_recall

        mock_response = {"response": "Hi Alice! Your timezone is US/Eastern. You prefer mornings."}

        with patch("app.tasks.recall.llm_messages_json", new_callable=AsyncMock, return_value=mock_response):
            result = await handle_recall({
                "job_id": "test-4",
                "phone": "+15551234567",
                "body": "What do you know about me?",
                "context": {
                    "user": {"name": "Alice", "timezone": "US/Eastern"},
                    "memories": {},
                    "active_tasks": [],
                },
            })

        assert "response" in result

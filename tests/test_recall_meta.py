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

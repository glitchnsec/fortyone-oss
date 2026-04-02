"""
Gap tests for classify_intent() — FOLLOWUP boundary conditions.

Existing test_conversation_window.py covers: FOLLOWUP fires for short messages,
doesn't override REMINDER, doesn't fire for long messages.
These tests cover exact boundary values and edge cases.
"""
import pytest
from app.core.intent import classify_intent, IntentType


def test_followup_boundary_exactly_14_words():
    """14 words (< 15) with no rule match → FOLLOWUP."""
    # exactly 14 words, no intent signals
    text = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
    assert len(text.split()) == 14
    intent = classify_intent(text)
    assert intent.type == IntentType.FOLLOWUP
    assert intent.requires_worker is True


def test_general_at_15_words():
    """Exactly 15 words with no rule match → GENERAL (not FOLLOWUP)."""
    # exactly 15 words, no intent signals
    text = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen"
    assert len(text.split()) == 15
    intent = classify_intent(text)
    assert intent.type == IntentType.GENERAL
    assert intent.confidence == 0.50


def test_empty_string_classified():
    """Empty string doesn't crash — classified as FOLLOWUP (0 words < 15)."""
    intent = classify_intent("")
    # Empty string splits to [''] which has length 1 (< 15), but strip() makes it ""
    # The split of "" = [''] (length 1), so < 15 → FOLLOWUP
    assert intent.type == IntentType.FOLLOWUP


def test_followup_does_not_override_web_search():
    """Short message with web search signal still matches WEB_SEARCH, not FOLLOWUP."""
    text = "search for cats"  # 3 words, but matches WEB_SEARCH rule
    intent = classify_intent(text)
    assert intent.type == IntentType.WEB_SEARCH
    assert intent.confidence > 0.5

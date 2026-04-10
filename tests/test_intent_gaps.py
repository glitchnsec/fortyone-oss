"""
Gap tests for classify_intent() — boundary conditions.

After Phase 4 (AGENT-07), all non-regex intents route to NEEDS_MANAGER.
Only GREETING and IDENTITY use regex fast-path. Everything else → NEEDS_MANAGER.
These tests verify that boundary cases route correctly.
"""
import pytest
from app.core.intent import classify_intent, IntentType


def test_short_message_routes_to_manager():
    """14 words with no rule match → NEEDS_MANAGER (Phase 4)."""
    text = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
    assert len(text.split()) == 14
    intent = classify_intent(text)
    assert intent.type == IntentType.NEEDS_MANAGER
    assert intent.requires_worker is True


def test_long_message_routes_to_manager():
    """Exactly 15 words with no rule match → NEEDS_MANAGER (Phase 4)."""
    text = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen"
    assert len(text.split()) == 15
    intent = classify_intent(text)
    assert intent.type == IntentType.NEEDS_MANAGER


def test_empty_string_classified():
    """Empty string doesn't crash — classified as NEEDS_MANAGER."""
    intent = classify_intent("")
    assert intent.type == IntentType.NEEDS_MANAGER


def test_greeting_still_regex_fast_path():
    """Greeting is still handled by regex fast-path, not NEEDS_MANAGER."""
    intent = classify_intent("hello")
    assert intent.type == IntentType.GREETING
    assert intent.requires_worker is False


def test_web_search_text_routes_to_manager():
    """'search for cats' routes to NEEDS_MANAGER (no more WEB_SEARCH regex)."""
    text = "search for cats"
    intent = classify_intent(text)
    assert intent.type == IntentType.NEEDS_MANAGER
    assert intent.requires_worker is True

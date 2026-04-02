"""
Tests for race timeout configuration and ACK placeholder content.

Verifies:
- race_timeout_s defaults to 4.0 (not 1.5)
- Static ACK pool entries are all placeholder-style (action words, no exclamations)
- Smart ACK with mocked LLM returns placeholder text
- Smart ACK fallback returns from static pool
"""
import asyncio
import re
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.core.intent import IntentType


def test_race_timeout_defaults_to_8():
    """Settings().race_timeout_s defaults to 8.0 seconds."""
    from app.config import Settings
    s = Settings()
    assert s.race_timeout_s == 8.0, f"Expected 8.0, got {s.race_timeout_s}"


def test_ack_pool_entries_contain_action_words():
    """Every _ACK_POOL entry must contain at least one action/placeholder word."""
    from app.core.ack import _ACK_POOL

    action_words = {"working", "looking", "checking", "pulling", "on it", "moment", "sec", "hang tight"}
    for intent, entries in _ACK_POOL.items():
        for entry in entries:
            lower = entry.lower()
            found = any(word in lower for word in action_words)
            assert found, (
                f"ACK pool entry for {intent.value} is not placeholder-style: {entry!r}. "
                f"Must contain one of: {action_words}"
            )


def test_ack_pool_entries_under_40_chars():
    """All static ACK entries should be concise (under 40 chars)."""
    from app.core.ack import _ACK_POOL

    for intent, entries in _ACK_POOL.items():
        for entry in entries:
            assert len(entry) <= 40, (
                f"ACK pool entry for {intent.value} too long ({len(entry)} chars): {entry!r}"
            )


def test_ack_pool_no_exclamations_or_questions():
    """Placeholder messages are statements, not exclamations or questions."""
    from app.core.ack import _ACK_POOL

    for intent, entries in _ACK_POOL.items():
        for entry in entries:
            assert "!" not in entry, (
                f"ACK pool entry for {intent.value} contains '!': {entry!r}"
            )
            assert "?" not in entry, (
                f"ACK pool entry for {intent.value} contains '?': {entry!r}"
            )


@pytest.mark.asyncio
async def test_smart_ack_with_mocked_llm_returns_placeholder():
    """get_smart_ack with LLM returns placeholder-style text."""
    from app.core.ack import get_smart_ack

    mock_settings = MagicMock()
    mock_settings.has_llm = True

    with patch("app.config.get_settings", return_value=mock_settings), \
         patch("app.core.ack._llm_ack", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "Checking your schedule..."
        result = await get_smart_ack(IntentType.SCHEDULE, "when is my next meeting?")

    assert result == "Checking your schedule..."


@pytest.mark.asyncio
async def test_smart_ack_fallback_returns_from_pool():
    """When LLM times out, fallback returns a string from _ACK_POOL."""
    from app.core.ack import get_smart_ack, _ACK_POOL

    mock_settings = MagicMock()
    mock_settings.has_llm = True

    with patch("app.config.get_settings", return_value=mock_settings), \
         patch("app.core.ack._llm_ack", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = asyncio.TimeoutError()
        result = await get_smart_ack(IntentType.RECALL, "What do you know about me?")

    assert result in _ACK_POOL[IntentType.RECALL]


def test_ack_system_prompt_forbids_answering():
    """The ACK system prompt must instruct the LLM to never answer the question."""
    from app.core.ack import _ACK_SYSTEM
    lower = _ACK_SYSTEM.lower()
    assert "never answer" in lower, (
        "_ACK_SYSTEM must contain 'NEVER answer' instruction"
    )

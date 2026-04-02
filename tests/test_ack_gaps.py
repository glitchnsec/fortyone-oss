"""
Gap tests for get_smart_ack() and _llm_ack() — message truncation, empty input, fallbacks.

Existing test_ack_recent_messages.py covers: accepts recent_messages, backward compat,
passes to LLM, timeout fallback, prompt inclusion.
These tests cover additional edge cases.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.core.intent import IntentType


@pytest.mark.asyncio
async def test_llm_ack_truncates_to_last_6_messages():
    """_llm_ack with >6 recent_messages only includes last 6 in LLM call."""
    from app.core.ack import _llm_ack

    mock_settings = MagicMock()
    mock_settings.has_llm = True
    mock_settings.llm_model_fast = "test-model"

    # 10 messages — only last 6 should be used
    recent = [
        {"direction": "inbound" if i % 2 == 0 else "outbound", "body": f"msg-{i}"}
        for i in range(10)
    ]

    captured_messages = []

    async def fake_create(**kwargs):
        captured_messages.extend(kwargs.get("messages", []))
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="Got it"))]
        return resp

    mock_client = MagicMock()
    mock_client.chat.completions.create = fake_create

    with patch("app.config.get_settings", return_value=mock_settings), \
         patch("app.tasks._llm._client", return_value=mock_client):
        result = await _llm_ack("test body", "Alice", recent_messages=recent)

    assert result == "Got it"
    # Messages: 1 system + 6 history + 1 user prompt = 8
    # Filter out system and the final user prompt to count history messages
    history = [m for m in captured_messages if m["role"] != "system" and "User just sent" not in m.get("content", "")]
    assert len(history) == 6
    # Should be the LAST 6 (msg-4 through msg-9)
    assert "msg-4" in history[0]["content"]


@pytest.mark.asyncio
async def test_llm_ack_empty_recent_messages_list():
    """_llm_ack with empty list [] works same as None — no history in prompt."""
    from app.core.ack import _llm_ack

    mock_settings = MagicMock()
    mock_settings.has_llm = True
    mock_settings.llm_model_fast = "test-model"

    captured_messages = []

    async def fake_create(**kwargs):
        captured_messages.extend(kwargs.get("messages", []))
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="On it"))]
        return resp

    mock_client = MagicMock()
    mock_client.chat.completions.create = fake_create

    with patch("app.config.get_settings", return_value=mock_settings), \
         patch("app.tasks._llm._client", return_value=mock_client):
        result = await _llm_ack("test body", "Alice", recent_messages=[])

    # Should be 1 system + 1 user prompt = 2 (no history)
    assert len(captured_messages) == 2


@pytest.mark.asyncio
async def test_smart_ack_generic_exception_fallback():
    """Non-timeout exception in LLM call → static fallback returned."""
    from app.core.ack import get_smart_ack, _ACK_POOL

    mock_settings = MagicMock()
    mock_settings.has_llm = True

    with patch("app.config.get_settings", return_value=mock_settings), \
         patch("app.core.ack._llm_ack", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = RuntimeError("Connection refused")
        result = await get_smart_ack(IntentType.REMINDER, "remind me to call mom")

    # Should be one of the static REMINDER pool messages
    assert result in _ACK_POOL[IntentType.REMINDER]


@pytest.mark.asyncio
async def test_smart_ack_llm_returns_empty_string():
    """LLM returning empty/None → falls back to static pool."""
    from app.core.ack import get_smart_ack, _ACK_POOL

    mock_settings = MagicMock()
    mock_settings.has_llm = True

    with patch("app.config.get_settings", return_value=mock_settings), \
         patch("app.core.ack._llm_ack", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = None  # empty LLM response
        result = await get_smart_ack(IntentType.GENERAL, "help me with something")

    assert result in _ACK_POOL[IntentType.GENERAL]

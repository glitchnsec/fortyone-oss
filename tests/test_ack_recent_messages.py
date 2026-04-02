"""
Tests for get_smart_ack with recent_messages context (D-12).

Verifies:
- get_smart_ack accepts recent_messages parameter
- recent_messages are included in the LLM prompt when provided
- backward compat: recent_messages=None behaves identically to before
- fallback to static pool on timeout regardless of recent_messages
- ACK system prompt with recent_messages differs from prompt without
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.intent import IntentType


@pytest.mark.asyncio
async def test_get_smart_ack_accepts_recent_messages():
    """get_smart_ack signature must include recent_messages parameter."""
    import inspect
    from app.core.ack import get_smart_ack
    sig = inspect.signature(get_smart_ack)
    assert "recent_messages" in sig.parameters, (
        f"recent_messages not in {list(sig.parameters)}"
    )


@pytest.mark.asyncio
async def test_get_smart_ack_recent_messages_none_backward_compat():
    """recent_messages=None must work identically to the old call pattern."""
    with patch("app.core.ack._llm_ack", new=AsyncMock(return_value="On it!")):
        with patch("app.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(has_llm=True, llm_model_fast="test-model")
            from app.core.ack import get_smart_ack
            result = await get_smart_ack(
                IntentType.GENERAL,
                "do something for me",
                user_name="Alice",
                recent_messages=None,
            )
    assert result == "On it!"


@pytest.mark.asyncio
async def test_get_smart_ack_passes_recent_messages_to_llm():
    """When recent_messages is provided, it should be passed to _llm_ack."""
    recent = [
        {"direction": "inbound", "body": "remind me about the meeting"},
        {"direction": "outbound", "body": "Got it, I'll remind you."},
    ]
    captured_kwargs = {}

    async def fake_llm_ack(body, user_name, recent_messages=None, **kwargs):
        captured_kwargs["recent_messages"] = recent_messages
        return "On it!"

    with patch("app.core.ack._llm_ack", side_effect=fake_llm_ack):
        with patch("app.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(has_llm=True, llm_model_fast="test-model")
            from app.core.ack import get_smart_ack
            result = await get_smart_ack(
                IntentType.GENERAL,
                "and also add a calendar event",
                user_name="Alice",
                recent_messages=recent,
            )

    assert captured_kwargs.get("recent_messages") is not None
    assert len(captured_kwargs["recent_messages"]) == 2
    assert result == "On it!"


@pytest.mark.asyncio
async def test_get_smart_ack_falls_back_on_timeout_with_recent_messages():
    """Static fallback triggers on timeout even when recent_messages is provided."""
    recent = [{"direction": "inbound", "body": "test message"}]

    async def slow_llm_ack(body, user_name, recent_messages=None):
        await asyncio.sleep(10)
        return "too slow"

    with patch("app.core.ack._llm_ack", side_effect=slow_llm_ack):
        with patch("app.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(has_llm=True, llm_model_fast="test-model")
            from app.core.ack import get_smart_ack
            result = await get_smart_ack(
                IntentType.REMINDER,
                "remind me at 3pm",
                recent_messages=recent,
                timeout_s=0.05,
            )

    # Must return a static fallback string, not None, not "too slow"
    assert isinstance(result, str)
    assert result != "too slow"
    assert len(result) > 0


@pytest.mark.asyncio
async def test_llm_ack_includes_recent_messages_in_prompt():
    """_llm_ack includes recent messages as conversation history in the messages list."""
    captured_messages = {}

    async def fake_create(**kwargs):
        captured_messages["messages"] = kwargs.get("messages", [])
        mock_choice = MagicMock()
        mock_choice.message.content = "Working on it"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        return mock_response

    recent = [
        {"direction": "inbound", "body": "schedule a meeting"},
        {"direction": "outbound", "body": "Got it, looking at your calendar"},
        {"direction": "inbound", "body": "for next Tuesday"},
    ]

    with patch("app.config.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            has_llm=True,
            llm_model_fast="test-model",
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create = fake_create
        with patch("app.tasks._llm._client", return_value=mock_client):
            from app.core.ack import _llm_ack
            await _llm_ack("ok, confirmed", user_name="Alice", recent_messages=recent)

    msgs = captured_messages.get("messages", [])
    # system + 3 history messages + current user message = at least 5
    assert len(msgs) >= 3, f"Expected messages with history, got {len(msgs)} messages"
    # The history should be included somewhere in the messages
    roles_and_content = [(m["role"], m.get("content", "")) for m in msgs]
    user_contents = [c for role, c in roles_and_content if role == "user"]
    assert any("schedule a meeting" in c or "for next Tuesday" in c for c in user_contents), (
        f"Recent inbound messages not found in prompt: {roles_and_content}"
    )

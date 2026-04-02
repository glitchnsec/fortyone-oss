"""
Gap tests for detect_persona() — edge cases not covered by test_persona_model.py.

Covers: dual signals, missing persona names, long-body inheritance skip,
LLM persona name mapping, short-body no-clarification, history passthrough.
"""
import pytest
from unittest.mock import AsyncMock, patch, call


def _make_persona(name, description=None):
    """Lightweight Persona-like object (no DB needed)."""
    class _FakePersona:
        pass
    p = _FakePersona()
    p.id = f"test-{name}"
    p.name = name
    p.description = description
    p.tone_notes = None
    p.is_active = True
    return p


@pytest.mark.asyncio
async def test_both_work_and_personal_signals_falls_to_llm():
    """Message with both work AND personal signals bypasses rule fast-path → hits LLM."""
    from app.core.persona import detect_persona

    personas = [_make_persona("work"), _make_persona("personal")]
    mock_response = {"persona": "work", "confidence": 0.8}

    with patch("app.tasks._llm.llm_messages_json", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_response
        name, conf, needs_clarification = await detect_persona(
            # "meeting" is work signal, "gym" is personal signal — both present
            body="after the meeting I need to go to the gym",
            user_personas=personas,
            recent_messages=[],
        )
    # LLM was called (not rule fast-path) because both work+personal signals present
    mock_llm.assert_called_once()
    assert name == "work"
    assert conf == 0.8


@pytest.mark.asyncio
async def test_work_signal_no_work_persona_falls_through():
    """Work signal present but user has no persona named 'work' → falls through to LLM."""
    from app.core.persona import detect_persona

    # User only has "professional" persona, not "work"
    personas = [_make_persona("professional"), _make_persona("personal")]
    mock_response = {"persona": "professional", "confidence": 0.7}

    with patch("app.tasks._llm.llm_messages_json", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_response
        name, conf, _ = await detect_persona(
            body="meeting with the client tomorrow",
            user_personas=personas,
            recent_messages=[],
        )
    # Rule detected "meeting" (work signal) but no persona named "work" → LLM called
    mock_llm.assert_called_once()


@pytest.mark.asyncio
async def test_inherit_skipped_for_long_messages():
    """last_persona is NOT inherited when body has >= 10 words (too long for follow-up)."""
    from app.core.persona import detect_persona

    personas = [_make_persona("work"), _make_persona("personal")]
    mock_response = {"persona": "personal", "confidence": 0.9}

    with patch("app.tasks._llm.llm_messages_json", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_response
        name, conf, _ = await detect_persona(
            # 12 words, no work/personal signal words → no rule match
            body="I want to plan something for the whole group this coming month please",
            user_personas=personas,
            recent_messages=[],
            last_persona="work",  # would inherit if body were short
        )
    # Inheritance skipped (>= 10 words), LLM was called
    mock_llm.assert_called_once()
    assert name == "personal"


@pytest.mark.asyncio
async def test_llm_maps_to_actual_persona_name():
    """LLM returns lowercase 'work' → mapped to user's 'Work' (capitalized) persona."""
    from app.core.persona import detect_persona

    personas = [_make_persona("Work"), _make_persona("Personal")]
    mock_response = {"persona": "work", "confidence": 0.85}

    with patch("app.tasks._llm.llm_messages_json", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_response
        name, conf, _ = await detect_persona(
            # No signal words, long enough to skip inheritance → LLM path
            body="I need to handle something for the group today and also something else entirely",
            user_personas=personas,
            recent_messages=[],
        )
    # Should map LLM's lowercase "work" to actual persona name "Work"
    assert name == "Work"
    assert conf == 0.85


@pytest.mark.asyncio
async def test_short_body_low_confidence_no_clarification():
    """Short body (<5 words) + low LLM confidence → needs_clarification=False.

    Per the code: needs_clarification requires len(body.split()) >= 5.
    Short messages should inherit last_persona instead, but if they reach LLM
    (no last_persona set), clarification is still suppressed for brevity.
    """
    from app.core.persona import detect_persona

    personas = [_make_persona("work"), _make_persona("personal")]
    mock_response = {"persona": "shared", "confidence": 0.3}  # well below 0.6

    with patch("app.tasks._llm.llm_messages_json", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_response
        name, conf, needs_clarification = await detect_persona(
            body="handle this please",  # 3 words, no signal, no last_persona → LLM
            user_personas=personas,
            recent_messages=[],
            last_persona=None,
        )
    assert needs_clarification is False  # short body suppresses clarification


@pytest.mark.asyncio
async def test_recent_messages_passed_to_llm():
    """Verifies conversation history from recent_messages appears in LLM call."""
    from app.core.persona import detect_persona

    personas = [_make_persona("work"), _make_persona("personal")]
    recent = [
        {"direction": "inbound", "body": "schedule a meeting"},
        {"direction": "outbound", "body": "Sure, checking your calendar"},
    ]
    mock_response = {"persona": "work", "confidence": 0.9}

    with patch("app.tasks._llm.llm_messages_json", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_response
        await detect_persona(
            body="I need to reorganize my entire schedule for the next two weeks please",
            user_personas=personas,
            recent_messages=recent,
        )
    # Verify the messages list includes history
    call_args = mock_llm.call_args
    messages = call_args.kwargs.get("messages", call_args[1].get("messages", []))
    # Should have system + 2 history messages + 1 user message = 4
    user_messages = [m for m in messages if m["role"] == "user"]
    assert len(user_messages) >= 2  # at least the history inbound + current body

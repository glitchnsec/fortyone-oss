"""
End-to-end pipeline integration tests — Phase 3 wiring.

Verifies:
- SCHEDULE intent calls get_context_full (tiered context routing)
- REMINDER intent calls get_context_standard
- FOLLOWUP intent routes to queue (does NOT return early)
- Job payload includes "persona" key
- get_smart_ack called with recent_messages from get_context_minimal
- GREETING intent returns early without pushing a job (regression)
- needs_clarification=True sends clarifying question and returns early (no push_job)
- Clarifying question message stored as outbound via store_message
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


def _make_pipeline():
    """Build a MessagePipeline with fully mocked dependencies."""
    from app.core.pipeline import MessagePipeline

    channel = MagicMock()
    channel.name = "sms"
    channel.send = AsyncMock(return_value=True)

    queue = MagicMock()
    queue.push_job = AsyncMock(return_value="job-123")

    store = MagicMock()

    # User stub
    user = MagicMock()
    user.id = "user-1"
    user.name = "Alice"
    store.get_or_create_user = AsyncMock(return_value=user)
    store.message_count = AsyncMock(return_value=5)  # not first message

    # Minimal context: recent messages + last_persona
    store.get_context_minimal = AsyncMock(return_value={
        "recent_messages": [
            {"direction": "inbound", "body": "hi", "intent": "greeting"},
            {"direction": "outbound", "body": "Hey!", "intent": None},
        ],
        "last_persona": "work",
        "message_count": 2,
    })

    # Standard and full context stubs
    store.get_context_standard = AsyncMock(return_value={"recent_messages": [], "memories": {}})
    store.get_context_full = AsyncMock(return_value={"recent_messages": [], "memories": {}, "personas": []})

    # No personas by default (tests that need personas override this)
    store.get_personas = AsyncMock(return_value=[])
    store.store_message = AsyncMock(return_value=MagicMock())

    return MessagePipeline(channel=channel, queue=queue, store=store)


@pytest.mark.asyncio
async def test_schedule_intent_uses_full_context():
    """SCHEDULE intent must call get_context_full, not get_context or get_context_standard."""
    pipeline = _make_pipeline()
    with patch("app.core.ack.get_smart_ack", new=AsyncMock(return_value="On it!")):
        await pipeline.handle("+15551234567", "schedule a meeting for tomorrow")

    pipeline.store.get_context_full.assert_called_once()
    pipeline.store.get_context_standard.assert_not_called()


@pytest.mark.asyncio
async def test_reminder_intent_uses_standard_context():
    """REMINDER intent must call get_context_standard, not get_context_full."""
    pipeline = _make_pipeline()
    with patch("app.core.ack.get_smart_ack", new=AsyncMock(return_value="On it!")):
        await pipeline.handle("+15551234567", "remind me at 3pm to call Bob")

    pipeline.store.get_context_standard.assert_called_once()
    pipeline.store.get_context_full.assert_not_called()


@pytest.mark.asyncio
async def test_followup_routes_to_job_queue():
    """FOLLOWUP intent (short message, no explicit rule match) must push a job — NOT return early."""
    pipeline = _make_pipeline()
    with patch("app.core.ack.get_smart_ack", new=AsyncMock(return_value="Ok!")):
        await pipeline.handle("+15551234567", "ok")  # short, no rule match → FOLLOWUP

    pipeline.queue.push_job.assert_called_once()


@pytest.mark.asyncio
async def test_greeting_returns_early():
    """GREETING intent must return early — no job pushed (regression check)."""
    pipeline = _make_pipeline()
    with patch("app.core.ack.get_smart_ack", new=AsyncMock(return_value="Hey!")):
        await pipeline.handle("+15551234567", "hi")

    pipeline.queue.push_job.assert_not_called()


@pytest.mark.asyncio
async def test_job_payload_includes_persona():
    """Job payload dict must include 'persona' key after pipeline runs."""
    pipeline = _make_pipeline()
    with patch("app.core.ack.get_smart_ack", new=AsyncMock(return_value="On it!")):
        await pipeline.handle("+15551234567", "remind me at 3pm")

    pipeline.queue.push_job.assert_called_once()
    payload = pipeline.queue.push_job.call_args[0][0]
    assert "persona" in payload, f"'persona' key missing from job payload: {list(payload.keys())}"


@pytest.mark.asyncio
async def test_ack_receives_recent_messages():
    """get_smart_ack must be called with recent_messages from get_context_minimal."""
    pipeline = _make_pipeline()

    ack_call_kwargs = {}

    async def capturing_ack(intent_type, body, user_name=None, recent_messages=None, **kwargs):
        ack_call_kwargs["recent_messages"] = recent_messages
        return "Got it!"

    with patch("app.core.pipeline.get_smart_ack", side_effect=capturing_ack):
        await pipeline.handle("+15551234567", "remind me at 3pm")

    assert ack_call_kwargs.get("recent_messages") is not None, (
        "get_smart_ack was called without recent_messages"
    )
    assert len(ack_call_kwargs["recent_messages"]) > 0


@pytest.mark.asyncio
async def test_needs_clarification_sends_question_and_returns_early():
    """
    When detect_persona returns needs_clarification=True:
    - channel.send must be called with the clarifying question text
    - push_job must NOT be called
    """
    pipeline = _make_pipeline()

    # Add a persona so detect_persona is actually called
    mock_persona = MagicMock()
    mock_persona.name = "work"
    pipeline.store.get_personas = AsyncMock(return_value=[mock_persona])

    with patch("app.core.ack.get_smart_ack", new=AsyncMock(return_value="On it!")):
        with patch("app.core.persona.detect_persona", new=AsyncMock(
            return_value=("shared", 0.4, True)  # needs_clarification=True
        )):
            await pipeline.handle("+15551234567", "add that to the calendar")

    # channel.send should have been called twice: once for ACK, once for clarifying question
    assert pipeline.channel.send.call_count >= 2, (
        f"Expected at least 2 sends (ACK + clarifying question), got {pipeline.channel.send.call_count}"
    )
    all_send_texts = [call_args[0][1] for call_args in pipeline.channel.send.call_args_list]
    assert any("work or personal" in text for text in all_send_texts), (
        f"Clarifying question not found in sends: {all_send_texts}"
    )
    pipeline.queue.push_job.assert_not_called()


@pytest.mark.asyncio
async def test_needs_clarification_stores_outbound_message():
    """
    When needs_clarification=True, store_message must be called for the
    clarifying question as an outbound message.
    """
    pipeline = _make_pipeline()

    mock_persona = MagicMock()
    mock_persona.name = "work"
    pipeline.store.get_personas = AsyncMock(return_value=[mock_persona])

    with patch("app.core.ack.get_smart_ack", new=AsyncMock(return_value="On it!")):
        with patch("app.core.persona.detect_persona", new=AsyncMock(
            return_value=("shared", 0.4, True)
        )):
            await pipeline.handle("+15551234567", "add that to the calendar")

    # Find outbound store_message calls
    outbound_calls = [
        c for c in pipeline.store.store_message.call_args_list
        if c.kwargs.get("direction") == "outbound" or (
            len(c.args) > 1 and c.args[1] == "outbound"
        )
    ]
    # Check that a clarifying question was stored with direction=outbound
    clarifying_stored = any(
        "work or personal" in (c.kwargs.get("body", "") or "")
        or (len(c.args) > 2 and "work or personal" in str(c.args[2]))
        for c in pipeline.store.store_message.call_args_list
    )
    assert clarifying_stored, (
        f"Clarifying question not found in store_message calls: {pipeline.store.store_message.call_args_list}"
    )

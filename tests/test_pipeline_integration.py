"""
End-to-end pipeline integration tests — Phase 3 wiring + race pattern.

Verifies:
- SCHEDULE intent calls get_context_full (tiered context routing)
- REMINDER intent calls get_context_standard
- FOLLOWUP intent routes to queue (does NOT return early)
- Job payload includes "persona" key
- GREETING intent returns early without pushing a job
- needs_clarification=True sends clarifying question and returns early
- Race pattern: worker responds fast → single message (no ACK)
- Race pattern: worker slow → ACK sent, ResponseListener delivers later
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_pipeline(race_result=None):
    """Build a MessagePipeline with fully mocked dependencies.

    race_result: if not None, wait_for_result returns this (simulates race win).
                 if None, wait_for_result returns None (simulates race timeout).
    """
    from app.core.pipeline import MessagePipeline

    channel = MagicMock()
    channel.name = "sms"
    channel.send = AsyncMock(return_value=True)

    queue = MagicMock()
    queue.push_job = AsyncMock(return_value="job-123")
    queue.wait_for_result = AsyncMock(return_value=race_result)
    queue.claim_delivery = AsyncMock(return_value=True)

    store = MagicMock()

    user = MagicMock()
    user.id = "user-1"
    user.name = "Alice"
    store.get_or_create_user = AsyncMock(return_value=user)
    store.message_count = AsyncMock(return_value=5)  # not first message

    store.get_context_minimal = AsyncMock(return_value={
        "recent_messages": [
            {"direction": "inbound", "body": "hi", "intent": "greeting"},
            {"direction": "outbound", "body": "Hey!", "intent": None},
        ],
        "last_persona": "work",
        "message_count": 2,
    })

    store.get_context_standard = AsyncMock(return_value={"recent_messages": [], "memories": {}})
    store.get_context_full = AsyncMock(return_value={"recent_messages": [], "memories": {}, "personas": []})

    store.get_personas = AsyncMock(return_value=[])
    store.store_message = AsyncMock(return_value=MagicMock())

    return MessagePipeline(channel=channel, queue=queue, store=store)


@pytest.mark.asyncio
async def test_schedule_intent_uses_full_context():
    """SCHEDULE intent must call get_context_full, not get_context_standard."""
    pipeline = _make_pipeline(race_result={"response": "Done!", "learn": {}})
    with patch("app.core.pipeline.get_smart_ack", new=AsyncMock(return_value="On it!")):
        await pipeline.handle("+15551234567", "schedule a meeting for tomorrow")

    pipeline.store.get_context_full.assert_called_once()
    pipeline.store.get_context_standard.assert_not_called()


@pytest.mark.asyncio
async def test_reminder_intent_uses_standard_context():
    """REMINDER intent must call get_context_standard, not get_context_full."""
    pipeline = _make_pipeline(race_result={"response": "Reminder set!", "learn": {}})
    with patch("app.core.pipeline.get_smart_ack", new=AsyncMock(return_value="On it!")):
        await pipeline.handle("+15551234567", "remind me at 3pm to call Bob")

    pipeline.store.get_context_standard.assert_called_once()
    pipeline.store.get_context_full.assert_not_called()


@pytest.mark.asyncio
async def test_followup_routes_to_job_queue():
    """FOLLOWUP intent (short message, no explicit rule match) must push a job."""
    pipeline = _make_pipeline(race_result={"response": "Sure!", "learn": {}})
    with patch("app.core.pipeline.get_smart_ack", new=AsyncMock(return_value="Ok!")):
        await pipeline.handle("+15551234567", "ok")

    pipeline.queue.push_job.assert_called_once()


@pytest.mark.asyncio
async def test_greeting_returns_early():
    """GREETING intent must return early — no job pushed."""
    pipeline = _make_pipeline()
    with patch("app.core.pipeline.get_smart_ack", new=AsyncMock(return_value="Hey!")):
        await pipeline.handle("+15551234567", "hi")

    pipeline.queue.push_job.assert_not_called()


@pytest.mark.asyncio
async def test_job_payload_includes_persona():
    """Job payload dict must include 'persona' key."""
    pipeline = _make_pipeline(race_result={"response": "Done!", "learn": {}})
    with patch("app.core.pipeline.get_smart_ack", new=AsyncMock(return_value="On it!")):
        await pipeline.handle("+15551234567", "remind me at 3pm")

    pipeline.queue.push_job.assert_called_once()
    payload = pipeline.queue.push_job.call_args[0][0]
    assert "persona" in payload


@pytest.mark.asyncio
async def test_race_won_sends_single_message():
    """When worker responds within timeout, only one message sent (no ACK)."""
    pipeline = _make_pipeline(race_result={"response": "Here's your answer!", "learn": {}})
    with patch("app.core.pipeline.get_smart_ack", new=AsyncMock(return_value="Working on it...")):
        await pipeline.handle("+15551234567", "remind me at 3pm")

    # Should send exactly 1 outbound message (the worker response, not the ACK)
    send_texts = [c[0][1] for c in pipeline.channel.send.call_args_list]
    assert "Here's your answer!" in send_texts
    assert "Working on it..." not in send_texts


@pytest.mark.asyncio
async def test_race_timeout_sends_ack():
    """When worker doesn't respond in time, ACK is sent."""
    pipeline = _make_pipeline(race_result=None)  # timeout
    with patch("app.core.pipeline.get_smart_ack", new=AsyncMock(return_value="Working on it...")):
        await pipeline.handle("+15551234567", "remind me at 3pm")

    send_texts = [c[0][1] for c in pipeline.channel.send.call_args_list]
    assert "Working on it..." in send_texts


@pytest.mark.asyncio
async def test_needs_clarification_sends_question_and_returns_early():
    """needs_clarification=True → sends clarifying question, no job pushed."""
    pipeline = _make_pipeline()

    mock_persona = MagicMock()
    mock_persona.name = "work"
    pipeline.store.get_personas = AsyncMock(return_value=[mock_persona])

    with patch("app.core.pipeline.get_smart_ack", new=AsyncMock(return_value="On it!")):
        with patch("app.core.persona.detect_persona", new=AsyncMock(
            return_value=("shared", 0.4, True)
        )):
            await pipeline.handle("+15551234567", "add that to the calendar")

    all_send_texts = [c[0][1] for c in pipeline.channel.send.call_args_list]
    assert any("work or personal" in text for text in all_send_texts)
    pipeline.queue.push_job.assert_not_called()


@pytest.mark.asyncio
async def test_needs_clarification_stores_outbound_message():
    """Clarifying question stored as outbound message."""
    pipeline = _make_pipeline()

    mock_persona = MagicMock()
    mock_persona.name = "work"
    pipeline.store.get_personas = AsyncMock(return_value=[mock_persona])

    with patch("app.core.pipeline.get_smart_ack", new=AsyncMock(return_value="On it!")):
        with patch("app.core.persona.detect_persona", new=AsyncMock(
            return_value=("shared", 0.4, True)
        )):
            await pipeline.handle("+15551234567", "add that to the calendar")

    clarifying_stored = any(
        "work or personal" in (c.kwargs.get("body", "") or "")
        for c in pipeline.store.store_message.call_args_list
    )
    assert clarifying_stored

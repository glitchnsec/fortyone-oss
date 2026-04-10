"""
Integration tests for channel-scoped conversation window and FOLLOWUP intent detection.

Covers:
  - store_message persists channel and persona_tag fields
  - get_context filters messages by channel (SMS vs Slack isolation)
  - NULL-channel rows are included in 'sms' context (backward compat)
  - classify_intent returns FOLLOWUP for short messages with no rule match
  - classify_intent does NOT return FOLLOWUP when an explicit rule fires (REMINDER, etc.)
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.memory.models import Base
from app.memory.store import MemoryStore


@pytest_asyncio.fixture
async def db_session():
    """In-memory async SQLite session — schema rebuilt fresh for each test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    async with AsyncSession() as session:
        yield session
    await engine.dispose()


# ─── store_message: channel and persona_tag persistence ─────────────────────

@pytest.mark.asyncio
async def test_store_message_persists_channel(db_session):
    """store_message(channel='sms') must persist 'sms' on the Message row."""
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+10000000001")

    msg = await store.store_message(
        user_id=user.id,
        direction="inbound",
        body="hello",
        channel="sms",
    )

    assert msg.channel == "sms", f"Expected channel='sms', got '{msg.channel}'"


@pytest.mark.asyncio
async def test_store_message_persists_persona_tag(db_session):
    """store_message(persona_tag='work') must persist 'work' on the Message row."""
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+10000000002")

    msg = await store.store_message(
        user_id=user.id,
        direction="inbound",
        body="schedule a meeting",
        channel="sms",
        persona_tag="work",
    )

    assert msg.persona_tag == "work", f"Expected persona_tag='work', got '{msg.persona_tag}'"


# ─── get_context: channel scoping ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_context_filters_by_channel_sms(db_session):
    """get_context(channel='sms') must return only sms messages for the user."""
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+10000000003")

    # 2 SMS, 2 Slack
    await store.store_message(user_id=user.id, direction="inbound", body="sms 1", channel="sms")
    await store.store_message(user_id=user.id, direction="inbound", body="sms 2", channel="sms")
    await store.store_message(user_id=user.id, direction="inbound", body="slack 1", channel="slack")
    await store.store_message(user_id=user.id, direction="inbound", body="slack 2", channel="slack")

    context = await store.get_context(user.id, channel="sms")
    bodies = [m["body"] for m in context["recent_messages"]]

    assert len(bodies) == 2, f"Expected 2 sms messages, got {len(bodies)}: {bodies}"
    assert all("sms" in b for b in bodies), f"Non-sms messages leaked into context: {bodies}"


@pytest.mark.asyncio
async def test_get_context_filters_by_channel_slack(db_session):
    """get_context(channel='slack') must return only slack messages for the user."""
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+10000000004")

    await store.store_message(user_id=user.id, direction="inbound", body="sms 1", channel="sms")
    await store.store_message(user_id=user.id, direction="inbound", body="slack 1", channel="slack")
    await store.store_message(user_id=user.id, direction="inbound", body="slack 2", channel="slack")

    context = await store.get_context(user.id, channel="slack")
    bodies = [m["body"] for m in context["recent_messages"]]

    assert len(bodies) == 2, f"Expected 2 slack messages, got {len(bodies)}: {bodies}"
    assert all("slack" in b for b in bodies), f"Non-slack messages leaked into context: {bodies}"


@pytest.mark.asyncio
async def test_get_context_null_channel_fallback(db_session):
    """Legacy rows with channel=None must appear in get_context(channel='sms')."""
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+10000000005")

    # Store a message with no channel (legacy row — simulate pre-migration data)
    null_msg = await store.store_message(
        user_id=user.id,
        direction="inbound",
        body="legacy message",
        channel=None,
    )
    # Manually set channel to NULL to simulate pre-migration state
    from sqlalchemy import text
    await db_session.execute(
        text("UPDATE messages SET channel = NULL WHERE id = :id"),
        {"id": null_msg.id},
    )
    await db_session.commit()

    context = await store.get_context(user.id, channel="sms")
    bodies = [m["body"] for m in context["recent_messages"]]

    assert "legacy message" in bodies, (
        f"NULL-channel message not included in sms context. Got: {bodies}"
    )


# ─── classify_intent: FOLLOWUP detection ─────────────────────────────────────

def test_short_message_routes_to_needs_manager():
    """classify_intent('ok') must return NEEDS_MANAGER (Phase 4: all non-regex → manager)."""
    from app.core.intent import classify_intent, IntentType

    result = classify_intent("ok")
    assert result.type == IntentType.NEEDS_MANAGER, (
        f"Expected NEEDS_MANAGER for 'ok', got {result.type}"
    )
    assert result.requires_worker is True


def test_short_ambiguous_messages_route_to_needs_manager():
    """Short clarification phrases must route to NEEDS_MANAGER (Phase 4)."""
    from app.core.intent import classify_intent, IntentType

    for text in ["I meant Friday", "actually 3pm", "no, work email", "sure", "yeah ok"]:
        result = classify_intent(text)
        assert result.type == IntentType.NEEDS_MANAGER, (
            f"Expected NEEDS_MANAGER for '{text}', got {result.type}"
        )


def test_reminder_text_routes_to_needs_manager():
    """classify_intent('remind me tomorrow') must return NEEDS_MANAGER (Phase 4: LLM classifies)."""
    from app.core.intent import classify_intent, IntentType

    result = classify_intent("remind me tomorrow")
    assert result.type == IntentType.NEEDS_MANAGER, (
        f"Expected NEEDS_MANAGER, got {result.type}"
    )


def test_long_messages_route_to_needs_manager():
    """Messages with 15+ words route to NEEDS_MANAGER (Phase 4: all non-regex → manager)."""
    from app.core.intent import classify_intent, IntentType

    long_msg = "this is a long message that does not match any rule pattern and has many words here"
    assert len(long_msg.split()) >= 15, "Test setup: need 15+ words"

    result = classify_intent(long_msg)
    assert result.type == IntentType.NEEDS_MANAGER, (
        f"Expected NEEDS_MANAGER for long message, got {result.type}"
    )

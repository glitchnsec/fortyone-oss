"""
Gap tests for MemoryStore semantic methods — token budget, persona filtering, context tiers.

Existing test_memory_engine.py covers: basic get_relevant_memories, get_context_* structure.
These tests cover boundary conditions and deeper behavior.
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.database import Base


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_session():
    """Fresh async SQLite session per test."""
    from app.memory import models  # noqa: F401 — register all models before create_all
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _create_user(session, phone="+15550001111"):
    from app.memory.models import User
    user = User(phone=phone)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_get_relevant_memories_token_budget_stops_at_limit(db_session):
    """Token budget enforcement — stops adding memories when budget exceeded."""
    from app.memory.store import MemoryStore

    user = await _create_user(db_session)
    store = MemoryStore(db_session)

    # Create memories with known sizes: each ~100 chars = ~25 tokens
    for i in range(20):
        await store.store_memory(
            user_id=user.id,
            memory_type="long_term",
            key=f"fact-{i}",
            value="x" * 96,  # key + value ~ 100 chars → ~25 tokens
        )

    # embed_text returns [] → recency fallback; budget=100 tokens → ~4 memories
    with patch("app.memory.embeddings.embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = []
        result = await store.get_relevant_memories(
            user_id=user.id,
            query_text="anything",
            token_budget=100,
        )

    # With ~25 tokens per memory, budget of 100 → should get ~4 (not all 20)
    assert len(result) <= 5
    assert len(result) >= 1


@pytest.mark.asyncio
async def test_get_relevant_memories_persona_tag_filter(db_session):
    """persona_tag='work' includes work AND shared memories, excludes personal."""
    from app.memory.store import MemoryStore

    user = await _create_user(db_session)
    store = MemoryStore(db_session)

    await store.store_memory(user.id, "long_term", "work-fact", "work data", persona_tag="work")
    await store.store_memory(user.id, "long_term", "shared-fact", "shared data", persona_tag="shared")
    await store.store_memory(user.id, "long_term", "personal-fact", "personal data", persona_tag="personal")

    with patch("app.memory.embeddings.embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = []  # forces recency fallback
        result = await store.get_relevant_memories(
            user_id=user.id,
            query_text="anything",
            persona_tag="work",
        )

    keys = {m.key for m in result}
    assert "work-fact" in keys
    assert "shared-fact" in keys
    assert "personal-fact" not in keys


@pytest.mark.asyncio
async def test_get_context_minimal_last_persona_from_newest_outbound(db_session):
    """last_persona comes from the most recent outbound message with persona_tag set."""
    from app.memory.store import MemoryStore

    user = await _create_user(db_session)
    store = MemoryStore(db_session)

    # Store messages: older outbound tagged "work", newer outbound tagged "personal"
    await store.store_message(user.id, "inbound", "hello", channel="sms")
    await store.store_message(user.id, "outbound", "ack1", channel="sms", persona_tag="work")
    await store.store_message(user.id, "outbound", "ack2", channel="sms", persona_tag="personal")

    ctx = await store.get_context_minimal(user.id, channel="sms")
    # Most recent outbound with tag is "personal"
    assert ctx["last_persona"] == "personal"


@pytest.mark.asyncio
async def test_get_context_standard_includes_memory_dict(db_session):
    """Standard context includes 'memories' dict with key:value pairs."""
    from app.memory.store import MemoryStore

    user = await _create_user(db_session)
    store = MemoryStore(db_session)

    await store.store_memory(user.id, "long_term", "favorite_color", "blue")

    with patch("app.memory.embeddings.embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = []
        ctx = await store.get_context_standard(user.id, channel="sms", query="color")

    assert "memories" in ctx
    assert isinstance(ctx["memories"], dict)
    assert ctx["memories"].get("favorite_color") == "blue"


@pytest.mark.asyncio
async def test_get_context_full_includes_all_personas(db_session):
    """Full context includes 'personas' list with all active personas."""
    from app.memory.store import MemoryStore

    user = await _create_user(db_session)
    store = MemoryStore(db_session)

    await store.create_persona(user.id, "work", description="PM role")
    await store.create_persona(user.id, "personal", description="Home life")

    with patch("app.memory.embeddings.embed_text", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = []
        ctx = await store.get_context_full(user.id, channel="sms")

    assert "personas" in ctx
    assert len(ctx["personas"]) == 2
    names = {p["name"] for p in ctx["personas"]}
    assert names == {"work", "personal"}

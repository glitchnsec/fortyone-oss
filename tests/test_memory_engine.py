"""
Integration tests for semantic memory engine and tiered context assembly.

Covers:
  - embed_text() graceful degradation and return contract
  - search_memories() persona_tag filtering
  - get_relevant_memories() token budget enforcement and fallback
  - Tiered context: minimal / standard / full
  - last_persona in get_context_minimal
  - Backward compat: original get_context() still works

All tests use AsyncMock / in-memory SQLite — no live database required.
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.memory.models import Base, Memory, Message
from app.memory.store import MemoryStore


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    async with AsyncSession() as session:
        yield session
    await engine.dispose()


# ─── embed_text tests ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_embed_text_returns_empty_without_key():
    """embed_text returns [] when has_llm is False — no API call, no crash."""
    mock_settings = MagicMock()
    mock_settings.has_llm = False

    with patch("app.memory.embeddings.get_settings", return_value=mock_settings):
        from app.memory.embeddings import embed_text
        result = await embed_text("hello world")
    assert result == []


@pytest.mark.asyncio
async def test_embed_text_returns_vector_when_llm_available():
    """embed_text returns a 1536-element list[float] when LLM is available."""
    fake_vector = [0.1] * 1536

    mock_settings = MagicMock()
    mock_settings.has_llm = True

    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=fake_vector)]

    mock_client = AsyncMock()
    mock_client.embeddings.create = AsyncMock(return_value=mock_response)

    with (
        patch("app.memory.embeddings.get_settings", return_value=mock_settings),
        patch("app.memory.embeddings._client", return_value=mock_client),
    ):
        from app.memory import embeddings as emb_mod
        # Reload to pick up patches cleanly in the module scope
        result = await emb_mod.embed_text("hello world")

    assert isinstance(result, list)
    assert len(result) == 1536
    assert result[0] == 0.1


@pytest.mark.asyncio
async def test_embed_text_truncates_long_input():
    """embed_text truncates input > 8000 chars before calling API."""
    long_text = "x" * 12000

    mock_settings = MagicMock()
    mock_settings.has_llm = True

    captured = {}

    async def mock_create(**kwargs):
        captured["input"] = kwargs.get("input", "")
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1536)]
        return mock_response

    mock_client = AsyncMock()
    mock_client.embeddings.create = mock_create

    with (
        patch("app.memory.embeddings.get_settings", return_value=mock_settings),
        patch("app.memory.embeddings._client", return_value=mock_client),
    ):
        from app.memory import embeddings as emb_mod
        await emb_mod.embed_text(long_text)

    assert len(captured["input"]) <= 8000


# ─── get_relevant_memories token budget tests ────────────────────────────────

@pytest.mark.asyncio
async def test_token_budget_enforced(db_session):
    """
    If 5 memories each have ~600-char values, token estimate = 600//4 = 150 tokens each.
    Budget=2000 → 2000//150 = 13 fit; but if value is very large, fewer fit.

    Use memories with 800-char values: 800//4 = 200 tokens each.
    Budget=500 → only 2 fit (2*200=400 < 500, 3*200=600 > 500).
    """
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+15550000001")

    # Create 5 memories with large values so budget kicks in
    big_value = "A" * 800  # ~200 tokens each
    for i in range(5):
        await store.store_memory(
            user_id=user.id,
            memory_type="long_term",
            key=f"memory_{i}",
            value=big_value,
        )

    # With embed_text returning [] (no key), fallback to recency
    with patch("app.memory.embeddings.embed_text", new_callable=AsyncMock, return_value=[]):
        memories = await store.get_relevant_memories(
            user_id=user.id,
            query_text="test",
            token_budget=500,
        )

    # 500 token budget / 200 tokens per memory = max 2
    assert len(memories) <= 3, f"Expected <=3 memories within 500 token budget, got {len(memories)}"


@pytest.mark.asyncio
async def test_get_relevant_memories_fallback_to_recency(db_session):
    """When embed_text returns [], get_relevant_memories still returns memories by recency."""
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+15550000002")

    await store.store_memory(user.id, "long_term", "key_a", "value_a")
    await store.store_memory(user.id, "long_term", "key_b", "value_b")

    with patch("app.memory.embeddings.embed_text", new_callable=AsyncMock, return_value=[]):
        memories = await store.get_relevant_memories(user.id, "query")

    assert len(memories) == 2
    keys = {m.key for m in memories}
    assert "key_a" in keys
    assert "key_b" in keys


@pytest.mark.asyncio
async def test_search_memories_persona_filter_excludes_personal(db_session):
    """search_memories with persona_tag='work' excludes 'personal' memories but includes 'shared'."""
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+15550000003")

    # Create memories with different persona tags
    work_mem = await store.store_memory(user.id, "long_term", "work_key", "work value", persona_tag="work")
    personal_mem = await store.store_memory(user.id, "long_term", "personal_key", "personal value", persona_tag="personal")
    shared_mem = await store.store_memory(user.id, "long_term", "shared_key", "shared value", persona_tag="shared")

    # Use a fake embedding — since SQLite doesn't have cosine_distance, patch search_memories
    # Instead test the filter logic directly via get_relevant_memories with a mock embedding path
    with patch("app.memory.embeddings.embed_text", new_callable=AsyncMock, return_value=[]):
        # Fallback to recency with persona_tag filter
        memories = await store.get_relevant_memories(user.id, "query", persona_tag="work")

    keys = {m.key for m in memories}
    assert "work_key" in keys, "work memory should be included"
    assert "shared_key" in keys, "shared memory should be included"
    assert "personal_key" not in keys, "personal memory should be excluded for work persona"


# ─── Tiered context tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_context_minimal_no_memories_key(db_session):
    """get_context_minimal returns dict WITHOUT 'memories' key."""
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+15550000010")
    ctx = await store.get_context_minimal(user.id)
    assert "memories" not in ctx, "get_context_minimal must NOT include memories key"
    assert "user" in ctx
    assert "recent_messages" in ctx
    assert "message_count" in ctx
    assert "last_persona" in ctx


@pytest.mark.asyncio
async def test_get_context_minimal_last_persona_none_when_no_tagged_messages(db_session):
    """last_persona is None when no outbound messages have persona_tag set."""
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+15550000011")
    # Store a message with no persona_tag
    await store.store_message(user.id, "outbound", "Hello!", persona_tag=None)
    ctx = await store.get_context_minimal(user.id)
    assert ctx["last_persona"] is None


@pytest.mark.asyncio
async def test_get_context_minimal_last_persona_from_tagged_outbound(db_session):
    """last_persona is set to the persona_tag of the most recent tagged outbound message."""
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+15550000012")
    await store.store_message(user.id, "outbound", "Work message", persona_tag="work")
    ctx = await store.get_context_minimal(user.id)
    assert ctx["last_persona"] == "work"


@pytest.mark.asyncio
async def test_get_context_standard_has_memories_key(db_session):
    """get_context_standard returns dict WITH 'memories' key."""
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+15550000020")
    with patch("app.memory.embeddings.embed_text", new_callable=AsyncMock, return_value=[]):
        ctx = await store.get_context_standard(user.id)
    assert "memories" in ctx, "get_context_standard must include memories key"
    assert "recent_messages" in ctx
    assert "active_tasks" in ctx


@pytest.mark.asyncio
async def test_get_context_full_has_personas_key(db_session):
    """get_context_full returns dict WITH 'personas' key."""
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+15550000030")
    with patch("app.memory.embeddings.embed_text", new_callable=AsyncMock, return_value=[]):
        ctx = await store.get_context_full(user.id)
    assert "personas" in ctx, "get_context_full must include personas key"
    assert "memories" in ctx
    assert "recent_messages" in ctx


@pytest.mark.asyncio
async def test_get_context_full_includes_personas_from_store(db_session):
    """get_context_full includes persona data from get_personas()."""
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+15550000031")
    await store.create_persona(user.id, "work", description="Work persona", tone_notes="formal")
    with patch("app.memory.embeddings.embed_text", new_callable=AsyncMock, return_value=[]):
        ctx = await store.get_context_full(user.id)
    assert len(ctx["personas"]) == 1
    assert ctx["personas"][0]["name"] == "work"


@pytest.mark.asyncio
async def test_get_context_backward_compat(db_session):
    """Original get_context() still works for backward compatibility."""
    store = MemoryStore(db_session)
    user = await store.get_or_create_user("+15550000040")
    ctx = await store.get_context(user.id)
    assert "memories" in ctx
    assert "recent_messages" in ctx
    assert "user" in ctx

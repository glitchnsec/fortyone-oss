"""
Integration tests for persona CRUD (MemoryStore) and persona detection (detect_persona).

Tests are structured for TDD:
  - Task 1 tests: Persona model, MemoryStore CRUD, Connection.persona_id
  - Task 2 tests: detect_persona() 3-tuple, needs_clarification logic, REST endpoints
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.database import Base


# ─── Test database setup ──────────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_session():
    """Provide a fresh async SQLite session for each test."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _create_user(session, phone="+15550001111", email=None):
    """Create a minimal User for tests."""
    from app.memory.models import User
    user = User(phone=phone, email=email)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


# ─── Task 1: Model, Store CRUD, Connection ────────────────────────────────────

@pytest.mark.asyncio
async def test_persona_model_importable():
    """Persona model can be imported and has required attributes."""
    from app.memory.models import Persona
    assert hasattr(Persona, "id")
    assert hasattr(Persona, "user_id")
    assert hasattr(Persona, "name")
    assert hasattr(Persona, "description")
    assert hasattr(Persona, "tone_notes")
    assert hasattr(Persona, "is_active")
    assert hasattr(Persona, "created_at")


@pytest.mark.asyncio
async def test_memory_has_persona_tag():
    """Memory model has persona_tag column."""
    from app.memory.models import Memory
    assert hasattr(Memory, "persona_tag")


@pytest.mark.asyncio
async def test_connection_has_persona_id():
    """Connection model in connections service has persona_id."""
    from connections.app.models import Connection
    assert hasattr(Connection, "persona_id")


@pytest.mark.asyncio
async def test_store_create_persona(db_session):
    """create_persona returns a Persona with correct fields."""
    from app.memory.store import MemoryStore
    user = await _create_user(db_session)
    store = MemoryStore(db_session)
    persona = await store.create_persona(
        user_id=user.id,
        name="work",
        description="PM at Acme",
        tone_notes=None,
    )
    assert persona.id is not None
    assert persona.user_id == user.id
    assert persona.name == "work"
    assert persona.description == "PM at Acme"
    assert persona.tone_notes is None
    assert persona.is_active is True


@pytest.mark.asyncio
async def test_store_get_personas_isolation(db_session):
    """get_personas only returns personas belonging to the given user."""
    from app.memory.store import MemoryStore
    user_a = await _create_user(db_session, phone="+15550001111")
    user_b = await _create_user(db_session, phone="+15550002222")
    store = MemoryStore(db_session)
    await store.create_persona(user_id=user_a.id, name="work")
    # User B should get empty list
    result = await store.get_personas(user_b.id)
    assert result == []


@pytest.mark.asyncio
async def test_store_delete_persona_wrong_user(db_session):
    """delete_persona returns False when user_id doesn't match."""
    from app.memory.store import MemoryStore
    user_a = await _create_user(db_session, phone="+15550001111")
    user_b = await _create_user(db_session, phone="+15550002222")
    store = MemoryStore(db_session)
    persona = await store.create_persona(user_id=user_a.id, name="work")
    result = await store.delete_persona(user_b.id, persona.id)
    assert result is False


@pytest.mark.asyncio
async def test_store_update_persona(db_session):
    """update_persona updates the name field."""
    from app.memory.store import MemoryStore
    user = await _create_user(db_session)
    store = MemoryStore(db_session)
    persona = await store.create_persona(user_id=user.id, name="work")
    updated = await store.update_persona(user.id, persona.id, name="personal")
    assert updated is not None
    assert updated.name == "personal"


@pytest.mark.asyncio
async def test_store_create_and_list_personas(db_session):
    """create two personas; list returns both."""
    from app.memory.store import MemoryStore
    user = await _create_user(db_session)
    store = MemoryStore(db_session)
    await store.create_persona(user_id=user.id, name="work")
    await store.create_persona(user_id=user.id, name="personal")
    personas = await store.get_personas(user.id)
    assert len(personas) == 2
    names = {p.name for p in personas}
    assert names == {"work", "personal"}


@pytest.mark.asyncio
async def test_store_memory_accepts_persona_tag(db_session):
    """store_memory accepts persona_tag kwarg without crashing."""
    from app.memory.store import MemoryStore
    user = await _create_user(db_session)
    store = MemoryStore(db_session)
    mem = await store.store_memory(
        user_id=user.id,
        memory_type="long_term",
        key="preference",
        value="coffee",
        persona_tag="work",
    )
    assert mem is not None
    assert mem.persona_tag == "work"


# ─── Task 2: detect_persona ────────────────────────────────────────────────────

def _make_persona(name, description=None):
    """Helper — creates a simple Persona-like object for detection tests."""
    from app.memory.models import Persona
    p = Persona.__new__(Persona)
    p.id = "test-id"
    p.name = name
    p.description = description
    p.tone_notes = None
    p.is_active = True
    return p


@pytest.mark.asyncio
async def test_detect_persona_work_signal():
    """Body containing a work signal → work persona, confidence > 0.5, no clarification."""
    from app.core.persona import detect_persona
    personas = [_make_persona("work"), _make_persona("personal")]
    name, conf, needs_clarification = await detect_persona(
        body="meeting with the team tomorrow",
        user_personas=personas,
        recent_messages=[],
    )
    assert name == "work"
    assert conf > 0.5
    assert needs_clarification is False


@pytest.mark.asyncio
async def test_detect_persona_personal_signal():
    """Body containing a personal signal → personal persona, confidence > 0.5, no clarification."""
    from app.core.persona import detect_persona
    personas = [_make_persona("work"), _make_persona("personal")]
    name, conf, needs_clarification = await detect_persona(
        body="gym session at 6pm",
        user_personas=personas,
        recent_messages=[],
    )
    assert name == "personal"
    assert conf > 0.5
    assert needs_clarification is False


@pytest.mark.asyncio
async def test_detect_persona_no_personas():
    """No personas → returns ('shared', 0.5, False)."""
    from app.core.persona import detect_persona
    result = await detect_persona(
        body="anything here",
        user_personas=[],
        recent_messages=[],
    )
    assert result == ("shared", 0.5, False)


@pytest.mark.asyncio
async def test_detect_persona_inherits_last():
    """Short body + last_persona='work' → inherits work persona, no clarification."""
    from app.core.persona import detect_persona
    personas = [_make_persona("work"), _make_persona("personal")]
    name, conf, needs_clarification = await detect_persona(
        body="ok",
        user_personas=personas,
        recent_messages=[],
        last_persona="work",
    )
    assert name == "work"
    assert needs_clarification is False


@pytest.mark.asyncio
async def test_detect_persona_needs_clarification():
    """LLM returns confidence < 0.6 on non-trivial body → needs_clarification=True."""
    from app.core.persona import detect_persona
    personas = [_make_persona("work"), _make_persona("personal")]

    mock_response = {"persona": "shared", "confidence": 0.4}
    with patch("app.tasks._llm.llm_messages_json", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_response
        name, conf, needs_clarification = await detect_persona(
            body="I need to handle something important today",  # 8 words, >= 5
            user_personas=personas,
            recent_messages=[],
        )
    assert needs_clarification is True


@pytest.mark.asyncio
async def test_detect_persona_llm_error_no_crash():
    """LLM raises exception → returns ('shared', 0.5, False) without crashing."""
    from app.core.persona import detect_persona
    personas = [_make_persona("work"), _make_persona("personal")]

    with patch("app.tasks._llm.llm_messages_json", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = RuntimeError("LLM exploded")
        result = await detect_persona(
            body="something ambiguous here to trigger LLM path",
            user_personas=personas,
            recent_messages=[],
        )
    assert result == ("shared", 0.5, False)


# ─── Task 2: REST endpoint tests ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_personas_router_importable():
    """personas router can be imported and has the correct prefix."""
    from app.routes.personas import router
    assert router.prefix == "/api/v1/personas"


@pytest.mark.asyncio
async def test_personas_router_registered_in_main():
    """personas router is registered in app/main.py."""
    import ast
    import os
    main_path = os.path.join(os.path.dirname(__file__), "..", "app", "main.py")
    with open(main_path) as f:
        src = f.read()
    assert "personas" in src, "personas router not registered in main.py"

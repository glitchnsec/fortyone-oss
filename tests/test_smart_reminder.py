"""
Tests for smart reminder action_type classification (Phase 4.1, Plan 01).

Verifies:
  - REMINDER_SYSTEM prompt contains action_type field description
  - handle_reminder stores action_type="notify" in metadata for standard reminders
  - handle_reminder stores action_type="execute" in metadata for execute reminders
  - Missing action_type from LLM defaults to "notify"
  - Invalid action_type from LLM normalizes to "notify"
  - Manager override (_manager_action_type) takes precedence over LLM extraction
"""
import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.memory.models import Base, Task
from app.memory.store import MemoryStore


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    async with AsyncSession() as session:
        yield session
    await engine.dispose()


def _make_llm_response(action_type=None, include_action_type=True):
    """Build a mock LLM extraction response with configurable action_type."""
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    payload = {
        "task": "call mom",
        "due_at": future.isoformat(),
        "recurrence": "none",
        "contact": None,
        "confirmation": "Got it! I'll remind you about that.",
    }
    if include_action_type:
        payload["action_type"] = action_type
    return payload


# ─── Test 1: Prompt contains action_type ─────────────────────────────────────

def test_reminder_system_prompt_contains_action_type():
    """REMINDER_SYSTEM prompt string contains action_type with notify and execute."""
    from app.tasks.reminder import REMINDER_SYSTEM
    assert "action_type" in REMINDER_SYSTEM
    assert "notify" in REMINDER_SYSTEM
    assert "execute" in REMINDER_SYSTEM


# ─── Test 2: notify action_type stored in metadata ───────────────────────────

@pytest.mark.asyncio
async def test_handle_reminder_stores_notify_action_type(db_session):
    """handle_reminder with LLM returning action_type='notify' stores it in metadata."""
    mock_response = _make_llm_response(action_type="notify")

    with patch("app.tasks.reminder.llm_messages_json", new_callable=AsyncMock, return_value=mock_response), \
         patch("app.tasks.reminder.AsyncSessionLocal") as mock_session_cls, \
         patch("app.tasks.reminder._schedule_task_reminder", new_callable=AsyncMock):

        # Make AsyncSessionLocal return our test session as async context manager
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.tasks.reminder import handle_reminder
        result = await handle_reminder({
            "job_id": "test-j1",
            "phone": "+15555550001",
            "body": "remind me to call mom tonight",
            "context": {"memories": {"timezone": "America/New_York"}},
        })

    assert "task_id" in result

    # Verify the task was stored with action_type in metadata
    from sqlalchemy import select
    stmt = select(Task).where(Task.id == result["task_id"])
    row = await db_session.execute(stmt)
    task = row.scalar_one()
    metadata = json.loads(task.metadata_json)
    assert metadata["action_type"] == "notify"


# ─── Test 3: execute action_type stored in metadata ──────────────────────────

@pytest.mark.asyncio
async def test_handle_reminder_stores_execute_action_type(db_session):
    """handle_reminder with LLM returning action_type='execute' stores it in metadata."""
    mock_response = _make_llm_response(action_type="execute")

    with patch("app.tasks.reminder.llm_messages_json", new_callable=AsyncMock, return_value=mock_response), \
         patch("app.tasks.reminder.AsyncSessionLocal") as mock_session_cls, \
         patch("app.tasks.reminder._schedule_task_reminder", new_callable=AsyncMock):

        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.tasks.reminder import handle_reminder
        result = await handle_reminder({
            "job_id": "test-j2",
            "phone": "+15555550002",
            "body": "tell me a joke in 5 minutes",
            "context": {"memories": {"timezone": "America/New_York"}},
        })

    assert "task_id" in result

    from sqlalchemy import select
    stmt = select(Task).where(Task.id == result["task_id"])
    row = await db_session.execute(stmt)
    task = row.scalar_one()
    metadata = json.loads(task.metadata_json)
    assert metadata["action_type"] == "execute"


# ─── Test 4: missing action_type defaults to notify ──────────────────────────

@pytest.mark.asyncio
async def test_handle_reminder_missing_action_type_defaults_notify(db_session):
    """When LLM returns no action_type field, defaults to 'notify'."""
    mock_response = _make_llm_response(include_action_type=False)

    with patch("app.tasks.reminder.llm_messages_json", new_callable=AsyncMock, return_value=mock_response), \
         patch("app.tasks.reminder.AsyncSessionLocal") as mock_session_cls, \
         patch("app.tasks.reminder._schedule_task_reminder", new_callable=AsyncMock):

        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.tasks.reminder import handle_reminder
        result = await handle_reminder({
            "job_id": "test-j3",
            "phone": "+15555550003",
            "body": "remind me to call mom tonight",
            "context": {"memories": {"timezone": "America/New_York"}},
        })

    assert "task_id" in result

    from sqlalchemy import select
    stmt = select(Task).where(Task.id == result["task_id"])
    row = await db_session.execute(stmt)
    task = row.scalar_one()
    metadata = json.loads(task.metadata_json)
    assert metadata["action_type"] == "notify"


# ─── Test 5: invalid action_type normalizes to notify ────────────────────────

@pytest.mark.asyncio
async def test_handle_reminder_invalid_action_type_normalizes_to_notify(db_session):
    """When LLM returns an invalid action_type like 'do_it', normalizes to 'notify'."""
    mock_response = _make_llm_response(action_type="do_it")

    with patch("app.tasks.reminder.llm_messages_json", new_callable=AsyncMock, return_value=mock_response), \
         patch("app.tasks.reminder.AsyncSessionLocal") as mock_session_cls, \
         patch("app.tasks.reminder._schedule_task_reminder", new_callable=AsyncMock):

        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.tasks.reminder import handle_reminder
        result = await handle_reminder({
            "job_id": "test-j4",
            "phone": "+15555550004",
            "body": "remind me to call mom tonight",
            "context": {"memories": {"timezone": "America/New_York"}},
        })

    assert "task_id" in result

    from sqlalchemy import select
    stmt = select(Task).where(Task.id == result["task_id"])
    row = await db_session.execute(stmt)
    task = row.scalar_one()
    metadata = json.loads(task.metadata_json)
    assert metadata["action_type"] == "notify"


# ─── Test 6: Manager override takes precedence ──────────────────────────────

@pytest.mark.asyncio
async def test_manager_override_takes_precedence(db_session):
    """_manager_action_type in payload overrides LLM extraction."""
    # LLM says notify, but manager says execute
    mock_response = _make_llm_response(action_type="notify")

    with patch("app.tasks.reminder.llm_messages_json", new_callable=AsyncMock, return_value=mock_response), \
         patch("app.tasks.reminder.AsyncSessionLocal") as mock_session_cls, \
         patch("app.tasks.reminder._schedule_task_reminder", new_callable=AsyncMock):

        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.tasks.reminder import handle_reminder
        result = await handle_reminder({
            "job_id": "test-j5",
            "phone": "+15555550005",
            "body": "tell me a joke in 5 minutes",
            "context": {"memories": {"timezone": "America/New_York"}},
            "_manager_action_type": "execute",
        })

    assert "task_id" in result

    from sqlalchemy import select
    stmt = select(Task).where(Task.id == result["task_id"])
    row = await db_session.execute(stmt)
    task = row.scalar_one()
    metadata = json.loads(task.metadata_json)
    assert metadata["action_type"] == "execute"

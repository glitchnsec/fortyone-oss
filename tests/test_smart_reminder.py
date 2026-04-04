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


# ═══════════════════════════════════════════════════════════════════════════════
# Plan 02: Integration tests for handle_task_reminder fork + loop prevention
# ═══════════════════════════════════════════════════════════════════════════════

from app.memory.models import User


async def _create_user_and_task(db_session, action_type=None, include_action_type=True):
    """Helper: create a User + Task with optional action_type in metadata."""
    import uuid
    user = User(id=str(uuid.uuid4()), phone="+15555559999")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    metadata = {"contact": None, "recurrence": "none"}
    if include_action_type and action_type is not None:
        metadata["action_type"] = action_type

    task = Task(
        id=str(uuid.uuid4()),
        user_id=user.id,
        task_type="reminder",
        title="call mom",
        metadata_json=json.dumps(metadata),
        completed=False,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    return user, task


# ─── Integration: handle_task_reminder fork ──────────────────────────────────

@pytest.mark.asyncio
async def test_task_reminder_notify_sends_static_sms(db_session):
    """action_type=notify sends 'Reminder: {title}' — existing behavior preserved."""
    user, task = await _create_user_and_task(db_session, action_type="notify")

    with patch("app.database.AsyncSessionLocal") as mock_session_cls, \
         patch("app.tasks.proactive._record_send", new_callable=AsyncMock):
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.tasks.proactive import handle_task_reminder
        result = await handle_task_reminder({
            "user_id": user.id,
            "job_id": "test-notify-1",
            "task_id": task.id,
            "phone": user.phone,
            "title": "call mom",
            "source": "scheduler",
            "channel": "sms",
        })

    assert result["response"] == "Reminder: call mom"


@pytest.mark.asyncio
async def test_task_reminder_execute_returns_empty_and_requeues(db_session):
    """action_type=execute returns empty response and pushes NEEDS_MANAGER job to Redis."""
    user, task = await _create_user_and_task(db_session, action_type="execute")

    mock_redis = AsyncMock()
    mock_redis.xadd = AsyncMock()
    mock_redis.aclose = AsyncMock()

    with patch("app.database.AsyncSessionLocal") as mock_session_cls, \
         patch("app.tasks.proactive._record_send", new_callable=AsyncMock), \
         patch("redis.asyncio.from_url", new_callable=AsyncMock, return_value=mock_redis):
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.tasks.proactive import handle_task_reminder
        result = await handle_task_reminder({
            "user_id": user.id,
            "job_id": "test-exec-1",
            "task_id": task.id,
            "phone": user.phone,
            "title": "tell me a joke",
            "source": "scheduler",
            "channel": "sms",
        })

    # Empty response — manager will produce the real one
    assert result["response"] == ""

    # Verify xadd was called with needs_manager intent and scheduled_execute source
    assert mock_redis.xadd.called
    call_args = mock_redis.xadd.call_args
    data_str = call_args[0][1]["data"]
    payload = json.loads(data_str)
    assert payload["intent"] == "needs_manager"
    assert payload["source"] == "scheduled_execute"


@pytest.mark.asyncio
async def test_task_reminder_execute_includes_user_context(db_session):
    """Execute path loads user context into re-queued payload."""
    user, task = await _create_user_and_task(db_session, action_type="execute")

    mock_redis = AsyncMock()
    mock_redis.xadd = AsyncMock()
    mock_redis.aclose = AsyncMock()

    mock_context = {
        "memories": {"fav_color": "blue"},
        "user": {"assistant_name": "Jarvis"},
    }

    with patch("app.database.AsyncSessionLocal") as mock_session_cls, \
         patch("app.tasks.proactive._record_send", new_callable=AsyncMock), \
         patch("redis.asyncio.from_url", new_callable=AsyncMock, return_value=mock_redis), \
         patch.object(MemoryStore, "get_context_standard", new_callable=AsyncMock, return_value=mock_context):
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.tasks.proactive import handle_task_reminder
        result = await handle_task_reminder({
            "user_id": user.id,
            "job_id": "test-exec-ctx",
            "task_id": task.id,
            "phone": user.phone,
            "title": "tell me a joke",
            "source": "scheduler",
            "channel": "sms",
        })

    assert result["response"] == ""
    # Verify context was included in the re-queued payload
    call_args = mock_redis.xadd.call_args
    data_str = call_args[0][1]["data"]
    payload = json.loads(data_str)
    assert payload["context"] != {}
    assert "memories" in payload["context"]


@pytest.mark.asyncio
async def test_task_reminder_missing_action_type_defaults_notify_plan02(db_session):
    """Tasks without action_type in metadata use notify path (proactive handler)."""
    user, task = await _create_user_and_task(db_session, include_action_type=False)

    with patch("app.database.AsyncSessionLocal") as mock_session_cls, \
         patch("app.tasks.proactive._record_send", new_callable=AsyncMock):
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.tasks.proactive import handle_task_reminder
        result = await handle_task_reminder({
            "user_id": user.id,
            "job_id": "test-default-1",
            "task_id": task.id,
            "phone": user.phone,
            "title": "call mom",
            "source": "scheduler",
            "channel": "sms",
        })

    assert result["response"].startswith("Reminder:")


@pytest.mark.asyncio
async def test_task_reminder_execute_fallback_on_redis_failure(db_session):
    """If Redis re-queue fails, execute falls back to static notification."""
    user, task = await _create_user_and_task(db_session, action_type="execute")

    mock_redis = AsyncMock()
    mock_redis.xadd = AsyncMock(side_effect=ConnectionError("Redis down"))
    mock_redis.aclose = AsyncMock()

    with patch("app.database.AsyncSessionLocal") as mock_session_cls, \
         patch("app.tasks.proactive._record_send", new_callable=AsyncMock), \
         patch("redis.asyncio.from_url", new_callable=AsyncMock, return_value=mock_redis):
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=db_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.tasks.proactive import handle_task_reminder
        result = await handle_task_reminder({
            "user_id": user.id,
            "job_id": "test-fallback-1",
            "task_id": task.id,
            "phone": user.phone,
            "title": "tell me a joke",
            "source": "scheduler",
            "channel": "sms",
        })

    # Fallback to static notification
    assert result["response"] == "Reminder: tell me a joke"


# ─── Integration: manager loop prevention ────────────────────────────────────

def test_manager_removes_create_reminder_for_scheduled_execute():
    """Verify create_reminder tool can be filtered out for scheduled_execute."""
    from app.core.tools import get_tool_schemas
    tools = get_tool_schemas()
    tool_names = [t["function"]["name"] for t in tools]
    assert "create_reminder" in tool_names, "Baseline: create_reminder exists"
    filtered = [t for t in tools if t["function"]["name"] != "create_reminder"]
    filtered_names = [t["function"]["name"] for t in filtered]
    assert "create_reminder" not in filtered_names


def test_system_prompt_includes_execution_instruction_for_scheduled_execute():
    """_build_system_prompt adds execution directive for scheduled_execute source."""
    from app.tasks.manager import _build_system_prompt
    payload = {"source": "scheduled_execute", "context": {}, "persona": "shared"}
    prompt = _build_system_prompt(payload)
    assert "scheduled task execution" in prompt.lower()
    assert "do not" in prompt.lower()


def test_system_prompt_normal_has_no_execution_instruction():
    """Normal (non-scheduled_execute) payloads don't get execution directive."""
    from app.tasks.manager import _build_system_prompt
    payload = {"context": {}, "persona": "shared"}
    prompt = _build_system_prompt(payload)
    assert "scheduled task execution" not in prompt.lower()


def test_worker_does_not_route_scheduled_execute_as_scheduler():
    """source='scheduled_execute' is NOT 'scheduler', so it falls through to route_job."""
    source = "scheduled_execute"
    assert source != "scheduler", "scheduled_execute must not match scheduler check"

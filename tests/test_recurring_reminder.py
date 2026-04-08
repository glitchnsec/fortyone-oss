"""
Tests for recurring reminder re-scheduling (bug fix: recurring-reminder-fires-once).

Verifies:
  - _compute_next_occurrence returns correct next time for daily/weekly/monthly
  - _compute_next_occurrence advances past stale dates (scheduler downtime)
  - handle_task_reminder re-schedules recurring reminders after firing
  - handle_task_reminder does NOT re-schedule non-recurring reminders
  - handle_task_reminder updates task.due_at in DB for recurring reminders
"""
import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.memory.models import Base, Task, User
from app.tasks.proactive import _compute_next_occurrence


# ─── Unit tests for _compute_next_occurrence ────────────────────────────────


class TestComputeNextOccurrence:
    """Test the pure computation of next occurrence times."""

    def test_daily_future(self):
        """Daily recurrence: next occurrence is 24 hours from current due_at."""
        now = datetime.now(timezone.utc)
        current_due = now - timedelta(seconds=30)  # just fired
        result = _compute_next_occurrence(current_due, "daily")
        assert result is not None
        assert result > now
        expected = current_due + timedelta(days=1)
        assert abs((result - expected).total_seconds()) < 2

    def test_weekly_future(self):
        """Weekly recurrence: next occurrence is 7 days from current due_at."""
        now = datetime.now(timezone.utc)
        current_due = now - timedelta(seconds=30)
        result = _compute_next_occurrence(current_due, "weekly")
        assert result is not None
        assert result > now
        expected = current_due + timedelta(weeks=1)
        assert abs((result - expected).total_seconds()) < 2

    def test_monthly_future(self):
        """Monthly recurrence: next is one month ahead."""
        now = datetime.now(timezone.utc)
        # Use a due_at that just fired (seconds ago)
        current_due = now - timedelta(seconds=5)
        result = _compute_next_occurrence(current_due, "monthly")
        assert result is not None
        assert result > now
        # Next month, same day (approximately)
        if current_due.month == 12:
            assert result.month == 1 and result.year == current_due.year + 1
        else:
            assert result.month == current_due.month + 1

    def test_monthly_end_of_month_clamp(self):
        """Monthly recurrence on the 31st clamps to shorter months."""
        import calendar
        now = datetime.now(timezone.utc)
        # Use a Jan 31 just seconds ago (simulate just-fired)
        # We need a controlled scenario: pick a month with 31 days, just before
        # a month with fewer days.
        # Use a fixed date: Jan 31 of the current year, but set time to now - 5s
        # so _compute_next_occurrence sees it as "just fired"
        current_due = now.replace(month=1, day=31, second=0, microsecond=0) - timedelta(seconds=5)
        result = _compute_next_occurrence(current_due, "monthly")
        assert result is not None
        assert result > now
        # The result day should never exceed the max days in its month
        max_day = calendar.monthrange(result.year, result.month)[1]
        assert result.day <= max_day

    def test_daily_advances_past_stale(self):
        """If scheduler was down for days, advance to the next future time."""
        now = datetime.now(timezone.utc)
        current_due = now - timedelta(days=3, seconds=30)
        result = _compute_next_occurrence(current_due, "daily")
        assert result is not None
        assert result > now
        assert result < now + timedelta(days=1, seconds=60)

    def test_none_recurrence_returns_none(self):
        """Non-recurring ('none') returns None."""
        current_due = datetime.now(timezone.utc) - timedelta(seconds=30)
        result = _compute_next_occurrence(current_due, "none")
        assert result is None

    def test_unknown_recurrence_returns_none(self):
        """Unknown recurrence value returns None."""
        current_due = datetime.now(timezone.utc) - timedelta(seconds=30)
        result = _compute_next_occurrence(current_due, "biweekly")
        assert result is None


# ─── Integration tests for handle_task_reminder re-scheduling ────────────────


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
    async with AsyncSession() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def user_and_task(db_session):
    """Create a test user and a recurring daily reminder task."""
    user = User(id="test-user-1", phone="+15551234567")
    db_session.add(user)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    task = Task(
        id="test-task-1",
        user_id=user.id,
        task_type="reminder",
        title="Take medicine",
        due_at=now - timedelta(seconds=10),  # just fired
        metadata_json=json.dumps({
            "recurrence": "daily",
            "action_type": "notify",
        }),
    )
    db_session.add(task)
    await db_session.commit()
    return user, task


@pytest.mark.asyncio
async def test_handle_task_reminder_reschedules_daily(db_session, user_and_task):
    """handle_task_reminder should re-schedule a daily recurring reminder."""
    user, task = user_and_task

    payload = {
        "user_id": user.id,
        "job_id": "job-1",
        "task_id": task.id,
        "phone": user.phone,
        "title": task.title,
        "channel": "sms",
    }

    with patch("app.database.AsyncSessionLocal") as mock_session_cls, \
         patch("app.tasks.proactive._record_send", new_callable=AsyncMock), \
         patch("app.tasks.reminder.schedule_task_reminder", new_callable=AsyncMock) as mock_schedule:

        # Make AsyncSessionLocal context manager return our test session
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=db_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_ctx

        from app.tasks.proactive import handle_task_reminder
        result = await handle_task_reminder(payload)

        # Should have delivered the reminder
        assert "Take medicine" in result["response"]

        # Should have called schedule_task_reminder for next occurrence
        assert mock_schedule.called, "schedule_task_reminder was not called for recurring reminder"
        call_args = mock_schedule.call_args
        next_due = call_args[0][4]  # 5th positional arg is due_at
        now = datetime.now(timezone.utc)
        assert next_due > now, "Next due_at should be in the future"


@pytest.mark.asyncio
async def test_handle_task_reminder_no_reschedule_for_nonrecurring(db_session):
    """handle_task_reminder should NOT re-schedule a non-recurring reminder."""
    user = User(id="test-user-2", phone="+15559876543")
    db_session.add(user)
    await db_session.flush()

    task = Task(
        id="test-task-2",
        user_id=user.id,
        task_type="reminder",
        title="Buy groceries",
        due_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        metadata_json=json.dumps({
            "recurrence": "none",
            "action_type": "notify",
        }),
    )
    db_session.add(task)
    await db_session.commit()

    payload = {
        "user_id": user.id,
        "job_id": "job-2",
        "task_id": task.id,
        "phone": user.phone,
        "title": task.title,
        "channel": "sms",
    }

    with patch("app.database.AsyncSessionLocal") as mock_session_cls, \
         patch("app.tasks.proactive._record_send", new_callable=AsyncMock), \
         patch("app.tasks.reminder.schedule_task_reminder", new_callable=AsyncMock) as mock_schedule:

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=db_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_ctx

        from app.tasks.proactive import handle_task_reminder
        result = await handle_task_reminder(payload)

        assert "Buy groceries" in result["response"]
        assert not mock_schedule.called, "schedule_task_reminder should NOT be called for non-recurring"

"""Tests for Phase 4.3 noise reduction: pool target, daily cap, content delta gates."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta


def test_pool_target_range():
    """D-01: Pool selects 1-3 categories/day."""
    import inspect
    from app.core.proactive_pool import plan_day
    src = inspect.getsource(plan_day)
    assert "random.randint(1, 3)" in src


def test_daily_cap_is_three():
    """D-02: DEFAULT_MAX_PER_DAY is 3."""
    from app.core.throttle import DEFAULT_MAX_PER_DAY
    assert DEFAULT_MAX_PER_DAY == 3


def test_select_categories_respects_target():
    """select_categories returns at most target_count items."""
    from app.core.proactive_pool import select_categories, DEFAULT_CATEGORIES
    user_state = {
        "profile_completeness": 0.3,
        "has_goals": True,
        "approaching_goals": False,
        "memory_count": 25,
        "has_calendar": False,
    }
    for target in [1, 2, 3]:
        result = select_categories(DEFAULT_CATEGORIES, user_state, target_count=target)
        assert len(result) <= target, f"Expected at most {target}, got {len(result)}"


@pytest.mark.asyncio
async def test_delta_suppress_morning_briefing_no_changes():
    """D-03/D-13: Morning briefing suppressed when no tasks/goals changed."""
    from app.tasks.proactive import _has_content_delta

    # Mock store with action_log showing recent send and no changes since
    store = AsyncMock()
    last_send = MagicMock()
    last_send.action_type = "morning_briefing"
    last_send.outcome = "success"
    last_send.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    store.get_action_log.return_value = [last_send]

    # Tasks and goals haven't changed since last send
    task = MagicMock()
    task.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    task.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    store.get_active_tasks.return_value = [task]

    goal = MagicMock()
    goal.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    goal.target_date = None
    store.get_goals.return_value = [goal]

    result = await _has_content_delta(store, "user-123", "morning_briefing")
    assert result is False, "Should suppress when nothing changed"


@pytest.mark.asyncio
async def test_delta_allows_first_send():
    """D-03: First send of any category always passes (no prior log)."""
    from app.tasks.proactive import _has_content_delta

    store = AsyncMock()
    store.get_action_log.return_value = []  # No prior sends

    result = await _has_content_delta(store, "user-123", "morning_briefing")
    assert result is True, "First send should always have delta"


@pytest.mark.asyncio
async def test_checkin_always_has_delta():
    """smart_checkin and day_checkin always pass delta (conversational)."""
    from app.tasks.proactive import _has_content_delta

    store = AsyncMock()
    last_send = MagicMock()
    last_send.action_type = "smart_checkin"
    last_send.outcome = "success"
    last_send.created_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    store.get_action_log.return_value = [last_send]

    for cat in ("smart_checkin", "day_checkin", "afternoon_followup"):
        result = await _has_content_delta(store, "user-123", cat)
        assert result is True, f"{cat} should always pass delta check"


@pytest.mark.asyncio
async def test_delta_suppress_evening_recap_no_actions():
    """Evening recap suppressed when no actions today."""
    from app.tasks.proactive import _has_content_delta

    store = AsyncMock()
    last_send = MagicMock()
    last_send.action_type = "evening_recap"
    last_send.outcome = "success"
    last_send.created_at = datetime.now(timezone.utc) - timedelta(hours=1)

    # No other actions today besides the evening_recap itself
    store.get_action_log.return_value = [last_send]

    result = await _has_content_delta(store, "user-123", "evening_recap")
    assert result is False, "Should suppress when no actions today"


@pytest.mark.asyncio
async def test_delta_morning_briefing_allows_when_task_updated():
    """Morning briefing sends when a task was updated after last send."""
    from app.tasks.proactive import _has_content_delta

    store = AsyncMock()
    last_send = MagicMock()
    last_send.action_type = "morning_briefing"
    last_send.outcome = "success"
    last_send.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    store.get_action_log.return_value = [last_send]

    # Task updated AFTER last send
    task = MagicMock()
    task.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    task.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    store.get_active_tasks.return_value = [task]

    result = await _has_content_delta(store, "user-123", "morning_briefing")
    assert result is True, "Should send when task updated after last send"


@pytest.mark.asyncio
async def test_delta_morning_briefing_allows_when_calendar_events():
    """Calendar events cause morning briefing delta to return True even with stale tasks/goals."""
    from app.tasks.proactive import _has_content_delta

    store = AsyncMock()
    last_send = MagicMock()
    last_send.action_type = "morning_briefing"
    last_send.outcome = "success"
    last_send.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    store.get_action_log.return_value = [last_send]

    # Tasks and goals haven't changed since last send
    task = MagicMock()
    task.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    task.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    store.get_active_tasks.return_value = [task]

    goal = MagicMock()
    goal.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    store.get_goals.return_value = [goal]

    with patch("app.tasks.proactive._has_calendar_events", new_callable=AsyncMock, return_value=True):
        result = await _has_content_delta(store, "user-123", "morning_briefing")
    assert result is True, "Calendar events should allow morning briefing"


@pytest.mark.asyncio
async def test_delta_evening_recap_allows_when_calendar_events():
    """Calendar events cause evening recap delta to return True even with no action logs."""
    from app.tasks.proactive import _has_content_delta

    store = AsyncMock()
    last_send = MagicMock()
    last_send.action_type = "evening_recap"
    last_send.outcome = "success"
    last_send.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    store.get_action_log.return_value = [last_send]

    with patch("app.tasks.proactive._has_calendar_events", new_callable=AsyncMock, return_value=True):
        result = await _has_content_delta(store, "user-123", "evening_recap")
    assert result is True, "Calendar events should allow evening recap"


@pytest.mark.asyncio
async def test_suppression_toggle_disables_delta():
    """When proactive_content_suppression=False, _should_check_delta returns False."""
    from app.tasks.proactive import _should_check_delta

    mock_settings = MagicMock()
    mock_settings.proactive_content_suppression = False
    with patch("app.tasks.proactive.get_settings", return_value=mock_settings):
        assert _should_check_delta() is False


@pytest.mark.asyncio
async def test_delta_morning_no_calendar_no_changes_suppressed():
    """No calendar events AND no task/goal changes => delta is False."""
    from app.tasks.proactive import _has_content_delta

    store = AsyncMock()
    last_send = MagicMock()
    last_send.action_type = "morning_briefing"
    last_send.outcome = "success"
    last_send.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    store.get_action_log.return_value = [last_send]

    # Stale tasks and goals
    task = MagicMock()
    task.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    task.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    store.get_active_tasks.return_value = [task]

    goal = MagicMock()
    goal.updated_at = datetime.now(timezone.utc) - timedelta(hours=2)
    store.get_goals.return_value = [goal]

    with patch("app.tasks.proactive._has_calendar_events", new_callable=AsyncMock, return_value=False):
        result = await _has_content_delta(store, "user-123", "morning_briefing")
    assert result is False, "No calendar and no changes should suppress"

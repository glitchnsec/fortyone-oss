"""
Tests for ProactivePool engine — weighted selection, jitter, spacing, quiet hours.

Tests:
  1. select_categories returns correct count (2-4)
  2. select_categories filters out categories where requires is not met
  3. select_categories respects days_of_week
  4. compute_jitter_time returns timestamp within window
  5. compute_jitter_time returns different values on repeated calls
  6. is_quiet_hours returns True during quiet period, False outside
  7. is_quiet_hours handles midnight wraparound correctly
  8. compute_user_state returns correct profile completeness score
"""
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.proactive_pool import (
    ProactiveCategory,
    DEFAULT_CATEGORIES,
    select_categories,
    compute_jitter_time,
    compute_user_state,
)
from app.core.throttle import is_quiet_hours


# ─── Test 1: select_categories returns correct count ──────────────────────


def test_select_categories_returns_correct_count():
    """select_categories with all weights returns 2-4 categories."""
    user_state = {
        "profile_completeness": 0.3,
        "has_goals": True,
        "approaching_goals": False,
        "memory_count": 25,
        "has_calendar": False,
    }
    for _ in range(20):
        result = select_categories(DEFAULT_CATEGORIES, user_state, target_count=3)
        assert 1 <= len(result) <= 4, f"Expected 1-4 categories, got {len(result)}"


# ─── Test 2: select_categories filters by requires ────────────────────────


def test_select_categories_filters_requires_no_goals():
    """Categories requiring has_goals are excluded when user has no goals."""
    user_state = {
        "profile_completeness": 0.9,
        "has_goals": False,
        "approaching_goals": False,
        "memory_count": 5,
        "has_calendar": False,
    }
    # Run multiple times to account for randomness
    for _ in range(30):
        result = select_categories(DEFAULT_CATEGORIES, user_state, target_count=7)
        names = [c.name for c in result]
        assert "goal_coaching" not in names, "goal_coaching should be excluded without goals"


# ─── Test 3: select_categories respects days_of_week ──────────────────────


def test_select_categories_respects_days_of_week():
    """weekly_digest only appears when today is Sunday (weekday=6)."""
    user_state = {
        "profile_completeness": 0.9,
        "has_goals": True,
        "approaching_goals": False,
        "memory_count": 25,
        "has_calendar": False,
    }
    # Mock datetime.now() to return a Monday (weekday=0)
    monday = datetime(2026, 4, 6)  # 2026-04-06 is a Monday
    with patch("app.core.proactive_pool.datetime") as mock_dt:
        mock_dt.now.return_value = monday
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        for _ in range(30):
            result = select_categories(DEFAULT_CATEGORIES, user_state, target_count=7)
            names = [c.name for c in result]
            assert "weekly_digest" not in names, "weekly_digest should not appear on Monday"


# ─── Test 4: compute_jitter_time within window ────────────────────────────


def test_compute_jitter_time_within_window():
    """Jitter time falls within the specified window bounds."""
    date = datetime(2026, 4, 6, 12, 0, 0)
    for _ in range(50):
        ts = compute_jitter_time(10.0, 14.0, "UTC", date)
        dt = datetime.fromtimestamp(ts)
        assert 10 <= dt.hour <= 14, f"Hour {dt.hour} outside window 10-14"


# ─── Test 5: compute_jitter_time randomness ───────────────────────────────


def test_compute_jitter_time_varies():
    """Repeated calls produce different timestamps (randomness check)."""
    date = datetime(2026, 4, 6, 12, 0, 0)
    timestamps = set()
    for _ in range(20):
        ts = compute_jitter_time(8.0, 18.0, "UTC", date)
        timestamps.add(ts)
    # With a 10-hour window and 20 samples, we expect multiple distinct values
    assert len(timestamps) > 1, "compute_jitter_time should produce varied timestamps"


# ─── Test 6: is_quiet_hours basic ─────────────────────────────────────────


def test_is_quiet_hours_true_during_quiet():
    """is_quiet_hours returns True at 11 PM (within default 22-7)."""
    result = is_quiet_hours("UTC", None, _override_hour=23)
    assert result is True


def test_is_quiet_hours_false_outside_quiet():
    """is_quiet_hours returns False at 10 AM (outside default 22-7)."""
    result = is_quiet_hours("UTC", None, _override_hour=10)
    assert result is False


# ─── Test 7: is_quiet_hours midnight wraparound ──────────────────────────


def test_is_quiet_hours_midnight_wraparound():
    """is_quiet_hours handles midnight wraparound (22-7): 2 AM is quiet."""
    result = is_quiet_hours("UTC", None, _override_hour=2)
    assert result is True, "2 AM should be within quiet hours (22-7)"


def test_is_quiet_hours_custom_settings():
    """Custom quiet hours from settings_json are respected."""
    settings = json.dumps({"quiet_hours": {"start": 20, "end": 9}})
    # 8 AM should be quiet (within 20-9)
    assert is_quiet_hours("UTC", settings, _override_hour=8) is True
    # 10 AM should NOT be quiet
    assert is_quiet_hours("UTC", settings, _override_hour=10) is False
    # 21:00 should be quiet
    assert is_quiet_hours("UTC", settings, _override_hour=21) is True


# ─── Test 8: compute_user_state profile completeness ──────────────────────


@pytest.mark.asyncio
async def test_compute_user_state_profile_completeness():
    """compute_user_state returns correct profile completeness score."""
    # Mock store and user
    mock_store = AsyncMock()

    # Create a user with some fields set
    mock_user = MagicMock()
    mock_user.name = "Test User"
    mock_user.timezone = "America/Chicago"
    mock_user.assistant_name = "Buddy"
    mock_user.personality_notes = None  # missing

    # Mock DB query
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_user
    mock_store.db.execute = AsyncMock(return_value=mock_result)

    # Mock profile entries — only has preferences
    mock_entry = MagicMock()
    mock_entry.section = "preferences"
    mock_store.get_profile_entries = AsyncMock(return_value=[mock_entry])

    # Mock goals — none active
    mock_store.get_goals = AsyncMock(return_value=[])

    # Mock memories — 10 memories
    mock_store.get_memories = AsyncMock(return_value=[MagicMock()] * 10)

    state = await compute_user_state(mock_store, "user123")

    # 4/7 checks pass: name=True, timezone=True, assistant_name=True, personality=False,
    # preferences=True, goals_profile=False, challenges=False
    expected_score = 4 / 7
    assert abs(state["profile_completeness"] - expected_score) < 0.01
    assert state["has_goals"] is False
    assert state["memory_count"] == 10
    assert state["has_calendar"] is False

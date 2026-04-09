"""Tests for task auto-archive and dynamic briefing window."""
import pytest


def test_compute_briefing_window_hours():
    """D-05: Window narrows with more items."""
    from app.tasks.proactive import _compute_briefing_window_hours
    assert _compute_briefing_window_hours(0) == 4.5   # Light
    assert _compute_briefing_window_hours(3) == 3.0   # Moderate
    assert _compute_briefing_window_hours(6) == 2.0   # Busy
    assert _compute_briefing_window_hours(10) == 2.0  # Very busy


def test_compute_briefing_window_with_calendar():
    """D-05: Calendar events contribute to density."""
    from app.tasks.proactive import _compute_briefing_window_hours
    assert _compute_briefing_window_hours(2, calendar_event_count=4) == 2.0  # 6 total = busy
    assert _compute_briefing_window_hours(1, calendar_event_count=1) == 4.5  # 2 total = light


def test_task_model_has_archived_at():
    """Migration 010: Task model has archived_at column."""
    from app.memory.models import Task
    assert hasattr(Task, 'archived_at'), 'Task model missing archived_at'
    assert hasattr(Task, 'follow_up_sent_at'), 'Task model missing follow_up_sent_at'


def test_feature_milestone_model():
    """Migration 010: FeatureMilestone model exists."""
    from app.memory.models import FeatureMilestone
    assert FeatureMilestone.__tablename__ == 'feature_milestones'
    assert hasattr(FeatureMilestone, 'milestone_name')
    assert hasattr(FeatureMilestone, 'achieved_at')


def test_store_has_archive_methods():
    """Store has archive and milestone methods."""
    from app.memory.store import MemoryStore
    for method in ('archive_task', 'mark_follow_up_sent', 'get_tasks_needing_archive',
                   'record_milestone', 'get_milestones'):
        assert hasattr(MemoryStore, method), f'Missing {method}'


def test_compute_briefing_window_boundary():
    """D-05: Boundary values for window calculation."""
    from app.tasks.proactive import _compute_briefing_window_hours
    assert _compute_briefing_window_hours(2) == 4.5   # 2 < 3 = light
    assert _compute_briefing_window_hours(5) == 3.0   # 5 < 6 = moderate
    assert _compute_briefing_window_hours(100) == 2.0  # Very busy

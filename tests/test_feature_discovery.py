"""Tests for Phase 4.3 feature discovery nudges (D-07, D-08, D-09)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta


def test_feature_nudges_have_required_fields():
    """D-09: Every nudge has text_command and dashboard_link."""
    from app.tasks.proactive import FEATURE_NUDGES
    for name, info in FEATURE_NUDGES.items():
        assert "text_command" in info, f"{name} missing text_command"
        assert "dashboard_link" in info, f"{name} missing dashboard_link"
        assert "description" in info, f"{name} missing description"
        assert info["dashboard_link"].startswith("/"), f"{name} dashboard_link should be a path"


def test_nudge_intervals_decaying():
    """D-08: Nudge intervals increase (1 week, 2 weeks, 1 month)."""
    from app.tasks.proactive import _NUDGE_INTERVALS_DAYS
    assert _NUDGE_INTERVALS_DAYS == [7, 14, 30]
    # Verify they are strictly increasing
    for i in range(1, len(_NUDGE_INTERVALS_DAYS)):
        assert _NUDGE_INTERVALS_DAYS[i] > _NUDGE_INTERVALS_DAYS[i - 1]


def test_feature_discovery_in_pool():
    """feature_discovery category exists in DEFAULT_CATEGORIES with cooldown_hours=48."""
    from app.core.proactive_pool import DEFAULT_CATEGORIES
    discovery = next((c for c in DEFAULT_CATEGORIES if c.name == "feature_discovery"), None)
    assert discovery is not None, "feature_discovery not in DEFAULT_CATEGORIES"
    assert discovery.cooldown_hours == 48, "Should have 48h cooldown"
    assert discovery.handler_type == "feature_discovery"


@pytest.mark.asyncio
async def test_handle_feature_discovery_no_user():
    """Handler returns empty result for missing user."""
    from app.tasks.proactive import handle_feature_discovery

    mock_db = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.database.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("app.tasks.proactive._get_user_by_id", return_value=None):
        result = await handle_feature_discovery({"user_id": "missing", "job_id": "j1"})
        assert result.get("response") in (None, "", "suppressed")


@pytest.mark.asyncio
async def test_handle_feature_discovery_all_achieved():
    """Handler returns empty when user has achieved all milestones."""
    from app.tasks.proactive import handle_feature_discovery, FEATURE_NUDGES

    # Mock a user who has achieved all milestones
    mock_user = MagicMock()
    mock_user.phone = "+1234567890"
    mock_user.proactive_settings_json = None
    mock_user.assistant_name = None
    mock_user.personality_notes = None

    all_milestones = []
    for name in FEATURE_NUDGES:
        m = MagicMock()
        m.milestone_name = name
        all_milestones.append(m)

    mock_db = AsyncMock()
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_store = AsyncMock()
    mock_store.get_milestones.return_value = all_milestones
    mock_store.get_action_log.return_value = []

    with patch("app.database.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("app.tasks.proactive._get_user_by_id", return_value=mock_user), \
         patch("app.memory.store.MemoryStore", return_value=mock_store):
        result = await handle_feature_discovery({"user_id": "u1", "job_id": "j1"})
        # Should suppress since all milestones achieved
        assert result.get("response") in (None, "", "suppressed")


def test_milestone_count():
    """At least 8 milestones defined for comprehensive discovery."""
    from app.tasks.proactive import FEATURE_NUDGES
    assert len(FEATURE_NUDGES) >= 8


def test_each_nudge_has_category():
    """Every nudge has a category field for grouping."""
    from app.tasks.proactive import FEATURE_NUDGES
    for name, info in FEATURE_NUDGES.items():
        assert "category" in info, f"{name} missing category"
        assert isinstance(info["category"], str), f"{name} category should be string"

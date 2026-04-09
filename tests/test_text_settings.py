"""Tests for Phase 4.3 text-based settings (D-10, D-11, D-12, D-16)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
import json


def test_update_setting_in_tool_schemas():
    """D-10: update_setting tool appears in LLM tool list."""
    from app.core.tools import get_tool_schemas
    schemas = get_tool_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert "update_setting" in names


def test_update_setting_schema_scopes():
    """D-11: Tool covers all simple-value scopes."""
    from app.core.tools import UPDATE_SETTING_SCHEMA
    scopes = UPDATE_SETTING_SCHEMA["function"]["parameters"]["properties"]["scope"]["enum"]
    assert set(scopes) == {"proactive", "task", "goal", "profile", "assistant"}


def test_update_setting_risk_medium():
    """D-10: update_setting requires confirmation (medium risk)."""
    from app.core.tools import TOOL_RISK
    assert TOOL_RISK.get("update_setting") == "medium"


def test_dashboard_links_for_complex():
    """D-12: Dashboard links exist for complex settings."""
    from app.tasks.settings_handler import DASHBOARD_LINKS
    assert "connections" in DASHBOARD_LINKS
    assert "persona" in DASHBOARD_LINKS
    assert all(v.startswith("/") for v in DASHBOARD_LINKS.values())


@pytest.mark.asyncio
async def test_handle_proactive_quiet_hours():
    """Text command can set quiet hours."""
    from app.tasks.settings_handler import execute_setting_update

    mock_user = MagicMock()
    mock_user.proactive_settings_json = json.dumps({"quiet_hours": {"start": 22, "end": 7}})

    with patch("app.database.AsyncSessionLocal") as mock_cls:
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_user
        mock_db.execute.return_value = mock_result
        mock_db.commit = AsyncMock()

        result = await execute_setting_update(
            {"scope": "proactive", "action": "update", "target": "quiet_hours_start", "value": 21},
            {"user_id": "u1"},
        )
        assert "result" in result
        assert "21" in result["result"]


@pytest.mark.asyncio
async def test_handle_proactive_max_daily():
    """Text command can set max daily proactive messages."""
    from app.tasks.settings_handler import execute_setting_update

    mock_user = MagicMock()
    mock_user.proactive_settings_json = None

    with patch("app.database.AsyncSessionLocal") as mock_cls:
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = mock_user
        mock_db.execute.return_value = mock_result
        mock_db.commit = AsyncMock()

        result = await execute_setting_update(
            {"scope": "proactive", "action": "update", "target": "max_daily_messages", "value": 5},
            {"user_id": "u1"},
        )
        assert "result" in result
        assert "5" in result["result"]


@pytest.mark.asyncio
async def test_handle_task_complete():
    """D-16: Text command can complete a task by title."""
    from app.tasks.settings_handler import execute_setting_update

    mock_task = MagicMock()
    mock_task.title = "Buy groceries"
    mock_task.id = "t1"
    mock_task.completed = False

    mock_store = AsyncMock()
    mock_store.get_active_tasks.return_value = [mock_task]
    mock_store.db = AsyncMock()

    with patch("app.database.AsyncSessionLocal") as mock_cls:
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("app.memory.store.MemoryStore", return_value=mock_store):
            result = await execute_setting_update(
                {"scope": "task", "action": "complete", "target": "groceries"},
                {"user_id": "u1"},
            )
            assert "result" in result
            assert "complete" in result["result"].lower() or "Buy groceries" in result["result"]


@pytest.mark.asyncio
async def test_handle_task_no_match():
    """Task action with no matching task returns error."""
    from app.tasks.settings_handler import execute_setting_update

    mock_store = AsyncMock()
    mock_store.get_active_tasks.return_value = []

    with patch("app.database.AsyncSessionLocal") as mock_cls:
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("app.memory.store.MemoryStore", return_value=mock_store):
            result = await execute_setting_update(
                {"scope": "task", "action": "complete", "target": "nonexistent"},
                {"user_id": "u1"},
            )
            assert "error" in result


@pytest.mark.asyncio
async def test_handle_unknown_scope():
    """Unknown scope returns error."""
    from app.tasks.settings_handler import execute_setting_update

    with patch("app.database.AsyncSessionLocal") as mock_cls:
        mock_db = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_store = AsyncMock()
        with patch("app.memory.store.MemoryStore", return_value=mock_store):
            result = await execute_setting_update(
                {"scope": "invalid", "action": "update", "target": "foo"},
                {"user_id": "u1"},
            )
            assert "error" in result


@pytest.mark.asyncio
async def test_handle_no_user_id():
    """Missing user_id returns error without hitting DB."""
    from app.tasks.settings_handler import execute_setting_update

    result = await execute_setting_update(
        {"scope": "proactive", "action": "update", "target": "quiet_hours_start", "value": 21},
        {"user_id": ""},
    )
    assert "error" in result
    assert "user context" in result["error"].lower()


def test_manager_dispatches_update_setting():
    """Manager _execute_tool handles update_setting."""
    import inspect
    from app.tasks.manager import _execute_tool
    src = inspect.getsource(_execute_tool)
    assert "update_setting" in src, "_execute_tool must handle update_setting"

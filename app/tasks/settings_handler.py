"""
Text-based settings handler (D-10, D-11, D-12, D-16).

Executes setting changes requested via the update_setting tool.
Called from manager._execute_tool after user confirms via PendingAction.

Scope coverage:
  - proactive: quiet_hours_start, quiet_hours_end, max_daily_messages, category enable/disable
  - task: complete, archive, delete by title match
  - goal: update status (completed, archived)
  - profile: name, timezone
  - assistant: assistant_name, personality_notes

Complex settings (OAuth, connections, personas) are NOT handled here.
The manager LLM is instructed to return a dashboard link for those.
"""
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Dashboard links for complex settings (D-12)
DASHBOARD_LINKS = {
    "connections": "/connections",
    "oauth": "/connections",
    "persona": "/personas",
    "persona_create": "/personas",
    "connection_manage": "/connections",
}


async def execute_setting_update(args: dict, payload: dict) -> dict:
    """
    Execute a setting update. Called by _execute_tool in manager.py.

    Args:
        args: {"scope": str, "action": str, "target": str, "value": any}
        payload: Job payload with user_id, phone, etc.

    Returns:
        {"result": str} on success or {"error": str} on failure.
    """
    scope = args.get("scope", "")
    action = args.get("action", "")
    target = args.get("target", "")
    value = args.get("value")
    user_id = payload.get("user_id", "")

    if not user_id:
        return {"error": "No user context available."}

    from app.database import AsyncSessionLocal
    from app.memory.store import MemoryStore

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)

        if scope == "proactive":
            return await _handle_proactive(store, db, user_id, action, target, value)
        elif scope == "task":
            return await _handle_task(store, user_id, action, target)
        elif scope == "goal":
            return await _handle_goal(store, user_id, action, target, value)
        elif scope == "profile":
            return await _handle_profile(store, db, user_id, action, target, value)
        elif scope == "assistant":
            return await _handle_assistant(store, db, user_id, action, target, value)
        else:
            return {"error": f"Unknown scope: {scope}. Try proactive, task, goal, profile, or assistant."}


async def _handle_proactive(store, db, user_id: str, action: str, target: str, value) -> dict:
    """Handle proactive settings: quiet hours, daily cap, category toggles."""
    from app.memory.models import User
    from sqlalchemy import select

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        return {"error": "User not found."}

    settings = {}
    if user.proactive_settings_json:
        try:
            settings = json.loads(user.proactive_settings_json)
        except (ValueError, TypeError):
            settings = {}

    # Global enable/disable — "turn off/on all proactive messages"
    if target == "enabled" or target in ("all", "global", "proactive", "proactive_messages"):
        if action == "disable":
            settings["enabled"] = False
            user.proactive_settings_json = json.dumps(settings)
            await db.commit()
            return {"result": "All proactive messages have been disabled."}
        elif action == "enable":
            settings["enabled"] = True
            user.proactive_settings_json = json.dumps(settings)
            await db.commit()
            return {"result": "Proactive messages have been re-enabled."}

    if target == "quiet_hours_start":
        quiet = settings.get("quiet_hours", {"start": 22, "end": 7})
        quiet["start"] = int(value) if value is not None else 22
        settings["quiet_hours"] = quiet
        user.proactive_settings_json = json.dumps(settings)
        await db.commit()
        return {"result": f"Quiet hours start set to {quiet['start']}:00."}

    elif target == "quiet_hours_end":
        quiet = settings.get("quiet_hours", {"start": 22, "end": 7})
        quiet["end"] = int(value) if value is not None else 7
        settings["quiet_hours"] = quiet
        user.proactive_settings_json = json.dumps(settings)
        await db.commit()
        return {"result": f"Quiet hours end set to {quiet['end']}:00."}

    elif target == "max_daily_messages":
        from app.core.throttle import DEFAULT_MAX_PER_DAY
        settings["max_daily_messages"] = int(value) if value is not None else DEFAULT_MAX_PER_DAY
        user.proactive_settings_json = json.dumps(settings)
        await db.commit()
        return {"result": f"Max daily proactive messages set to {settings['max_daily_messages']}."}

    elif action in ("enable", "disable"):
        # Toggle a proactive category via ProactivePreference table
        # (same store the dashboard reads/writes)
        from app.memory.models import ProactivePreference
        from sqlalchemy import select as sa_select
        from datetime import datetime, timezone as tz

        enabled_val = (action == "enable")
        result = await db.execute(
            sa_select(ProactivePreference).where(
                ProactivePreference.user_id == user_id,
                ProactivePreference.category_name == target,
            )
        )
        existing = result.scalars().first()
        if existing:
            existing.enabled = enabled_val
            existing.updated_at = datetime.now(tz.utc)
        else:
            import uuid
            pref = ProactivePreference(
                id=str(uuid.uuid4()),
                user_id=user_id,
                category_name=target,
                enabled=enabled_val,
            )
            db.add(pref)
        await db.commit()
        state = "enabled" if enabled_val else "disabled"
        return {"result": f"Proactive category '{target}' {state}."}

    elif target == "preferred_channel":
        if value in ("sms", "slack"):
            settings["preferred_channel"] = value
            user.proactive_settings_json = json.dumps(settings)
            await db.commit()
            return {"result": f"Preferred proactive channel set to {value}."}
        return {"error": "Channel must be 'sms' or 'slack'."}

    return {"error": f"Unknown proactive setting: {target}. Try quiet_hours_start, quiet_hours_end, max_daily_messages, or a category name."}


async def _handle_task(store, user_id: str, action: str, target: str) -> dict:
    """Handle task actions: complete, archive, delete by title match (D-16)."""
    tasks = await store.get_active_tasks(user_id)

    # Find task by title match (case-insensitive partial match)
    target_lower = target.lower()
    matches = [t for t in tasks if target_lower in t.title.lower()]

    if not matches:
        return {"error": f"No active task matching '{target}'. Check your task list."}

    if len(matches) > 1:
        titles = ", ".join(f"'{t.title}'" for t in matches[:5])
        return {"error": f"Multiple tasks match '{target}': {titles}. Be more specific."}

    task = matches[0]

    if action == "complete":
        task.completed = True
        task.updated_at = datetime.now(timezone.utc)
        await store.db.commit()
        return {"result": f"Task '{task.title}' marked as complete."}

    elif action == "archive":
        await store.archive_task(user_id, task.id)
        return {"result": f"Task '{task.title}' archived."}

    elif action == "delete":
        await store.db.delete(task)
        await store.db.commit()
        return {"result": f"Task '{task.title}' deleted."}

    return {"error": f"Unknown task action: {action}. Try complete, archive, or delete."}


async def _handle_goal(store, user_id: str, action: str, target: str, value) -> dict:
    """Handle goal actions: update status."""
    goals = await store.get_goals(user_id, status="all")

    target_lower = target.lower()
    matches = [g for g in goals if target_lower in g.title.lower()]

    if not matches:
        return {"error": f"No goal matching '{target}'."}

    if len(matches) > 1:
        titles = ", ".join(f"'{g.title}'" for g in matches[:5])
        return {"error": f"Multiple goals match '{target}': {titles}. Be more specific."}

    goal = matches[0]

    if action == "complete":
        goal.status = "completed"
        goal.updated_at = datetime.now(timezone.utc)
        await store.db.commit()
        return {"result": f"Goal '{goal.title}' marked as completed."}

    elif action == "archive":
        goal.status = "archived"
        goal.updated_at = datetime.now(timezone.utc)
        await store.db.commit()
        return {"result": f"Goal '{goal.title}' archived."}

    elif action == "update" and value:
        goal.status = str(value)
        goal.updated_at = datetime.now(timezone.utc)
        await store.db.commit()
        return {"result": f"Goal '{goal.title}' status updated to '{value}'."}

    return {"error": f"Unknown goal action: {action}. Try complete, archive, or update."}


async def _handle_profile(store, db, user_id: str, action: str, target: str, value) -> dict:
    """Handle profile field updates: name, timezone."""
    from app.memory.models import User
    from sqlalchemy import select

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        return {"error": "User not found."}

    if target == "name":
        user.name = str(value) if value else user.name
        await db.commit()
        return {"result": f"Name updated to '{user.name}'."}

    elif target == "timezone":
        # Validate timezone
        import zoneinfo
        try:
            zoneinfo.ZoneInfo(str(value))
            user.timezone = str(value)
            await db.commit()
            return {"result": f"Timezone updated to '{user.timezone}'."}
        except (KeyError, ValueError):
            return {"error": f"Invalid timezone: '{value}'. Use format like 'America/New_York'."}

    return {"error": f"Unknown profile field: {target}. Try name or timezone. For connections or personas, visit your dashboard."}


async def _handle_assistant(store, db, user_id: str, action: str, target: str, value) -> dict:
    """Handle assistant profile: name, personality_notes."""
    from app.memory.models import User
    from sqlalchemy import select

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        return {"error": "User not found."}

    if target in ("assistant_name", "name"):
        user.assistant_name = str(value) if value else user.assistant_name
        await db.commit()
        return {"result": f"Assistant name updated to '{user.assistant_name}'."}

    elif target in ("personality_notes", "personality", "tone"):
        user.personality_notes = str(value) if value else user.personality_notes
        await db.commit()
        return {"result": f"Assistant personality updated."}

    return {"error": f"Unknown assistant setting: {target}. Try assistant_name or personality_notes."}

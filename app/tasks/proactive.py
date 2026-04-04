"""
Proactive job handlers -- process scheduled jobs from the scheduler service.

Handlers:
  - handle_morning_briefing: summarize upcoming tasks, goals, calendar for the day
  - handle_evening_recap: summarize what was accomplished today
  - handle_goal_checkin: surface suggestions related to active goals
  - handle_weekly_digest: weekly SMS summary of actions taken (D-10)

All handlers:
  - Record actions via store.log_action (AGENT-06)
  - Record proactive sends via throttle.record_proactive_send (AGENT-05)
  - Return standard result dict {job_id, phone, response, channel, address}

Proactive jobs arrive with source="scheduler" in payload.
They are dispatched by the worker, NOT the inbound pipeline.
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


async def handle_morning_briefing(payload: dict) -> dict:
    """
    Morning briefing -- summarize the user's day ahead.
    Checks active tasks, goals, and (if available) calendar events.
    """
    user_id = payload.get("user_id", "")
    job_id = payload.get("job_id", "")

    from app.database import AsyncSessionLocal
    from app.memory.store import MemoryStore

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)
        user = await _get_user_by_id(store, user_id)
        if not user:
            return _empty_result(job_id, user_id)

        # Gather context for briefing
        tasks = await store.get_active_tasks(user_id)
        goals = await store.get_goals(user_id, status="active")

        # Build briefing via LLM
        from app.tasks._llm import llm_text
        from app.core.identity import identity_preamble

        task_summary = "\n".join(
            f"- {t.title} (due: {t.due_at.strftime('%I:%M %p') if t.due_at else 'no deadline'})"
            for t in tasks[:10]
        ) or "No pending tasks."

        goal_summary = "\n".join(
            f"- {g.title} ({g.framework}, {g.status})"
            for g in goals[:5]
        ) or "No active goals."

        system = identity_preamble(
            assistant_name=getattr(user, "assistant_name", None),
            personality_notes=getattr(user, "personality_notes", None),
        )

        briefing = await llm_text(
            system=system + "\nYou are sending a morning briefing. Be concise, warm, and actionable.",
            messages=[{
                "role": "user",
                "content": (
                    f"Generate a brief morning briefing for today. "
                    f"Here's what's on the plate:\n\n"
                    f"Tasks:\n{task_summary}\n\n"
                    f"Goals:\n{goal_summary}\n\n"
                    f"Keep it under 200 words. Use a friendly tone."
                ),
            }],
            mock_text=f"Good morning! Here's your day: {task_summary[:100]}",
            timeout_s=10.0,
        )

        # Log the action (AGENT-06)
        await store.log_action(
            user_id=user_id,
            action_type="morning_briefing",
            description=f"Sent morning briefing with {len(tasks)} tasks and {len(goals)} goals",
            outcome="success",
            trigger="scheduled",
        )

    # Record proactive send (AGENT-05)
    await _record_send(user_id)

    return {
        "job_id": job_id,
        "phone": getattr(user, "phone", ""),
        "address": getattr(user, "phone", ""),
        "channel": payload.get("channel", "sms"),
        "response": briefing,
    }


async def handle_evening_recap(payload: dict) -> dict:
    """Evening recap -- summarize what was accomplished today."""
    user_id = payload.get("user_id", "")
    job_id = payload.get("job_id", "")

    from app.database import AsyncSessionLocal
    from app.memory.store import MemoryStore

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)
        user = await _get_user_by_id(store, user_id)
        if not user:
            return _empty_result(job_id, user_id)

        # Get today's actions
        actions = await store.get_action_log(user_id, limit=20)
        today = datetime.now(timezone.utc).date()
        today_actions = [a for a in actions if a.created_at.date() == today]

        from app.tasks._llm import llm_text
        from app.core.identity import identity_preamble

        action_summary = "\n".join(
            f"- {a.action_type}: {a.description} ({a.outcome})"
            for a in today_actions[:10]
        ) or "No recorded actions today."

        system = identity_preamble(
            assistant_name=getattr(user, "assistant_name", None),
            personality_notes=getattr(user, "personality_notes", None),
        )

        recap = await llm_text(
            system=system + "\nYou are sending an evening recap. Be concise and supportive.",
            messages=[{
                "role": "user",
                "content": (
                    f"Generate a brief evening recap. "
                    f"Here's what happened today:\n\n"
                    f"Actions:\n{action_summary}\n\n"
                    f"Keep it under 150 words."
                ),
            }],
            mock_text=f"Here's your evening recap: {action_summary[:100]}",
            timeout_s=10.0,
        )

        await store.log_action(
            user_id=user_id,
            action_type="evening_recap",
            description=f"Sent evening recap covering {len(today_actions)} actions",
            outcome="success",
            trigger="scheduled",
        )

    await _record_send(user_id)

    return {
        "job_id": job_id,
        "phone": getattr(user, "phone", ""),
        "address": getattr(user, "phone", ""),
        "channel": payload.get("channel", "sms"),
        "response": recap,
    }


async def handle_goal_checkin(payload: dict) -> dict:
    """Goal check-in -- surface progress and suggestions for active goals (AGENT-04)."""
    user_id = payload.get("user_id", "")
    job_id = payload.get("job_id", "")

    from app.database import AsyncSessionLocal
    from app.memory.store import MemoryStore

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)
        user = await _get_user_by_id(store, user_id)
        if not user:
            return _empty_result(job_id, user_id)

        goals = await store.get_goals(user_id, status="active")
        if not goals:
            return _empty_result(job_id, user_id)

        from app.tasks._llm import llm_text
        from app.core.identity import identity_preamble

        goal_details = "\n".join(
            f"- {g.title}: {g.description or 'No description'} "
            f"(framework: {g.framework}, target: {g.target_date.strftime('%Y-%m-%d') if g.target_date else 'none'})"
            for g in goals[:5]
        )

        system = identity_preamble(
            assistant_name=getattr(user, "assistant_name", None),
            personality_notes=getattr(user, "personality_notes", None),
        )

        checkin = await llm_text(
            system=system + "\nYou are doing a goal progress check-in. Be encouraging and actionable.",
            messages=[{
                "role": "user",
                "content": (
                    f"Generate a brief goal check-in message. "
                    f"Active goals:\n{goal_details}\n\n"
                    f"Ask about progress on one specific goal and suggest a next step. "
                    f"Keep it under 100 words."
                ),
            }],
            mock_text=f"How's progress on your goals? Here's a quick check-in: {goal_details[:80]}",
            timeout_s=10.0,
        )

        await store.log_action(
            user_id=user_id,
            action_type="goal_checkin",
            description=f"Sent goal check-in for {len(goals)} active goals",
            outcome="success",
            trigger="scheduled",
        )

    await _record_send(user_id)

    return {
        "job_id": job_id,
        "phone": getattr(user, "phone", ""),
        "address": getattr(user, "phone", ""),
        "channel": payload.get("channel", "sms"),
        "response": checkin,
    }


async def handle_weekly_digest(payload: dict) -> dict:
    """
    Weekly SMS digest -- summarize the week's assistant actions (D-10).

    Scheduled to run once per week (e.g., Sunday 10:00 AM user local time).
    Pulls the past 7 days of action log entries and generates a summary via LLM.
    """
    user_id = payload.get("user_id", "")
    job_id = payload.get("job_id", "")

    from app.database import AsyncSessionLocal
    from app.memory.store import MemoryStore

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)
        user = await _get_user_by_id(store, user_id)
        if not user:
            return _empty_result(job_id, user_id)

        # Get past week's actions (up to 100)
        all_actions = await store.get_action_log(user_id, limit=100)
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        week_actions = [a for a in all_actions if a.created_at >= week_ago]

        if not week_actions:
            digest_text = "Quiet week! No actions were taken by your assistant. Need help with anything?"
        else:
            from app.tasks._llm import llm_text
            from app.core.identity import identity_preamble

            # Group by action type for summary
            action_groups: dict[str, int] = {}
            for a in week_actions:
                action_groups[a.action_type] = action_groups.get(a.action_type, 0) + 1

            action_summary = "\n".join(
                f"- {action_type}: {count} time(s)"
                for action_type, count in sorted(action_groups.items(), key=lambda x: -x[1])
            )

            recent_highlights = "\n".join(
                f"- {a.description} ({a.outcome})"
                for a in week_actions[:10]
            )

            system = identity_preamble(
                assistant_name=getattr(user, "assistant_name", None),
                personality_notes=getattr(user, "personality_notes", None),
            )

            digest_text = await llm_text(
                system=system + "\nYou are sending a weekly activity digest via SMS. Be brief and informative.",
                messages=[{
                    "role": "user",
                    "content": (
                        f"Generate a weekly digest SMS. This week:\n\n"
                        f"Action summary:\n{action_summary}\n\n"
                        f"Recent highlights:\n{recent_highlights}\n\n"
                        f"Total actions: {len(week_actions)}\n"
                        f"Keep it under 200 words. Start with 'Weekly recap:'"
                    ),
                }],
                mock_text=f"Weekly recap: {len(week_actions)} actions this week. {action_summary[:100]}",
                timeout_s=10.0,
            )

        await store.log_action(
            user_id=user_id,
            action_type="weekly_digest",
            description=f"Sent weekly digest covering {len(week_actions)} actions",
            outcome="success",
            trigger="scheduled",
        )

    await _record_send(user_id)

    return {
        "job_id": job_id,
        "phone": getattr(user, "phone", ""),
        "address": getattr(user, "phone", ""),
        "channel": payload.get("channel", "sms"),
        "response": digest_text,
    }


async def handle_task_reminder(payload: dict) -> dict:
    """
    Deliver a task reminder via SMS when due_at arrives.

    Scheduled by handle_reminder or the dashboard create_task endpoint.
    Fetches the task from DB to get current title (may have been edited).
    Marks nothing as complete — the user decides when to mark done.
    """
    user_id = payload.get("user_id", "")
    job_id = payload.get("job_id", "")
    task_id = payload.get("task_id", "")
    phone = payload.get("phone", "")
    title = payload.get("title", "your task")

    from app.database import AsyncSessionLocal
    from app.memory.store import MemoryStore

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)

        # Fetch current task state (title may have been edited)
        from sqlalchemy import select
        from app.memory.models import Task as TaskModel
        result = await store.db.execute(
            select(TaskModel).where(TaskModel.id == task_id, TaskModel.user_id == user_id)
        )
        task = result.scalar_one_or_none()

        if task and task.completed:
            # Task was already completed before the reminder fired — skip
            logger.info("REMINDER_SKIP_COMPLETED  task_id=%s  user=%s", task_id, user_id[:8])
            return _empty_result(job_id, user_id)

        # Use current title if task still exists
        reminder_title = task.title if task else title

        # Parse metadata for action_type (Phase 4.1: smart reminder execution)
        import json as _json
        metadata = {}
        if task and task.metadata_json:
            try:
                metadata = _json.loads(task.metadata_json)
            except (ValueError, TypeError):
                pass

        action_type = metadata.get("action_type", "notify")
        if action_type not in ("notify", "execute"):
            action_type = "notify"

        if action_type == "execute":
            logger.info(
                "EXECUTE_REMINDER  task_id=%s  user=%s  title=%r",
                task_id, user_id[:8], reminder_title[:60],
            )
            return await _execute_reminder_via_manager(
                user_id, task_id, reminder_title, phone, payload
            )

        response = f"Reminder: {reminder_title}"

        # Log the action
        await store.log_action(
            user_id=user_id,
            action_type="task_reminder",
            description=f"Delivered reminder: {reminder_title}",
            outcome="success",
            trigger="scheduled",
        )

    await _record_send(user_id)

    return {
        "job_id": job_id,
        "phone": phone,
        "address": phone,
        "channel": payload.get("channel", "sms"),
        "response": response,
    }


# ─── Execute reminder re-queue ───────────────────────────────────────────────

async def _execute_reminder_via_manager(
    user_id: str, task_id: str, title: str, phone: str, original_payload: dict
) -> dict:
    """Re-queue a scheduled 'execute' reminder as a NEEDS_MANAGER job.

    Loads user context (memories, profile) from DB so the manager LLM
    can generate personalized responses (e.g. weather for user's location).

    Uses a fresh Redis connection (not queue_client singleton) because
    the worker process may not have queue_client connected (Research Pitfall 4).
    """
    import uuid
    import json as _json
    import redis.asyncio as aioredis
    from app.config import get_settings
    from app.database import AsyncSessionLocal
    from app.memory.store import MemoryStore

    settings = get_settings()
    new_job_id = str(uuid.uuid4())

    # Load user context so manager has memories, personality, location, etc.
    context = {}
    try:
        async with AsyncSessionLocal() as db:
            store = MemoryStore(db)
            context = await store.get_context_standard(user_id)
    except Exception as exc:
        logger.warning("Failed to load context for execute reminder: %s", exc)

    manager_payload = {
        "job_id": new_job_id,
        "intent": "needs_manager",
        "phone": phone,
        "address": phone,
        "channel": original_payload.get("channel", "sms"),
        "body": title,
        "user_id": user_id,
        "persona": "shared",
        "context": context,
        "source": "scheduled_execute",
    }

    try:
        r = await aioredis.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True
        )
        await r.xadd(settings.queue_name, {"data": _json.dumps(manager_payload)})
        await r.aclose()
        logger.info(
            "EXECUTE_REMINDER_QUEUED  task_id=%s  new_job_id=%s  title=%r",
            task_id, new_job_id, title[:60],
        )
    except Exception as exc:
        logger.error("Failed to re-queue execute reminder: %s", exc)
        # Fallback: send static notification so user isn't left hanging
        return {
            "job_id": original_payload.get("job_id", ""),
            "phone": phone,
            "address": phone,
            "channel": original_payload.get("channel", "sms"),
            "response": f"Reminder: {title}",
        }

    # Log the action
    try:
        async with AsyncSessionLocal() as db:
            store = MemoryStore(db)
            await store.log_action(
                user_id=user_id,
                action_type="execute_reminder",
                description=f"Re-queued execute reminder: {title}",
                outcome="success",
                trigger="scheduled",
            )
    except Exception as exc:
        logger.warning("Failed to log execute reminder action: %s", exc)

    await _record_send(user_id)

    # Return empty response — the manager job will produce the real one
    return {
        "job_id": original_payload.get("job_id", ""),
        "phone": phone,
        "address": phone,
        "channel": original_payload.get("channel", "sms"),
        "response": "",
    }


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _get_user_by_id(store, user_id: str):
    """Look up user by ID. Returns None if not found."""
    from sqlalchemy import select
    from app.memory.models import User
    result = await store.db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def _record_send(user_id: str) -> None:
    """Record proactive send in rate limiter."""
    try:
        import redis.asyncio as aioredis
        from app.config import get_settings
        from app.core.throttle import record_proactive_send
        settings = get_settings()
        r = await aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        await record_proactive_send(r, user_id)
        await r.aclose()
    except Exception as exc:
        logger.warning("Failed to record proactive send: %s", exc)


def _empty_result(job_id: str, user_id: str) -> dict:
    """Return an empty result (user not found or no goals)."""
    return {
        "job_id": job_id,
        "phone": "",
        "address": "",
        "channel": "sms",
        "response": "",
    }

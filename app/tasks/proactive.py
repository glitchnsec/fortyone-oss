"""
Proactive job handlers -- process scheduled jobs from the scheduler service.

Handlers:
  - handle_morning_briefing: summarize upcoming tasks, goals, calendar for the day
  - handle_evening_recap: summarize what was accomplished today
  - handle_goal_checkin: surface suggestions related to active goals (deprecated, see handle_goal_coaching)
  - handle_weekly_digest: weekly SMS summary of actions taken (D-10)
  - handle_profile_nudge: detect incomplete profile fields, send friendly nudges (D-02)
  - handle_smart_checkin: re-queue through manager for tool-assisted check-in (D-03)
  - handle_insight_observation: surface patterns from accumulated memories (D-02)
  - handle_goal_coaching: full coaching loop via manager tool-calling (D-09, D-10)

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


async def _has_content_delta(store, user_id: str, category: str) -> bool:
    """
    Check if anything changed since the last send of this category (D-03, D-13).

    Uses action_log to find the most recent send of this category type.
    Returns True if there is new content (should send), False if stale (suppress).
    Default to True if no prior send exists (first time always sends).
    """
    logs = await store.get_action_log(user_id, limit=100)
    last_send = next(
        (a for a in logs if a.action_type == category and a.outcome == "success"),
        None,
    )
    if not last_send:
        return True  # Never sent -- always has news

    since = last_send.created_at

    if category == "morning_briefing":
        tasks = await store.get_active_tasks(user_id)
        if any(t.updated_at > since or t.created_at > since for t in tasks):
            return True
        goals = await store.get_goals(user_id, status="active")
        return any(g.updated_at > since for g in goals)

    elif category == "evening_recap":
        logs_today = await store.get_action_log(user_id, limit=20)
        today = datetime.now(timezone.utc).date()
        today_actions = [a for a in logs_today if a.created_at.date() == today and a.action_type != "evening_recap"]
        return len(today_actions) > 0

    elif category == "goal_coaching" or category == "goal_checkin":
        goals = await store.get_goals(user_id, status="active")
        return any(g.updated_at > since for g in goals) or any(
            g.target_date and (g.target_date - datetime.now(timezone.utc)) <= timedelta(days=7)
            for g in goals
        )

    elif category == "profile_nudge":
        from app.core.proactive_pool import compute_user_state
        state = await compute_user_state(store, user_id)
        return state.get("profile_completeness", 1.0) < 0.8

    elif category == "insight_observation":
        memories = await store.get_memories(user_id)
        # Check if any new memories since last send
        return any(m.created_at > since for m in memories)

    elif category == "feature_discovery":
        # Feature discovery always has delta -- handler self-filters by milestones
        return True

    elif category in ("smart_checkin", "day_checkin", "afternoon_followup"):
        # Check-ins always have delta (they are conversational, not data-driven)
        return True

    elif category == "weekly_digest":
        logs_week = await store.get_action_log(user_id, limit=100)
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        week_actions = [a for a in logs_week if a.created_at >= week_ago and a.action_type != "weekly_digest"]
        return len(week_actions) > 0

    return True  # Unknown category -- default to sending


def _compute_briefing_window_hours(task_count: int, calendar_event_count: int = 0) -> float:
    """
    Map task/calendar density to briefing window size in hours (D-05).

    Busy = narrower window (focus on immediate). Light = wider window (more context).
    """
    total_items = task_count + calendar_event_count
    if total_items >= 6:
        return 2.0   # Busy -- focus on next 2 hours
    elif total_items >= 3:
        return 3.0   # Moderate -- next 3 hours
    else:
        return 4.5   # Light -- next 4-5 hours


async def handle_morning_briefing(payload: dict) -> dict:
    """
    Morning briefing -- summarize the user's day ahead.
    Checks active tasks, goals, and (if available) calendar events.
    Uses dynamic time window based on task density (D-05).
    Handles remind task follow-ups and auto-archive (D-15).
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

        if not await _has_content_delta(store, user_id, "morning_briefing"):
            logger.info("DELTA_SUPPRESS user=%s category=morning_briefing", user_id[:8])
            return _empty_result(job_id, user_id)

        # Gather context for briefing
        tasks = await store.get_active_tasks(user_id)
        goals = await store.get_goals(user_id, status="active")

        # D-05: Dynamic time-windowed briefing
        now = datetime.now(timezone.utc)
        window_hours = _compute_briefing_window_hours(len(tasks))
        window_cutoff = now + timedelta(hours=window_hours)

        # Filter tasks to within the window (tasks with due_at in the next N hours)
        windowed_tasks = [
            t for t in tasks
            if t.due_at and t.due_at <= window_cutoff
        ]
        # Also include tasks with no due_at (they're always relevant)
        no_deadline_tasks = [t for t in tasks if not t.due_at]
        briefing_tasks = windowed_tasks + no_deadline_tasks[:3]  # Cap no-deadline at 3

        # D-15: Check for overdue remind tasks that need follow-up
        import json as _json
        overdue_remind = []
        for t in tasks:
            if (t.due_at and t.due_at < now
                    and not t.completed and not getattr(t, 'archived_at', None)):
                metadata = {}
                if t.metadata_json:
                    try:
                        metadata = _json.loads(t.metadata_json)
                    except (ValueError, TypeError):
                        pass
                action_type = metadata.get("action_type", "notify")
                if action_type == "notify":
                    if not getattr(t, 'follow_up_sent_at', None):
                        overdue_remind.append(t)
                    elif t.follow_up_sent_at:
                        # Follow-up was already sent -- auto-archive now (D-15)
                        await store.archive_task(user_id, t.id)
                        logger.info("AUTO_ARCHIVE_REMIND task=%s user=%s", t.id, user_id[:8])

        # Include follow-up prompts in briefing
        follow_up_section = ""
        if overdue_remind:
            follow_up_items = "\n".join(f"- {t.title}" for t in overdue_remind[:5])
            follow_up_section = f"\n\nOverdue reminders to follow up on:\n{follow_up_items}"
            # Mark follow-up sent
            for t in overdue_remind[:5]:
                await store.mark_follow_up_sent(user_id, t.id)

        # Build briefing via LLM
        from app.tasks._llm import llm_text
        from app.core.identity import identity_preamble

        task_summary = "\n".join(
            f"- {t.title} (due: {t.due_at.strftime('%I:%M %p') if t.due_at else 'no deadline'})"
            for t in briefing_tasks[:10]
        ) or "No pending tasks in your upcoming window."

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
                    f"Generate a brief morning briefing for the next {window_hours:.0f} hours. "
                    f"Here's what's on the plate:\n\n"
                    f"Tasks:\n{task_summary}\n\n"
                    f"Goals:\n{goal_summary}"
                    f"{follow_up_section}\n\n"
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
            description=f"Sent morning briefing ({window_hours:.0f}h window) with {len(briefing_tasks)} tasks and {len(goals)} goals",
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

        if not await _has_content_delta(store, user_id, "evening_recap"):
            logger.info("DELTA_SUPPRESS user=%s category=evening_recap", user_id[:8])
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
    """Goal check-in -- surface progress and suggestions for active goals (AGENT-04).

    DEPRECATED: Superseded by handle_goal_coaching which uses the manager's
    tool-calling system for richer, research-backed coaching (D-09, D-10).
    Kept for backward compatibility with pre-pool scheduled jobs.
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

        goals = await store.get_goals(user_id, status="active")
        if not goals:
            return _empty_result(job_id, user_id)

        if not await _has_content_delta(store, user_id, "goal_checkin"):
            logger.info("DELTA_SUPPRESS user=%s category=goal_checkin", user_id[:8])
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

        if not await _has_content_delta(store, user_id, "weekly_digest"):
            logger.info("DELTA_SUPPRESS user=%s category=weekly_digest", user_id[:8])
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

    For recurring reminders (daily/weekly/monthly), computes the next due_at
    and re-schedules the reminder so it fires again.
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

        # Parse metadata for action_type and recurrence
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

        # Re-schedule recurring reminders BEFORE dispatching (so it happens
        # even if the execute path or send fails)
        recurrence = metadata.get("recurrence", "none")
        if recurrence != "none" and task and task.due_at:
            next_due = _compute_next_occurrence(task.due_at, recurrence)
            if next_due:
                # Update the task's due_at in the DB to the next occurrence
                task.due_at = next_due
                task.updated_at = datetime.now(timezone.utc)
                await db.commit()

                # Schedule the next reminder delivery
                from app.tasks.reminder import schedule_task_reminder
                await schedule_task_reminder(user_id, task_id, reminder_title, phone, next_due)
                logger.info(
                    "RECURRING_RESCHEDULE  task_id=%s  user=%s  recurrence=%s  next=%s",
                    task_id, user_id[:8], recurrence, next_due.isoformat(),
                )

        if action_type == "execute":
            logger.info(
                "EXECUTE_REMINDER  task_id=%s  user=%s  title=%r",
                task_id, user_id[:8], reminder_title[:60],
            )
            result = await _execute_reminder_via_manager(
                user_id, task_id, reminder_title, phone, payload
            )
            # D-14: Auto-archive execute tasks immediately after execution
            try:
                async with AsyncSessionLocal() as archive_db:
                    archive_store = MemoryStore(archive_db)
                    await archive_store.archive_task(user_id, task_id)
                    logger.info("AUTO_ARCHIVE_EXECUTE task=%s user=%s", task_id, user_id[:8])
            except Exception:
                logger.warning("AUTO_ARCHIVE_FAILED task=%s user=%s", task_id, user_id[:8], exc_info=True)
            return result

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


def _compute_next_occurrence(current_due: datetime, recurrence: str) -> datetime | None:
    """
    Compute the next occurrence for a recurring reminder.

    Adds the recurrence interval to current_due. If the result is in the past
    (e.g. the scheduler was down), advances forward until the next future time.

    Returns None for unrecognized recurrence values.
    """
    intervals = {
        "daily": timedelta(days=1),
        "weekly": timedelta(weeks=1),
        "monthly": None,  # handled separately (variable days)
    }

    if recurrence not in intervals:
        return None

    now = datetime.now(timezone.utc)

    if recurrence == "monthly":
        # Add one month — handle variable month lengths
        year = current_due.year
        month = current_due.month + 1
        if month > 12:
            month = 1
            year += 1
        # Clamp day to the max for that month
        import calendar
        max_day = calendar.monthrange(year, month)[1]
        day = min(current_due.day, max_day)
        next_due = current_due.replace(year=year, month=month, day=day)
        # If still in the past, keep advancing
        while next_due <= now:
            month += 1
            if month > 12:
                month = 1
                year += 1
            max_day = calendar.monthrange(year, month)[1]
            day = min(current_due.day, max_day)
            next_due = current_due.replace(year=year, month=month, day=day)
        return next_due

    delta = intervals[recurrence]
    next_due = current_due + delta
    # If scheduler was down, advance until we're in the future
    while next_due <= now:
        next_due += delta
    return next_due


# ─── New proactive handlers (Phase 4.2, D-02, D-03) ────────────────────────


async def handle_profile_nudge(payload: dict) -> dict:
    """
    Profile completion nudge -- detect incomplete profile fields and
    send a friendly, conversational nudge to encourage the user to fill them in.

    Completeness scoring checks user fields + TELOS profile sections.
    Spacing logic prevents nagging: 1st nudge day 1, 2nd day 3, 3rd day 7, then weekly.
    Stops nudging once profile is > 80% complete.
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

        if not await _has_content_delta(store, user_id, "profile_nudge"):
            logger.info("DELTA_SUPPRESS user=%s category=profile_nudge", user_id[:8])
            return _empty_result(job_id, user_id)

        # Compute profile completeness
        entries = await store.get_profile_entries(user_id)
        score, missing = _profile_completeness(user, entries)

        # If profile is good enough, no nudge needed
        if score > 0.8:
            logger.info("PROFILE_NUDGE_SKIP  user=%s  score=%.2f  reason=complete_enough", user_id[:8], score)
            return _empty_result(job_id, user_id)

        # Check nudge spacing via Redis
        import redis.asyncio as aioredis
        import time as _time
        from app.config import get_settings
        settings = get_settings()

        nudge_count = 0  # default if Redis is unavailable
        try:
            r = await aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
            nudge_count_key = f"proactive:nudge_count:{user_id}"
            last_nudge_key = f"proactive:last_nudge:{user_id}"

            nudge_count = int(await r.get(nudge_count_key) or 0)
            last_nudge_ts = float(await r.get(last_nudge_key) or 0)

            now = _time.time()
            # Spacing: 1st=0 days, 2nd=3 days, 3rd=7 days, then weekly
            spacing_days = [0, 3, 7] + [7] * 100  # weekly after 3rd
            required_gap_seconds = spacing_days[min(nudge_count, len(spacing_days) - 1)] * 86400

            if last_nudge_ts > 0 and (now - last_nudge_ts) < required_gap_seconds:
                logger.info(
                    "PROFILE_NUDGE_SKIP  user=%s  reason=too_soon  nudge_count=%d  gap_needed=%dd",
                    user_id[:8], nudge_count, required_gap_seconds // 86400,
                )
                await r.aclose()
                return _empty_result(job_id, user_id)

            # Nudge is allowed -- update tracking
            await r.incr(nudge_count_key)
            await r.set(last_nudge_key, str(now))
            await r.aclose()
        except Exception as exc:
            logger.warning("Profile nudge Redis check failed: %s", exc)

        # Pick 1-2 specific missing fields to ask about
        nudge_fields = missing[:2]

        from app.tasks._llm import llm_text
        from app.core.identity import identity_preamble

        system = identity_preamble(
            assistant_name=getattr(user, "assistant_name", None),
            personality_notes=getattr(user, "personality_notes", None),
        )

        field_descriptions = {
            "name": "their name",
            "timezone": "their timezone or location",
            "assistant_name": "a custom name for you (the assistant)",
            "personality_notes": "how they'd like you to communicate",
            "has_preferences": "their preferences and likes",
            "has_goals_profile": "their goals or projects they're working on",
            "has_challenges": "challenges or obstacles they're facing",
        }
        missing_desc = ", ".join(field_descriptions.get(f, f) for f in nudge_fields)

        nudge_text = await llm_text(
            system=(
                system + "\nYou are sending a casual, friendly nudge to learn more about the user. "
                "Sound like a curious friend, NOT a form or survey. Keep it short (1-2 sentences). "
                "Do NOT list items. Pick ONE thing to ask about naturally."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"I'd like to know more about the user. They haven't shared: {missing_desc}. "
                    f"This is nudge #{nudge_count + 1}. Write a warm, natural-sounding message "
                    f"asking about one of these. Example tone: 'Hey! I realized I don't know "
                    f"much about what you're working towards. Want to share any goals?'"
                ),
            }],
            mock_text=f"Hey! I'd love to learn more about you. Can you tell me about {missing_desc}?",
            timeout_s=10.0,
        )

        await store.log_action(
            user_id=user_id,
            action_type="profile_nudge",
            description=f"Sent profile nudge (score={score:.2f}, missing={','.join(nudge_fields)})",
            outcome="success",
            trigger="scheduled",
        )

    await _record_send(user_id)

    return {
        "job_id": job_id,
        "phone": getattr(user, "phone", ""),
        "address": getattr(user, "phone", ""),
        "channel": payload.get("channel", "sms"),
        "response": nudge_text,
    }


async def handle_smart_checkin(payload: dict) -> dict:
    """
    Smart day check-in -- re-queues through the manager for tool access (D-03).

    Rather than generating a generic check-in via direct LLM call, this handler
    re-queues as a needs_manager job with source=scheduled_checkin so the manager
    can use tools (calendar, tasks, web search) to generate a contextual,
    personalized check-in message.
    """
    user_id = payload.get("user_id", "")
    job_id = payload.get("job_id", "")
    phone = payload.get("phone", "")

    from app.database import AsyncSessionLocal
    from app.memory.store import MemoryStore

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)
        user = await _get_user_by_id(store, user_id)
        if not user:
            return _empty_result(job_id, user_id)
        phone = phone or getattr(user, "phone", "")

    body_prompt = (
        "Generate a thoughtful check-in for the user. Consider their calendar events today, "
        "active tasks, and recent interactions. Be specific and helpful, not generic. "
        "If you have calendar access, mention upcoming meetings. If not, reference their "
        "tasks and goals. Keep it warm and actionable."
    )

    result = await _requeue_via_manager(
        user_id=user_id,
        phone=phone,
        channel=payload.get("channel", "sms"),
        body_prompt=body_prompt,
        source_tag="scheduled_checkin",
        payload=payload,
        action_type="smart_checkin",
    )
    return result


async def handle_insight_observation(payload: dict) -> dict:
    """
    Insight observation -- surface ONE interesting pattern from accumulated
    memories that the user might not have noticed themselves (D-02).

    Gates on 15+ memories minimum -- below that, not enough data for
    meaningful pattern recognition.
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

        if not await _has_content_delta(store, user_id, "insight_observation"):
            logger.info("DELTA_SUPPRESS user=%s category=insight_observation", user_id[:8])
            return _empty_result(job_id, user_id)

        # Gate: need at least 15 memories for meaningful insights
        memories = await store.get_memories(user_id)
        if len(memories) < 15:
            logger.info(
                "INSIGHT_SKIP  user=%s  reason=insufficient_memories  count=%d  min=15",
                user_id[:8], len(memories),
            )
            return _empty_result(job_id, user_id)

        # Load profile entries and recent actions for richer context
        profile_entries = await store.get_profile_entries(user_id)
        recent_actions = await store.get_action_log(user_id, limit=20)

        from app.tasks._llm import llm_text
        from app.core.identity import identity_preamble

        system = identity_preamble(
            assistant_name=getattr(user, "assistant_name", None),
            personality_notes=getattr(user, "personality_notes", None),
        )

        memory_summary = "\n".join(
            f"- {m.key}: {m.value}" for m in memories[:30]
        )
        profile_summary = "\n".join(
            f"- [{e.section}] {e.label}: {e.content}" for e in profile_entries[:15]
        )
        action_summary = "\n".join(
            f"- {a.action_type}: {a.description}" for a in recent_actions[:10]
        )

        insight = await llm_text(
            system=(
                system + "\nBased on everything you know about this user, surface ONE interesting "
                "insight, pattern, or observation they might not have noticed themselves. "
                "Be specific and reference actual data. Examples: noting scheduling patterns, "
                "preference trends, goal alignment observations. Keep it under 100 words."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Here's what I know about this user:\n\n"
                    f"Memories:\n{memory_summary}\n\n"
                    f"Profile:\n{profile_summary}\n\n"
                    f"Recent actions:\n{action_summary}\n\n"
                    f"Surface one interesting insight or pattern."
                ),
            }],
            mock_text="I've noticed you tend to set reminders in the evening but your most productive "
                      "tasks happen in the morning. Maybe scheduling key work earlier could help!",
            timeout_s=10.0,
        )

        await store.log_action(
            user_id=user_id,
            action_type="insight_observation",
            description=f"Surfaced insight from {len(memories)} memories",
            outcome="success",
            trigger="scheduled",
        )

    await _record_send(user_id)

    return {
        "job_id": job_id,
        "phone": getattr(user, "phone", ""),
        "address": getattr(user, "phone", ""),
        "channel": payload.get("channel", "sms"),
        "response": insight,
    }


# ─── Goal coaching handler (D-09, D-10) ───────────────────────────────────


# Coaching states cycle: research -> plan -> check_in -> follow_up -> research
_COACHING_STATES = ["research", "plan", "check_in", "follow_up"]


async def handle_goal_coaching(payload: dict) -> dict:
    """
    Full goal coaching loop via manager tool-calling (D-09, D-10).

    Selects the most relevant active goal, reads coaching state from
    metadata_json, builds a coaching-specific body prompt, and re-queues
    through the manager with source=scheduled_coaching for tool access
    (web search, calendar, email).

    Coaching state machine cycles: research -> plan -> check_in -> follow_up.
    State is persisted in goal.metadata_json under the "coaching" key.
    """
    import json as _json

    user_id = payload.get("user_id", "")
    job_id = payload.get("job_id", "")
    phone = payload.get("phone", "")

    from app.database import AsyncSessionLocal
    from app.memory.store import MemoryStore

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)
        user = await _get_user_by_id(store, user_id)
        if not user:
            return _empty_result(job_id, user_id)

        if not await _has_content_delta(store, user_id, "goal_coaching"):
            logger.info("DELTA_SUPPRESS user=%s category=goal_coaching", user_id[:8])
            return _empty_result(job_id, user_id)

        phone = phone or getattr(user, "phone", "")

        goals = await store.get_goals(user_id, status="active")
        if not goals:
            logger.info("GOAL_COACHING_SKIP  user=%s  reason=no_active_goals", user_id[:8])
            return _empty_result(job_id, user_id)

        # Select the most relevant goal for coaching:
        # 1. Prefer goals with approaching target_date (within 14 days)
        # 2. Otherwise pick the most recently updated goal
        now = datetime.now(timezone.utc)
        approaching = [
            g for g in goals
            if g.target_date and (g.target_date - now) <= timedelta(days=14)
        ]
        if approaching:
            # Pick the one with the soonest target_date
            goal = min(approaching, key=lambda g: g.target_date)
        else:
            # Pick the most recently updated goal
            goal = max(goals, key=lambda g: (g.updated_at or g.created_at or now))

        # Read coaching state from metadata_json
        metadata = {}
        if goal.metadata_json:
            try:
                metadata = _json.loads(goal.metadata_json)
            except (ValueError, TypeError):
                pass

        coaching = metadata.get("coaching", {"state": "research"})
        state = coaching.get("state", "research")
        steps = coaching.get("steps", [])
        current_step = coaching.get("current_step", 0)

        # Build target date string
        target_str = (
            goal.target_date.strftime("%Y-%m-%d") if goal.target_date else "no specific deadline"
        )

        # Build coaching-specific body prompt based on state
        if state == "research":
            body_prompt = (
                f"Research strategies and best practices for achieving this goal: "
                f"'{goal.title}'. Description: {goal.description or 'No description'}. "
                f"Use web search to find actionable advice. Target date: {target_str}."
            )
        elif state == "plan":
            research_results = coaching.get("research_results", "")
            body_prompt = (
                f"Based on previous research, create a step-by-step action plan for: "
                f"'{goal.title}'. Break it into 3-5 concrete next steps the user can "
                f"take this week. {f'Previous research: {research_results[:500]}' if research_results else ''}"
            )
        elif state == "check_in":
            step_text = steps[current_step] if current_step < len(steps) else "their current step"
            body_prompt = (
                f"Check in on progress for goal: '{goal.title}'. "
                f"Current step: {step_text}. "
                f"Ask how it's going and offer encouragement or adjustment."
            )
        elif state == "follow_up":
            body_prompt = (
                f"The user may be stuck on their goal '{goal.title}'. "
                f"Research alternative approaches and suggest a pivot or different strategy."
            )
        else:
            body_prompt = (
                f"Provide coaching for the user's goal: '{goal.title}'. "
                f"Description: {goal.description or 'No description'}. Target: {target_str}."
            )

    # Re-queue via manager with coaching source
    result = await _requeue_via_manager(
        user_id=user_id,
        phone=phone,
        channel=payload.get("channel", "sms"),
        body_prompt=body_prompt,
        source_tag="scheduled_coaching",
        payload=payload,
        action_type="goal_coaching",
    )

    # Advance coaching state after re-queue
    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)
        # Re-read goal to avoid stale state
        from sqlalchemy import select as sa_select
        from app.memory.models import Goal as GoalModel
        goal_result = await db.execute(
            sa_select(GoalModel).where(GoalModel.id == goal.id)
        )
        fresh_goal = goal_result.scalar_one_or_none()
        if fresh_goal:
            meta = {}
            if fresh_goal.metadata_json:
                try:
                    meta = _json.loads(fresh_goal.metadata_json)
                except (ValueError, TypeError):
                    pass

            coaching_data = meta.get("coaching", {"state": "research"})
            current_state = coaching_data.get("state", "research")

            # Advance state: research -> plan -> check_in -> follow_up -> research
            try:
                idx = _COACHING_STATES.index(current_state)
                next_state = _COACHING_STATES[(idx + 1) % len(_COACHING_STATES)]
            except ValueError:
                next_state = "research"

            coaching_data["state"] = next_state
            coaching_data["last_coaching_at"] = now.isoformat()
            meta["coaching"] = coaching_data
            fresh_goal.metadata_json = _json.dumps(meta)
            fresh_goal.updated_at = now
            await db.commit()

            logger.info(
                "GOAL_COACHING  user=%s  goal=%s  state=%s->%s",
                user_id[:8], goal.title[:40], current_state, next_state,
            )

    return result


# ─── Shared re-queue helper ────────────────────────────────────────────────


async def _requeue_via_manager(
    user_id: str,
    phone: str,
    channel: str,
    body_prompt: str,
    source_tag: str,
    payload: dict,
    action_type: str = "requeue_manager",
) -> dict:
    """Re-queue a proactive job as a NEEDS_MANAGER job for tool-assisted processing.

    Shared helper used by handle_smart_checkin, handle_goal_coaching (Plan 03),
    and _execute_reminder_via_manager. Loads user context, builds a manager
    payload, and XADD-s to the Redis stream.

    Uses a fresh Redis connection (not queue_client singleton) because
    the worker process may not have queue_client connected (Research Pitfall 4).

    Returns an empty response dict -- the manager job produces the real response.
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
        logger.warning("Failed to load context for %s re-queue: %s", source_tag, exc)

    manager_payload = {
        "job_id": new_job_id,
        "intent": "needs_manager",
        "phone": phone,
        "address": phone,
        "channel": channel,
        "body": body_prompt,
        "user_id": user_id,
        "persona": "shared",
        "context": context,
        "source": source_tag,
    }

    try:
        r = await aioredis.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True
        )
        await r.xadd(settings.queue_name, {"data": _json.dumps(manager_payload)})
        await r.aclose()
        logger.info(
            "REQUEUE_VIA_MANAGER  source=%s  new_job_id=%s  user=%s",
            source_tag, new_job_id, user_id[:8],
        )
    except Exception as exc:
        logger.error("Failed to re-queue via manager (source=%s): %s", source_tag, exc)
        return {
            "job_id": payload.get("job_id", ""),
            "phone": phone,
            "address": phone,
            "channel": channel,
            "response": "",
        }

    # Log the action
    try:
        async with AsyncSessionLocal() as db:
            store = MemoryStore(db)
            await store.log_action(
                user_id=user_id,
                action_type=action_type,
                description=f"Re-queued as needs_manager (source={source_tag})",
                outcome="success",
                trigger="scheduled",
            )
    except Exception as exc:
        logger.warning("Failed to log %s action: %s", action_type, exc)

    await _record_send(user_id)

    # Return empty response — the manager job will produce the real one
    return {
        "job_id": payload.get("job_id", ""),
        "phone": phone,
        "address": phone,
        "channel": channel,
        "response": "",
    }


# ─── Execute reminder re-queue ───────────────────────────────────────────────

async def _execute_reminder_via_manager(
    user_id: str, task_id: str, title: str, phone: str, original_payload: dict
) -> dict:
    """Re-queue a scheduled 'execute' reminder as a NEEDS_MANAGER job.

    Delegates to _requeue_via_manager for the actual re-queue logic.
    """
    return await _requeue_via_manager(
        user_id=user_id,
        phone=phone,
        channel=original_payload.get("channel", "sms"),
        body_prompt=title,
        source_tag="scheduled_execute",
        payload=original_payload,
        action_type="execute_reminder",
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _profile_completeness(user, entries: list) -> tuple[float, list[str]]:
    """Compute profile completeness score and list of missing fields.

    Checks user-level fields (name, timezone, assistant_name, personality_notes)
    and TELOS profile sections (preferences, goals, challenges).

    Returns (0.0-1.0 score, list of missing field names).
    """
    checks = {
        "name": bool(user and user.name),
        "timezone": bool(user and user.timezone and user.timezone != "America/New_York"),
        "assistant_name": bool(user and getattr(user, "assistant_name", None)),
        "personality_notes": bool(user and getattr(user, "personality_notes", None)),
        "has_preferences": any(getattr(e, "section", "") == "preferences" for e in entries),
        "has_goals_profile": any(getattr(e, "section", "") == "goals" for e in entries),
        "has_challenges": any(getattr(e, "section", "") == "challenges" for e in entries),
    }
    score = sum(checks.values()) / len(checks) if checks else 0.0
    missing = [k for k, v in checks.items() if not v]
    return score, missing


# ─── Feature Discovery Nudges (D-07, D-08, D-09) ─────────────────────────

# Milestones: features users can discover. Each has a nudge with both
# a text command AND a dashboard link per D-09.
FEATURE_NUDGES = {
    "connected_gmail": {
        "description": "Connect your Gmail so I can read and send emails for you.",
        "text_command": "Try saying: 'Send an email to [name] about [topic]'",
        "dashboard_link": "/connections",
        "category": "connections",
    },
    "connected_calendar": {
        "description": "Connect your Google Calendar so I can manage your schedule.",
        "text_command": "Try saying: 'What's on my calendar today?'",
        "dashboard_link": "/connections",
        "category": "connections",
    },
    "created_persona": {
        "description": "Create Work and Personal personas to get context-aware responses.",
        "text_command": "Try saying: 'Create a Work persona'",
        "dashboard_link": "/personas",
        "category": "personas",
    },
    "set_goal": {
        "description": "Set a goal and I'll coach you toward it with check-ins and suggestions.",
        "text_command": "Try saying: 'My goal is to ship v1 by end of month'",
        "dashboard_link": "/goals",
        "category": "goals",
    },
    "configured_quiet_hours": {
        "description": "Set quiet hours so I won't message you at inconvenient times.",
        "text_command": "Try saying: 'Set quiet hours from 10pm to 7am'",
        "dashboard_link": "/settings/proactive",
        "category": "settings",
    },
    "configured_proactive": {
        "description": "Customize which types of proactive messages you receive.",
        "text_command": "Try saying: 'Disable evening recaps' or 'Enable goal coaching'",
        "dashboard_link": "/settings/proactive",
        "category": "settings",
    },
    "used_web_search": {
        "description": "Ask me anything and I'll search the web for answers -- no connection needed!",
        "text_command": "Try saying: 'What's the weather in Austin?'",
        "dashboard_link": "/capabilities",
        "category": "tools",
    },
    "created_custom_agent": {
        "description": "Create custom agents (webhooks or prompts) to extend my capabilities.",
        "text_command": "Check the Capabilities page in your dashboard to get started.",
        "dashboard_link": "/capabilities",
        "category": "agents",
    },
}

# Decaying schedule intervals (D-08): 1 week, 2 weeks, 1 month, then stop
_NUDGE_INTERVALS_DAYS = [7, 14, 30]


async def handle_feature_discovery(payload: dict) -> dict:
    """
    Feature discovery nudge -- suggest features the user hasn't explored (D-07, D-08, D-09).

    Picks one undiscovered feature, sends a nudge with both text-command and dashboard link.
    Respects decaying schedule: after 3 nudges for a feature, stops suggesting it.
    Users can dismiss nudges via proactive_settings_json.
    """
    user_id = payload.get("user_id", "")
    job_id = payload.get("job_id", "")

    from app.database import AsyncSessionLocal
    from app.memory.store import MemoryStore
    import json as _json
    import random

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)
        user = await _get_user_by_id(store, user_id)
        if not user:
            return _empty_result(job_id, user_id)

        # Get achieved milestones
        milestones = await store.get_milestones(user_id)
        achieved_names = {m.milestone_name for m in milestones}

        # Get dismissed nudges from proactive_settings_json
        dismissed = set()
        if user.proactive_settings_json:
            try:
                ps = _json.loads(user.proactive_settings_json)
                dismissed = set(ps.get("dismissed_nudges", []))
            except (ValueError, TypeError):
                pass

        # Find undiscovered, non-dismissed features
        candidates = [
            (name, info) for name, info in FEATURE_NUDGES.items()
            if name not in achieved_names and name not in dismissed
        ]

        if not candidates:
            logger.info("DISCOVERY_NO_CANDIDATES user=%s", user_id[:8])
            return _empty_result(job_id, user_id)

        # Check nudge frequency via action_log (decaying schedule D-08)
        logs = await store.get_action_log(user_id, limit=100)
        eligible = []
        for name, info in candidates:
            # Count how many times this nudge was sent
            nudge_sends = [
                a for a in logs
                if a.action_type == "feature_discovery" and name in (a.description or "")
            ]
            send_count = len(nudge_sends)

            if send_count >= len(_NUDGE_INTERVALS_DAYS):
                continue  # Max nudges reached, stop suggesting

            if send_count > 0:
                last = nudge_sends[0]  # Most recent (logs are desc by created_at)
                interval = _NUDGE_INTERVALS_DAYS[min(send_count, len(_NUDGE_INTERVALS_DAYS) - 1)]
                if (datetime.now(timezone.utc) - last.created_at).days < interval:
                    continue  # Too soon for next nudge

            eligible.append((name, info))

        if not eligible:
            return _empty_result(job_id, user_id)

        # Pick one at random
        name, info = random.choice(eligible)

        from app.tasks._llm import llm_text
        from app.core.identity import identity_preamble

        system = identity_preamble(
            assistant_name=getattr(user, "assistant_name", None),
            personality_notes=getattr(user, "personality_notes", None),
        )

        nudge_text = await llm_text(
            system=system + "\nYou are sending a feature discovery tip. Be casual and brief.",
            messages=[{
                "role": "user",
                "content": (
                    f"Generate a short, friendly feature tip message.\n\n"
                    f"Feature: {info['description']}\n"
                    f"Text command: {info['text_command']}\n"
                    f"Dashboard: {info['dashboard_link']}\n\n"
                    f"Include BOTH the text command suggestion AND the dashboard link. "
                    f"Keep it under 100 words. Don't be pushy."
                ),
            }],
            mock_text=(
                f"Tip: {info['description']} "
                f"{info['text_command']} "
                f"Or visit your dashboard: {info['dashboard_link']}"
            ),
            timeout_s=10.0,
        )

        await store.log_action(
            user_id=user_id,
            action_type="feature_discovery",
            description=f"Sent feature discovery nudge: {name}",
            outcome="success",
            trigger="scheduled",
        )

    await _record_send(user_id)

    return {
        "job_id": job_id,
        "phone": getattr(user, "phone", ""),
        "address": getattr(user, "phone", ""),
        "channel": payload.get("channel", "sms"),
        "response": nudge_text,
    }


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

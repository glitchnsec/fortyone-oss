"""
Reminder and preference task handlers.

handle_reminder  — extract structured reminder data, store Task, confirm.
handle_preference — extract preference key/value, store Memory, confirm.
"""
import json
import logging
from datetime import datetime, timezone

from app.database import AsyncSessionLocal
from app.memory.store import MemoryStore
from app.tasks._llm import llm_messages_json

logger = logging.getLogger(__name__)

REMINDER_SYSTEM = (
    "You are a reminder extraction assistant. "
    "Extract structured reminder data from the user's message and return JSON with these fields: "
    "task (string), due_at (ISO 8601 UTC string or null), recurrence ('none'|'daily'|'weekly'|'monthly'), "
    "contact (string or null), confirmation (one casual friendly sentence confirming the reminder). "
    "Return valid JSON only. If time is relative (e.g. 'tomorrow at 3pm'), convert to absolute UTC. "
    "If time is ambiguous, pick the most sensible interpretation and mention it."
)

PREFERENCE_SYSTEM = (
    "You are a preference extraction assistant. "
    "Extract a user preference from the message and return JSON with: "
    "key (snake_case, e.g. 'preferred_meeting_time'), value (descriptive string), "
    "confirmation (one casual sentence confirming you noted it). Return valid JSON only."
)


import re
from datetime import timedelta


def _parse_relative_time(text: str) -> datetime | None:
    """
    Parse relative time expressions deterministically — no LLM needed.

    Handles:
      "in 5 minutes", "in 30 min", "in 1 hour", "in 2 hours",
      "in an hour", "in half an hour"

    Returns a timezone-aware UTC datetime, or None if no match.
    """
    lower = text.lower()
    now = datetime.now(timezone.utc)

    # "in N minute(s)/min(s)"
    m = re.search(r'\bin\s+(\d+)\s*(?:minutes?|mins?)\b', lower)
    if m:
        return now + timedelta(minutes=int(m.group(1)))

    # "in N hour(s)/hr(s)"
    m = re.search(r'\bin\s+(\d+)\s*(?:hours?|hrs?)\b', lower)
    if m:
        return now + timedelta(hours=int(m.group(1)))

    # "in an hour" / "in 1 hour"
    if re.search(r'\bin\s+(?:an?\s+)?hour\b', lower):
        return now + timedelta(hours=1)

    # "in half an hour" / "in 30 min"
    if re.search(r'\bin\s+half\s+an?\s+hour\b', lower):
        return now + timedelta(minutes=30)

    # "in N seconds" (for testing)
    m = re.search(r'\bin\s+(\d+)\s*(?:seconds?|secs?)\b', lower)
    if m:
        return now + timedelta(seconds=int(m.group(1)))

    return None


def _has_time_reference(text: str) -> bool:
    """Check if the user's message contains any time-related words."""
    lower = text.lower()
    time_patterns = [
        r'\bin\s+\d+\s*(?:min|hour|sec|hr)',
        r'\bin\s+(?:a|an)\s+(?:hour|minute)',
        r'\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?',
        r'\btomorrow\b', r'\btonight\b', r'\btoday\b',
        r'\bnext\s+(?:week|month|hour|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b',
        r'\bmorning\b', r'\bevening\b', r'\bafternoon\b',
        r'\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b',
    ]
    return any(re.search(p, lower) for p in time_patterns)


async def handle_reminder(payload: dict) -> dict:
    job_id: str = payload["job_id"]
    phone: str = payload["phone"]
    body: str = payload["body"]
    context: dict = payload.get("context", {})

    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M UTC")
    tz = context.get("memories", {}).get("timezone", "America/New_York")

    # Separate system instructions from user content — no f-string interpolation (D-10)
    messages = [
        {
            "role": "system",
            "content": (
                REMINDER_SYSTEM
                + f"\nCurrent UTC time: {now_str}"
                + f"\nCurrent year: {now.year}"
                + f"\nUser timezone: {tz}"
                + f"\nIMPORTANT: All dates MUST be in {now.year} or later. Never generate a date in the past."
            ),
        },
        {
            "role": "user",
            "content": body,   # user content ONLY here — no wrapping or interpolation
        },
    ]
    mock_due = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
    # Use the capable model for date extraction — gpt-4o-mini consistently
    # generates past dates (2024) because its training cutoff is old.
    from app.config import get_settings as _get_settings
    _s = _get_settings()
    data = await llm_messages_json(messages, mock_payload={
        "task": body,
        "due_at": mock_due.isoformat(),
        "recurrence": "none",
        "contact": None,
        "confirmation": f"Got it! I'll remind you about that.",
    }, model=_s.llm_model_capable)

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)
        user = await store.get_or_create_user(phone)

        due_at = None
        if data.get("due_at"):
            try:
                due_at = datetime.fromisoformat(str(data["due_at"]).replace("Z", "+00:00"))
                # Guard against LLM hallucinating past dates (common with gpt-4o-mini)
                now = datetime.now(timezone.utc)
                if due_at < now:
                    logger.warning(
                        "REMINDER_DATE_IN_PAST  parsed=%s  now=%s — falling back to relative parse",
                        due_at.isoformat(), now.isoformat(),
                    )
                    due_at = None  # Will try relative parse below
            except (ValueError, TypeError):
                pass

        # Fallback: if LLM failed to produce a valid future date, try to
        # compute it deterministically from the user's message.
        # This handles "in 5 minutes", "in an hour", "in 30 min", etc.
        if due_at is None:
            due_at = _parse_relative_time(body)
            if due_at:
                logger.info(
                    "REMINDER_RELATIVE_PARSE  body=%r  computed=%s",
                    body[:60], due_at.isoformat(),
                )

        # If we still have no due_at after LLM + relative parse, signal
        # failure back to the caller so the manager LLM can ask the user
        # to clarify — NOT silently claim success.
        user_wanted_time = bool(data.get("due_at")) or _has_time_reference(body)
        if due_at is None and user_wanted_time:
            logger.warning(
                "REMINDER_DATE_FAILED  body=%r — could not compute a valid future time",
                body[:60],
            )
            task_title = data.get("task", body)
            # Still store the task (without due_at) so the user can edit it in dashboard
            task = await store.store_task(
                user_id=user.id,
                task_type="reminder",
                title=task_title,
                due_at=None,
                metadata={
                    "contact": data.get("contact"),
                    "recurrence": data.get("recurrence", "none"),
                },
            )
            return {
                "job_id": job_id,
                "phone": phone,
                "response": (
                    f"I saved '{task_title}' as a task, but I couldn't figure out "
                    f"the exact time. Could you tell me when you'd like to be reminded? "
                    f"You can also set the time from your dashboard."
                ),
                "task_id": task.id,
                "error": "date_parse_failed",
                "learn": {
                    "type": "reminder_created",
                    "task": task_title,
                    "due_at": None,
                },
            }

        task = await store.store_task(
            user_id=user.id,
            task_type="reminder",
            title=data.get("task", body),
            due_at=due_at,
            metadata={
                "contact": data.get("contact"),
                "recurrence": data.get("recurrence", "none"),
            },
        )

        # Schedule reminder delivery at due_at via Redis sorted set
        if due_at:
            await _schedule_task_reminder(user.id, task.id, task.title, phone, due_at)

        due_label = ""
        if due_at:
            due_label = due_at.strftime(" — %a %b %-d at %-I:%M %p UTC")

        confirmation = data.get("confirmation") or f"Reminder set: {data.get('task', body)}{due_label}"

        return {
            "job_id": job_id,
            "phone": phone,
            "response": confirmation,
            "task_id": task.id,
            "learn": {
                "type": "reminder_created",
                "task": data.get("task"),
                "due_at": due_at.isoformat() if due_at else None,
            },
        }


async def schedule_task_reminder(
    user_id: str, task_id: str, title: str, phone: str, due_at: datetime
) -> None:
    """
    Schedule a task reminder into Redis sorted set for delivery at due_at.
    The scheduler service picks it up and dispatches to the worker.

    This is the PUBLIC function — called by both handle_reminder and the
    dashboard create_task endpoint.
    """
    await _schedule_task_reminder(user_id, task_id, title, phone, due_at)


async def _schedule_task_reminder(
    user_id: str, task_id: str, title: str, phone: str, due_at: datetime
) -> None:
    """Schedule a task_reminder job into Redis sorted set at due_at timestamp."""
    try:
        import redis.asyncio as aioredis
        from app.config import get_settings
        settings = get_settings()
        r = await aioredis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        payload = json.dumps({
            "type": "task_reminder",
            "user_id": user_id,
            "task_id": task_id,
            "title": title,
            "phone": phone,
            "source": "scheduler",
        })
        score = due_at.timestamp()
        await r.zadd("scheduled_jobs", {payload: score})
        await r.aclose()
        logger.info(
            "REMINDER_SCHEDULED  task_id=%s  user=%s  due_at=%s",
            task_id, user_id[:8], due_at.isoformat(),
        )
    except Exception as exc:
        logger.error("Failed to schedule reminder: %s", exc)


async def handle_preference(payload: dict) -> dict:
    job_id: str = payload["job_id"]
    phone: str = payload["phone"]
    body: str = payload["body"]

    # Separate system instructions from user content — no f-string interpolation (D-10)
    messages = [
        {"role": "system", "content": PREFERENCE_SYSTEM},
        {"role": "user", "content": body},
    ]
    data = await llm_messages_json(messages, mock_payload={
        "key": "preference",
        "value": body,
        "confirmation": "Got it — I'll keep that in mind.",
    })

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)
        user = await store.get_or_create_user(phone)
        await store.store_memory(
            user_id=user.id,
            memory_type="long_term",
            key=data.get("key", "preference"),
            value=data.get("value", body),
        )
        return {
            "job_id": job_id,
            "phone": phone,
            "response": data.get("confirmation", "Noted — I'll remember that."),
            "learn": {
                "type": "preference_stored",
                "key": data.get("key"),
                "value": data.get("value"),
            },
        }

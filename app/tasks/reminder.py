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
    "contact (string or null), "
    "action_type ('notify' if the user wants to be reminded to do something themselves, "
    "'execute' if the user wants ME to do something at that time — e.g. 'tell me a joke', "
    "'send me the weather', 'text me a fun fact'), "
    "confirmation (one casual friendly sentence confirming the reminder). "
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


def _parse_relative_time(text: str, user_timezone: str = "America/New_York") -> datetime | None:
    """
    Parse relative time expressions deterministically — no LLM needed.

    Handles:
      "in 5 minutes", "in 30 min", "in 1 hour", "in 2 hours",
      "in an hour", "in half an hour", "tonight"

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

    # "tonight" → 8pm in the user's timezone
    if re.search(r'\btonight\b', lower):
        try:
            import zoneinfo
            user_tz = zoneinfo.ZoneInfo(user_timezone)
            local_now = now.astimezone(user_tz)
            tonight_local = local_now.replace(hour=20, minute=0, second=0, microsecond=0)
            if tonight_local <= local_now:
                tonight_local += timedelta(days=1)
            # Convert back to UTC for storage
            return tonight_local.astimezone(timezone.utc)
        except Exception:
            # Fallback: 8pm UTC
            tonight = now.replace(hour=20, minute=0, second=0, microsecond=0)
            if tonight <= now:
                tonight += timedelta(days=1)
            return tonight

    return None


def _parse_with_dateparser(text: str, user_timezone: str = "America/New_York") -> datetime | None:
    """
    Parse natural language dates using the dateparser library.

    Handles: "tomorrow at 3pm", "Friday at 6pm", "April 5th at noon",
    "in 2 hours", "at 8pm", "3:30pm", etc.

    Returns a timezone-aware UTC datetime, or None if no match.
    """
    try:
        import dateparser

        # Extract time-related phrases from the text.
        # dateparser works best with isolated time expressions, not full sentences.
        # Try the full text first, then extract common patterns.
        settings = {
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": user_timezone,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TO_TIMEZONE": "UTC",
        }

        result = dateparser.parse(text, settings=settings)
        if result:
            # Ensure timezone-aware
            if result.tzinfo is None:
                result = result.replace(tzinfo=timezone.utc)
            return result

        # Try extracting common time phrases from the sentence
        import re as _re
        # Match "at 3pm", "at 8:30pm", "tomorrow at noon", "friday at 6pm"
        time_phrases = [
            r'(tomorrow\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?)',
            r'((?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?)',
            r'((?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm))',
            r'(tomorrow)',
            r'(tonight)',
        ]
        for pattern in time_phrases:
            m = _re.search(pattern, text.lower())
            if m:
                result = dateparser.parse(m.group(1), settings=settings)
                if result:
                    if result.tzinfo is None:
                        result = result.replace(tzinfo=timezone.utc)
                    return result

        return None
    except ImportError:
        return None
    except Exception:
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
    phone: str = payload.get("phone", "")
    body: str = payload["body"]
    context: dict = payload.get("context", {})
    user_id: str = payload.get("user_id", "")

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
        "action_type": "notify",
        "confirmation": f"Got it! I'll remind you about that.",
    }, model=_s.llm_model_capable)

    # Normalize action_type from LLM extraction
    action_type = data.get("action_type", "notify")
    if action_type not in ("notify", "execute"):
        action_type = "notify"

    # Manager's classification (with full conversation context) takes precedence
    manager_override = payload.get("_manager_action_type")
    if manager_override in ("notify", "execute"):
        action_type = manager_override

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)

        # Use user_id (UUID) for identity when available; fall back to phone
        # for backward compat with in-flight jobs that lack user_id.
        if user_id:
            user = await store.get_user_by_id(user_id)
            if not user:
                user = await store.get_or_create_user(phone)
        else:
            user = await store.get_or_create_user(phone)

        # DETERMINISTIC FIRST: Try relative time parsing BEFORE using the LLM's
        # date. "in 5 minutes", "in 2 mins", "in an hour" are computed exactly
        # with zero LLM dependency. This is the most reliable path.
        original_body = payload.get("_original_body", "")
        due_at = _parse_relative_time(original_body, user_timezone=tz) if original_body else None
        if due_at is None:
            due_at = _parse_relative_time(body, user_timezone=tz)
        if due_at:
            logger.info(
                "REMINDER_RELATIVE_PARSE  original=%r  body=%r  computed=%s",
                original_body[:60], body[:60], due_at.isoformat(),
            )

        # TIER 2: dateparser library — handles "tomorrow at 3pm", "Friday at 6pm",
        # "April 5th at noon", absolute times, etc. deterministically.
        if due_at is None:
            parse_text = original_body or body
            due_at = _parse_with_dateparser(parse_text, user_timezone=tz)
            if due_at is None and original_body:
                due_at = _parse_with_dateparser(body, user_timezone=tz)
            if due_at:
                now_check = datetime.now(timezone.utc)
                if due_at < now_check:
                    logger.warning(
                        "DATEPARSER_PAST  parsed=%s  now=%s — discarding",
                        due_at.isoformat(), now_check.isoformat(),
                    )
                    due_at = None
                else:
                    logger.info(
                        "REMINDER_DATEPARSER  text=%r  computed=%s",
                        parse_text[:60], due_at.isoformat(),
                    )

        # TIER 3: LLM's date — only if both deterministic parsers failed
        if due_at is None and data.get("due_at"):
            try:
                due_at = datetime.fromisoformat(str(data["due_at"]).replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if due_at < now:
                    logger.warning(
                        "REMINDER_DATE_IN_PAST  parsed=%s  now=%s — discarding",
                        due_at.isoformat(), now.isoformat(),
                    )
                    due_at = None
            except (ValueError, TypeError):
                pass

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
                    "action_type": action_type,
                },
            )
            return {
                "job_id": job_id,
                "phone": phone,
                "address": payload.get("address", phone),
                "channel": payload.get("channel", "sms"),
                "user_id": user.id,
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
                "action_type": action_type,
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
            "address": payload.get("address", phone),
            "channel": payload.get("channel", "sms"),
            "user_id": user.id,
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
    phone: str = payload.get("phone", "")
    body: str = payload["body"]
    user_id: str = payload.get("user_id", "")

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

        # Use user_id (UUID) for identity when available; fall back to phone
        if user_id:
            user = await store.get_user_by_id(user_id)
            if not user:
                user = await store.get_or_create_user(phone)
        else:
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
            "address": payload.get("address", phone),
            "channel": payload.get("channel", "sms"),
            "user_id": user.id,
            "response": data.get("confirmation", "Noted — I'll remember that."),
            "learn": {
                "type": "preference_stored",
                "key": data.get("key"),
                "value": data.get("value"),
            },
        }

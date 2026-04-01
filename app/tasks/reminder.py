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


async def handle_reminder(payload: dict) -> dict:
    job_id: str = payload["job_id"]
    phone: str = payload["phone"]
    body: str = payload["body"]
    context: dict = payload.get("context", {})

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tz = context.get("memories", {}).get("timezone", "America/New_York")

    # Separate system instructions from user content — no f-string interpolation (D-10)
    messages = [
        {
            "role": "system",
            "content": REMINDER_SYSTEM + f"\nCurrent UTC time: {now_str}\nUser timezone: {tz}",
        },
        {
            "role": "user",
            "content": body,   # user content ONLY here — no wrapping or interpolation
        },
    ]
    mock_due = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
    data = await llm_messages_json(messages, mock_payload={
        "task": body,
        "due_at": mock_due.isoformat(),
        "recurrence": "none",
        "contact": None,
        "confirmation": f"Got it! I'll remind you about that.",
    })

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)
        user = await store.get_or_create_user(phone)

        due_at = None
        if data.get("due_at"):
            try:
                due_at = datetime.fromisoformat(str(data["due_at"]).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

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

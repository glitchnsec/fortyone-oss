"""
Reminder and preference task handlers.

handle_reminder  — extract structured reminder data, store Task, confirm.
handle_preference — extract preference key/value, store Memory, confirm.
"""
import json
import logging
from datetime import datetime, timezone

from app.database import SessionLocal
from app.memory.store import MemoryStore
from app.tasks._llm import llm_json

logger = logging.getLogger(__name__)


async def handle_reminder(payload: dict) -> dict:
    job_id: str = payload["job_id"]
    phone: str = payload["phone"]
    body: str = payload["body"]
    context: dict = payload.get("context", {})

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tz = context.get("memories", {}).get("timezone", "America/New_York")

    prompt = f"""Extract reminder details from the user message and return JSON.

User message: "{body}"
Current UTC time: {now_str}
User timezone: {tz}

Return JSON with these fields:
- task: string — what to remind about
- due_at: ISO 8601 UTC string — when, or null if unclear
- recurrence: "none" | "daily" | "weekly" | "monthly"
- contact: string or null — person involved
- confirmation: string — one casual, friendly sentence confirming the reminder

If time is relative (e.g. "tomorrow at 3pm"), convert to absolute UTC.
If time is ambiguous, pick the most sensible interpretation and mention it."""

    mock_due = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
    data = await llm_json(prompt, mock_payload={
        "task": body,
        "due_at": mock_due.isoformat(),
        "recurrence": "none",
        "contact": None,
        "confirmation": f"Got it! I'll remind you: {body}.",
    })

    db = SessionLocal()
    try:
        store = MemoryStore(db)
        user = store.get_or_create_user(phone)

        due_at = None
        if data.get("due_at"):
            try:
                due_at = datetime.fromisoformat(str(data["due_at"]).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        task = store.store_task(
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
    finally:
        db.close()


async def handle_preference(payload: dict) -> dict:
    job_id: str = payload["job_id"]
    phone: str = payload["phone"]
    body: str = payload["body"]

    prompt = f"""Extract a user preference from this message and return JSON.

User message: "{body}"

Return JSON with:
- key: snake_case key (e.g. "preferred_meeting_time", "communication_style", "wake_up_time")
- value: descriptive string for the preference value
- confirmation: one casual sentence confirming you noted it"""

    data = await llm_json(prompt, mock_payload={
        "key": "preference",
        "value": body,
        "confirmation": "Got it — I'll keep that in mind.",
    })

    db = SessionLocal()
    try:
        store = MemoryStore(db)
        user = store.get_or_create_user(phone)
        store.store_memory(
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
    finally:
        db.close()

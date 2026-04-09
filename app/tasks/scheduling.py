"""
Scheduling suggestion handler.

Uses user memory (preferred_meeting_time, preferred_days, etc.) to produce
a contextual suggestion.  Falls back gracefully when no LLM key is present.
"""
import logging
from datetime import datetime, timezone

from app.tasks._llm import llm_text

logger = logging.getLogger(__name__)


async def handle_scheduling(payload: dict) -> dict:
    job_id: str = payload["job_id"]
    phone: str = payload["phone"]
    body: str = payload["body"]
    context: dict = payload.get("context", {})

    memories: dict = context.get("memories", {})
    user_tz_name = context.get("user", {}).get("timezone") or "America/New_York"

    # Show time in user's local timezone so the LLM suggests local times
    try:
        import zoneinfo
        user_tz = zoneinfo.ZoneInfo(user_tz_name)
        now_local = datetime.now(user_tz)
        now_str = now_local.strftime(f"%A, %B %d %Y, %I:%M %p ({user_tz_name})")
    except Exception:
        now_str = datetime.now(timezone.utc).strftime("%A, %B %d %Y, %I:%M %p UTC")

    # Build a compact preference summary
    pref_lines: list[str] = []
    relevant_keys = {
        "preferred_meeting_time", "preferred_days", "preferred_time_of_day",
        "wake_up_time", "work_hours",
    }
    for k, v in memories.items():
        if k in relevant_keys:
            pref_lines.append(f"  - {k.replace('_', ' ')}: {v}")

    pref_block = "\n".join(pref_lines) if pref_lines else "  (no preferences stored yet)"

    system = (
        "You are a personal executive assistant helping with scheduling. "
        "Be concise (2–3 sentences max). Reference the user's preferences when relevant. "
        "Suggest specific times in the user's local timezone. Be proactive."
    )

    user_msg = (
        f"Current time: {now_str}\n"
        f"User timezone: {user_tz_name}\n\n"
        f"Known preferences:\n{pref_block}\n\n"
        f"User says: \"{body}\""
    )

    # Build a sensible mock suggestion using whatever preferences we have
    preferred_time = memories.get("preferred_meeting_time") or memories.get("preferred_time_of_day") or "morning"
    mock_response = (
        f"You usually prefer {preferred_time}s — how about Tuesday or Thursday at 9 AM? "
        "Let me know and I'll block it for you."
    )

    suggestion = await llm_text(
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        mock_text=mock_response,
    )

    return {
        "job_id": job_id,
        "phone": phone,
        "address": payload.get("address", phone),
        "channel": payload.get("channel", "sms"),
        "user_id": payload.get("user_id", ""),
        "response": suggestion,
        "learn": {"type": "scheduling_request", "query": body},
    }

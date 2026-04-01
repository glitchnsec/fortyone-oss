"""
Recall, general conversation, and task-completion handlers.
"""
import json
import logging
import re

from app.database import AsyncSessionLocal
from app.memory.store import MemoryStore
from app.tasks._llm import llm_json, llm_messages_json, llm_text

logger = logging.getLogger(__name__)

GENERAL_SYSTEM = (
    "You are a personal assistant communicating via SMS/chat. "
    "Be concise, warm, and human. Keep your reply under 3 sentences — no bullet points. "
    "Return JSON: {\"response\": \"your reply\", \"profile\": {\"name\": null or string, "
    "\"timezone\": null or IANA string, \"email\": null or string, \"assistant_name\": null or string}}. "
    "Only populate a profile field if the user explicitly mentioned it in THIS message."
)


async def handle_recall(payload: dict) -> dict:
    """Return a formatted list of the user's active tasks/reminders."""
    job_id: str = payload["job_id"]
    phone: str = payload["phone"]

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)
        user = await store.get_or_create_user(phone)
        tasks = await store.get_active_tasks(user.id)

        if not tasks:
            return {
                "job_id": job_id,
                "phone": phone,
                "response": "You're all clear — no pending reminders or tasks right now. 🎉",
            }

        lines = ["Here's what I have for you:"]
        for i, task in enumerate(tasks[:8], 1):
            due_str = ""
            if task.due_at:
                due_str = f" — due {task.due_at.strftime('%a %b %-d, %-I:%M %p')}"
            lines.append(f"{i}. {task.title}{due_str}")

        if len(tasks) > 8:
            lines.append(f"...and {len(tasks) - 8} more.")

        return {
            "job_id": job_id,
            "phone": phone,
            "response": "\n".join(lines),
        }


async def handle_complete(payload: dict) -> dict:
    """
    Mark a task complete based on fuzzy name matching.
    User can say "done with call John" and we find the best match.
    """
    job_id: str = payload["job_id"]
    phone: str = payload["phone"]
    body: str = payload["body"]

    async with AsyncSessionLocal() as db:
        store = MemoryStore(db)
        user = await store.get_or_create_user(phone)
        tasks = await store.get_active_tasks(user.id)

        if not tasks:
            return {
                "job_id": job_id,
                "phone": phone,
                "response": "You don't have any active tasks to mark complete.",
            }

        # Simple keyword matching — find the task whose title best overlaps
        body_lower = body.lower()
        best_task = None
        best_score = 0

        for task in tasks:
            words = set(re.findall(r"\w+", task.title.lower()))
            body_words = set(re.findall(r"\w+", body_lower))
            overlap = len(words & body_words)
            if overlap > best_score:
                best_score = overlap
                best_task = task

        if best_task and best_score > 0:
            await store.complete_task(task_id=best_task.id, user_id=user.id)
            return {
                "job_id": job_id,
                "phone": phone,
                "response": f"Done! ✓ Marked \"{best_task.title}\" as complete.",
            }

        # Couldn't match — show list
        task_list = "\n".join(f"{i+1}. {t.title}" for i, t in enumerate(tasks[:5]))
        return {
            "job_id": job_id,
            "phone": phone,
            "response": f"Which one did you complete? Here are your active tasks:\n{task_list}",
        }


async def handle_general(payload: dict) -> dict:
    """
    Catch-all handler for general conversation.

    Single LLM call returns both:
      • response  — the reply to send
      • profile   — any personal details the user mentioned in this turn
                    (name, timezone, email, assistant_name preference)
                    stored as a profile_update learn signal at zero extra cost.
    """
    job_id: str = payload["job_id"]
    phone: str   = payload["phone"]
    body: str    = payload["body"]
    context: dict = payload.get("context", {})

    memories: dict      = context.get("memories", {})
    recent_msgs: list   = context.get("recent_messages", [])
    user_info: dict     = context.get("user", {})

    name = user_info.get("name") or memories.get("name")
    name_line = f"User's name: {name}" if name else ""

    memory_lines = "\n".join(
        f"  {k}: {v}" for k, v in memories.items()
        if k not in ("name", "greeted", "onboarding_step")
    ) or "  (nothing stored yet)"

    history_json = json.dumps([
        {
            "role": "user" if m["direction"] == "inbound" else "assistant",
            "content": m["body"],
        }
        for m in recent_msgs[-6:]
    ])

    # Build context block (safe — no user body in system message)
    context_lines = []
    if name_line:
        context_lines.append(name_line)
    context_lines.append(f"What I know about this user:\n{memory_lines}")
    context_lines.append(f"Recent conversation:\n{history_json}")
    system_with_context = GENERAL_SYSTEM + "\n\n" + "\n\n".join(context_lines)

    messages = [
        {"role": "system", "content": system_with_context},
        {"role": "user", "content": body},   # body ONLY in user role
    ]
    mock_response = (
        "I'm here to help with reminders, scheduling, and keeping things on track. "
        "What can I do for you?"
    )
    data = await llm_messages_json(messages, mock_payload={"response": mock_response, "profile": {}})

    profile = {k: v for k, v in data.get("profile", {}).items() if v}
    logger.info("GENERAL  job_id=%s  profile_found=%s", job_id, list(profile))

    result: dict = {
        "job_id":  job_id,
        "phone":   phone,
        "address": payload.get("address", phone),
        "channel": payload.get("channel", "sms"),
        "response": data.get("response") or mock_response,
    }
    if profile:
        result["learn"] = {"type": "profile_update", "fields": profile}

    return result

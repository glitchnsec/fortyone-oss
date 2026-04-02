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


_RECALL_SYSTEM = (
    "You are a personal assistant communicating via SMS/chat. "
    "The user asked what you know about them. "
    "Summarize their stored information naturally and conversationally. "
    "Include their profile, memories, and active tasks. "
    "Be warm but concise — 3-5 sentences max. "
    "Return JSON: {\"response\": \"your summary\"}"
)


async def handle_recall(payload: dict) -> dict:
    """
    Return a natural-language summary of what the assistant knows about the user.

    Uses context from the pipeline payload (memories, active_tasks, user profile)
    rather than querying the DB directly — context assembly is handled upstream.
    Falls back to a static summary when LLM is unavailable.
    """
    job_id: str = payload["job_id"]
    phone: str = payload["phone"]
    body: str = payload.get("body", "What do you know about me?")

    context: dict = payload.get("context", {})
    memories: dict = context.get("memories", {})
    active_tasks: list = context.get("active_tasks", [])
    user_info: dict = context.get("user", {})

    name = user_info.get("name") or memories.get("name")
    timezone = user_info.get("timezone") or memories.get("timezone")

    # Build structured context block for the LLM
    profile_parts = []
    if name:
        profile_parts.append(f"name={name}")
    if timezone:
        profile_parts.append(f"timezone={timezone}")
    profile_line = ", ".join(profile_parts) if profile_parts else "(no profile yet)"

    memory_lines = "\n".join(
        f"  {k}: {v}" for k, v in memories.items()
        if k not in ("name", "timezone", "greeted", "onboarding_step")
    ) or "  (none stored yet)"

    task_lines = "\n".join(
        f"  - {t.get('title', 'Untitled')}"
        + (f" (due {t['due_at']})" if t.get("due_at") else "")
        for t in active_tasks[:8]
    ) or "  (none)"

    user_message = (
        f"User asked: {body}\n\n"
        f"Profile: {profile_line}\n"
        f"Memories:\n{memory_lines}\n"
        f"Active tasks:\n{task_lines}"
    )

    # Build mock fallback — static summary without LLM
    mock_parts = []
    if name:
        mock_parts.append(f"Your name is {name}.")
    if timezone:
        mock_parts.append(f"Your timezone is {timezone}.")

    stored_memories = {k: v for k, v in memories.items()
                       if k not in ("name", "timezone", "greeted", "onboarding_step")}
    if stored_memories:
        items = ", ".join(f"{k}: {v}" for k, v in list(stored_memories.items())[:5])
        mock_parts.append(f"I remember: {items}.")

    if active_tasks:
        titles = ", ".join(t.get("title", "Untitled") for t in active_tasks[:5])
        mock_parts.append(f"Active tasks: {titles}.")

    if not mock_parts:
        mock_text = "I don't have much stored about you yet — the more we chat, the more I'll remember!"
    else:
        mock_text = "Here's what I know about you: " + " ".join(mock_parts)

    messages = [
        {"role": "system", "content": _RECALL_SYSTEM},
        {"role": "user", "content": user_message},
    ]
    data = await llm_messages_json(messages, mock_payload={"response": mock_text})

    return {
        "job_id": job_id,
        "phone": phone,
        "address": payload.get("address", phone),
        "channel": payload.get("channel", "sms"),
        "response": data.get("response") or mock_text,
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

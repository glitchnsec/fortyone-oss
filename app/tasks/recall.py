"""
Recall, general conversation, and task-completion handlers.
"""
import logging
import re

from app.database import SessionLocal
from app.memory.store import MemoryStore
from app.tasks._llm import llm_text

logger = logging.getLogger(__name__)


async def handle_recall(payload: dict) -> dict:
    """Return a formatted list of the user's active tasks/reminders."""
    job_id: str = payload["job_id"]
    phone: str = payload["phone"]

    db = SessionLocal()
    try:
        store = MemoryStore(db)
        user = store.get_or_create_user(phone)
        tasks = store.get_active_tasks(user.id)

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
    finally:
        db.close()


async def handle_complete(payload: dict) -> dict:
    """
    Mark a task complete based on fuzzy name matching.
    User can say "done with call John" and we find the best match.
    """
    job_id: str = payload["job_id"]
    phone: str = payload["phone"]
    body: str = payload["body"]

    db = SessionLocal()
    try:
        store = MemoryStore(db)
        user = store.get_or_create_user(phone)
        tasks = store.get_active_tasks(user.id)

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
            store.complete_task(best_task.id)
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
    finally:
        db.close()


async def handle_general(payload: dict) -> dict:
    """
    Catch-all handler for unclassified messages.
    Uses conversation history + memory as context for the LLM.
    """
    job_id: str = payload["job_id"]
    phone: str = payload["phone"]
    body: str = payload["body"]
    context: dict = payload.get("context", {})

    memories: dict = context.get("memories", {})
    recent_messages: list = context.get("recent_messages", [])
    user_info: dict = context.get("user", {})

    name = user_info.get("name") or memories.get("name")
    greeting = f"The user's name is {name}. " if name else ""

    memory_lines = [f"  - {k}: {v}" for k, v in memories.items() if k != "name"]
    memory_block = "\n".join(memory_lines) if memory_lines else "  (nothing stored yet)"

    system = (
        f"You are a personal SMS assistant — concise, proactive, human. "
        f"Keep replies under 3 sentences. {greeting}"
        f"What I know about this user:\n{memory_block}"
    )

    # Build conversation history for the LLM
    history: list[dict] = []
    for msg in recent_messages[-6:]:
        role = "user" if msg["direction"] == "inbound" else "assistant"
        history.append({"role": role, "content": msg["body"]})
    history.append({"role": "user", "content": body})

    mock_text = (
        "I'm here to help with reminders, scheduling, and keeping things on track. "
        "Try asking me to set a reminder or find a meeting time."
    )

    response = await llm_text(system=system, messages=history, mock_text=mock_text)

    return {
        "job_id": job_id,
        "phone": phone,
        "response": response,
    }

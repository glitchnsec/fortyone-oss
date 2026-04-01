"""
Debug endpoints — DEV ONLY.

These routes are conditionally registered in app/main.py only when
ENVIRONMENT=development. They are never exposed in staging or production.
"""
import logging
from urllib.parse import unquote

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.memory.models import Memory, Message, Task, User

logger = logging.getLogger(__name__)
router = APIRouter()


@router.delete("/users/{phone}/onboarding", tags=["Debug"])
async def reset_user(phone: str) -> JSONResponse:
    """
    Full user reset — wipes all memories, conversation history, and tasks,
    then resets the user row so the next message starts completely fresh.

    phone must be URL-encoded, e.g. %2B15551234567 for +15551234567
    """
    phone = unquote(phone)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.phone == phone))
        user = result.scalars().first()
        if not user:
            return JSONResponse(status_code=404, content={"error": f"No user found for {phone}"})

        mem_result = await db.execute(select(Memory).where(Memory.user_id == user.id))
        memories = mem_result.scalars().all()
        msg_result = await db.execute(select(Message).where(Message.user_id == user.id))
        messages = msg_result.scalars().all()
        task_result = await db.execute(select(Task).where(Task.user_id == user.id))
        tasks = task_result.scalars().all()

        for obj in list(memories) + list(messages) + list(tasks):
            await db.delete(obj)

        user.name = None
        user.timezone = "America/New_York"
        await db.commit()

        logger.info(
            "USER_RESET  phone=%s  memories=%d  messages=%d  tasks=%d",
            phone, len(memories), len(messages), len(tasks),
        )

        return JSONResponse(content={
            "phone": phone,
            "reset": True,
            "deleted": {
                "memories": len(memories),
                "messages": len(messages),
                "tasks": len(tasks),
            },
            "message": "User fully reset — next message starts fresh.",
        })


@router.get("/users", tags=["Debug"])
async def debug_users() -> JSONResponse:
    """Dev-only: inspect stored users, memories, and tasks."""
    async with AsyncSessionLocal() as db:
        users_result = await db.execute(select(User))
        users = users_result.scalars().all()
        output = []
        for u in users:
            mem_result = await db.execute(
                select(Memory).where(Memory.user_id == u.id)
            )
            memories = mem_result.scalars().all()
            task_result = await db.execute(
                select(Task).where(Task.user_id == u.id, Task.completed == False)  # noqa: E712
            )
            tasks = task_result.scalars().all()
            output.append({
                "phone": u.phone,
                "name": u.name,
                "timezone": u.timezone,
                "first_seen": u.created_at.isoformat(),
                "last_seen": u.last_seen_at.isoformat(),
                "memories": {m.key: m.value for m in memories},
                "active_tasks": [
                    {
                        "title": t.title,
                        "type": t.task_type,
                        "due_at": t.due_at.isoformat() if t.due_at else None,
                    }
                    for t in tasks
                ],
            })
        return JSONResponse(content=output)

"""
Debug endpoints — DEV ONLY.

These routes are conditionally registered in app/main.py only when
ENVIRONMENT=development. They are never exposed in staging or production.

Note: Uses synchronous SessionLocal (not async) since this plan runs
independently of plan 01-01 which adds AsyncSessionLocal. Will be updated
to async sessions when plan 01-01 is merged.
"""
import logging
from urllib.parse import unquote

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.database import SessionLocal
from app.memory.models import Memory, Message, Task, User
from sqlalchemy import select

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
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.phone == phone).first()
        if not user:
            return JSONResponse(status_code=404, content={"error": f"No user found for {phone}"})

        memories = db.query(Memory).filter(Memory.user_id == user.id).all()
        messages = db.query(Message).filter(Message.user_id == user.id).all()
        tasks = db.query(Task).filter(Task.user_id == user.id).all()

        for obj in list(memories) + list(messages) + list(tasks):
            db.delete(obj)

        user.name = None
        user.timezone = "America/New_York"
        db.commit()

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
    finally:
        db.close()


@router.get("/users", tags=["Debug"])
async def debug_users() -> JSONResponse:
    """Dev-only: inspect stored users, memories, and tasks."""
    db = SessionLocal()
    try:
        users = db.query(User).all()
        output = []
        for u in users:
            memories = db.query(Memory).filter(Memory.user_id == u.id).all()
            tasks = db.query(Task).filter(
                Task.user_id == u.id, Task.completed == False  # noqa: E712
            ).all()
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
    finally:
        db.close()

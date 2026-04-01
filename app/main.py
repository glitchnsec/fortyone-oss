"""
FastAPI application entry point.

Startup:
  1. Init SQLite database (create tables)
  2. Connect Redis queue client
  3. Build channel registry (SMS + Slack)
  4. Start ResponseListener background task (pub/sub → delivery)

Shutdown:
  1. Cancel listener
  2. Disconnect Redis

Adding a new channel:
  1. Implement app/channels/<name>.py
  2. Add it to the CHANNELS dict below
  3. Add its inbound route under app/routes/<name>.py
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.channels.sms import SMSChannel
from app.channels.slack import SlackChannel
from app.config import get_settings
from app.core.pipeline import ResponseListener
from app.database import init_db
from app.queue.client import queue_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    # ── Startup ───────────────────────────────────────────────────────────────
    await init_db()
    logger.info("Database ready")

    await queue_client.connect()
    logger.info("Queue client connected")

    # ── Channel registry ──────────────────────────────────────────────────────
    # Map channel name → Channel instance.  ResponseListener uses this to
    # route completed worker jobs back to the right delivery channel.
    channels = {
        "sms":   SMSChannel(),
        "slack": SlackChannel(),
    }
    logger.info("Channels registered: %s", list(channels))

    # Separate Redis connection for pub/sub (pub/sub clients can't issue other commands)
    listener_redis = await aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )

    listener = ResponseListener(channels=channels)
    listener_task = asyncio.create_task(listener.start(listener_redis))
    logger.info("ResponseListener started")

    app.state.listener_redis = listener_redis
    app.state.listener_task = listener_task

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    listener_task.cancel()
    try:
        await listener_task
    except asyncio.CancelledError:
        pass

    await listener_redis.aclose()
    await queue_client.disconnect()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Personal Assistant",
    description="Hybrid always-on + async-worker personal assistant. Supports SMS and Slack.",
    version="0.2.0",
    lifespan=lifespan,
)

# ── Routes ────────────────────────────────────────────────────────────────────

from app.routes.sms import router as sms_router        # noqa: E402
from app.routes.slack import router as slack_router    # noqa: E402

app.include_router(sms_router,   prefix="/sms",   tags=["SMS"])
app.include_router(slack_router, prefix="/slack", tags=["Slack"])


@app.get("/health", tags=["Meta"])
async def health() -> dict:
    return {"status": "ok", "environment": settings.environment}


@app.delete("/debug/users/{phone}/onboarding", tags=["Debug"])
async def reset_onboarding(phone: str) -> JSONResponse:
    """
    Reset onboarding state for a user so they go through the name/timezone
    flow again on their next message.  Also clears name + timezone.

    phone must be URL-encoded, e.g. %2B15551234567 for +15551234567
    """
    from urllib.parse import unquote
    from sqlalchemy import select, delete
    from app.database import AsyncSessionLocal
    from app.memory.models import Memory, User

    phone = unquote(phone)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.phone == phone))
        user = result.scalars().first()
        if not user:
            return JSONResponse(status_code=404, content={"error": f"No user found for {phone}"})

        keys_to_clear = {"onboarding_step", "name", "first_seen"}
        deleted = []
        for key in keys_to_clear:
            mem_result = await db.execute(
                select(Memory).where(Memory.user_id == user.id, Memory.key == key)
            )
            mem = mem_result.scalars().first()
            if mem:
                await db.delete(mem)
                deleted.append(key)

        # Reset name + timezone on the User row itself
        user.name = None
        user.timezone = "America/New_York"
        await db.commit()

        return JSONResponse(content={
            "phone": phone,
            "reset": True,
            "memories_cleared": deleted,
            "message": "Onboarding reset — next message will restart the flow.",
        })


@app.get("/debug/users", tags=["Debug"])
async def debug_users() -> JSONResponse:
    """Dev-only: inspect stored users, memories, and tasks."""
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.memory.models import Memory, Task, User

    async with AsyncSessionLocal() as db:
        users_result = await db.execute(select(User))
        users = users_result.scalars().all()
        result = []
        for u in users:
            memories_result = await db.execute(
                select(Memory).where(Memory.user_id == u.id)
            )
            memories = memories_result.scalars().all()
            tasks_result = await db.execute(
                select(Task).where(Task.user_id == u.id, Task.completed == False)  # noqa: E712
            )
            tasks = tasks_result.scalars().all()
            result.append({
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
        return JSONResponse(content=result)

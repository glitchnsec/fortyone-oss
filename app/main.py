"""
FastAPI application entry point.

Startup:
  1. Init SQLite database (create tables)
  2. Connect Redis queue client
  3. Start ResponseListener background task (pub/sub → SMS delivery)

Shutdown:
  1. Cancel listener
  2. Disconnect Redis
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.core.pipeline import ResponseListener
from app.database import init_db
from app.queue.client import queue_client
from app.sms.client import SMSClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    # ── Startup ───────────────────────────────────────────────────────────────
    init_db()
    logger.info("Database ready")

    await queue_client.connect()
    logger.info("Queue client connected")

    # Separate Redis connection for pub/sub (pub/sub clients can't issue other commands)
    listener_redis = await aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )

    sms_client = SMSClient()
    listener = ResponseListener(sms=sms_client)
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
    title="SMS Personal Assistant",
    description="Hybrid always-on + async-worker personal assistant over SMS.",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Routes ────────────────────────────────────────────────────────────────────

from app.routes.sms import router as sms_router  # noqa: E402

app.include_router(sms_router, prefix="/sms", tags=["SMS"])


@app.get("/health", tags=["Meta"])
async def health() -> dict:
    return {"status": "ok", "environment": settings.environment}


@app.get("/debug/users", tags=["Debug"])
async def debug_users() -> JSONResponse:
    """Dev-only: inspect stored users, memories, and tasks."""
    from app.database import SessionLocal
    from app.memory.models import Memory, Task, User

    db = SessionLocal()
    try:
        users = db.query(User).all()
        result = []
        for u in users:
            memories = db.query(Memory).filter(Memory.user_id == u.id).all()
            tasks = db.query(Task).filter(Task.user_id == u.id, Task.completed == False).all()  # noqa: E712
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
    finally:
        db.close()

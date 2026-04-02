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
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import FastAPI

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

from app.routes.auth import router as auth_router          # noqa: E402
from app.routes.dashboard import router as dashboard_router  # noqa: E402
from app.routes.personas import router as personas_router  # noqa: E402
from app.routes.sms import router as sms_router            # noqa: E402
from app.routes.slack import router as slack_router        # noqa: E402

# Auth + dashboard routers must be registered BEFORE any static/catch-all mounts
app.include_router(auth_router, prefix="/auth", tags=["Auth"])
app.include_router(dashboard_router, tags=["Dashboard"])
app.include_router(personas_router)
app.include_router(sms_router, prefix="/sms", tags=["SMS"])

# ── Slack routes (disabled when signing secret is not configured) ─────────────
if settings.slack_signing_secret:
    app.include_router(slack_router, prefix="/slack", tags=["Slack"])
    logger.info("Slack routes registered")
else:
    logger.info("Slack routes disabled — SLACK_SIGNING_SECRET not set")

# ── Debug routes (development only) ──────────────────────────────────────────
if settings.environment == "development":
    from app.routes.debug import router as debug_router  # noqa: E402
    app.include_router(debug_router, prefix="/debug", tags=["Debug"])
    logger.info("Debug routes registered (ENVIRONMENT=development)")


@app.get("/health", tags=["Meta"])
async def health() -> dict:
    return {"status": "ok", "environment": settings.environment}


# ── SPA static serving ────────────────────────────────────────────────────────
# CRITICAL: This MUST come after all include_router() calls.
# API routes registered above are unaffected; this catch-all only fires for
# unmatched paths (e.g. /connections, /conversations, /settings).
from fastapi.staticfiles import StaticFiles   # noqa: E402
from fastapi.responses import FileResponse    # noqa: E402

_DASHBOARD_DIST = os.path.join(os.path.dirname(__file__), "..", "dashboard", "dist")

if os.path.exists(_DASHBOARD_DIST):
    # Mount /assets so Vite JS/CSS bundles (in dist/assets/) are served correctly
    _assets_dir = os.path.join(_DASHBOARD_DIST, "assets")
    if os.path.exists(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="static_assets")

    # Catch-all: returns index.html for all paths not already handled by API routes.
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        return FileResponse(os.path.join(_DASHBOARD_DIST, "index.html"))

    logger.info("SPA static mount registered from %s", _DASHBOARD_DIST)
else:
    logger.info("dashboard/dist not found — SPA static mount skipped (run npm run build)")


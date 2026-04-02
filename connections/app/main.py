"""Connections service — OAuth flow and credential vending machine."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database import engine, Base
from app.routes.oauth import router as oauth_router
from app.routes.connections import router as conn_router
from app.routes.tools import router as tools_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _run_startup_migrations(sync_conn):
    """Add columns that were added to models after initial table creation.
    create_all only creates missing tables — it won't ALTER existing ones.
    This runs synchronously inside run_sync() — receives a raw connection."""
    from sqlalchemy import inspect, text
    try:
        inspector = inspect(sync_conn)
        if inspector.has_table("connections"):
            columns = {c["name"] for c in inspector.get_columns("connections")}
            if "persona_id" not in columns:
                sync_conn.execute(text("ALTER TABLE connections ADD COLUMN persona_id VARCHAR"))
                logger.info("STARTUP_MIGRATION added connections.persona_id column")
            else:
                logger.info("STARTUP_MIGRATION connections.persona_id already exists — skipping")
        else:
            logger.info("STARTUP_MIGRATION connections table not found — create_all will handle it")
    except Exception as exc:
        logger.error("STARTUP_MIGRATION failed: %s", exc, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_run_startup_migrations)
    logger.info("Connections service started — schema up to date")
    yield
    await engine.dispose()


app = FastAPI(title="Connections Service", lifespan=lifespan)
app.include_router(oauth_router, tags=["OAuth"])
app.include_router(conn_router, tags=["Connections"])
app.include_router(tools_router, tags=["Tools"])

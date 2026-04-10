"""Connections service — OAuth flow and credential vending machine."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database import engine, Base
from app.routes.oauth import router as oauth_router
from app.routes.connections import router as conn_router
from app.routes.tools import router as tools_router
from app.routes.mcp import router as mcp_router

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

        # Add MCP-related columns to connections (Phase 09 + display_name)
        if inspector.has_table("connections"):
            columns = {c["name"] for c in inspector.get_columns("connections")}
            for col_name, col_type in [
                ("execution_type", "VARCHAR DEFAULT 'native'"),
                ("mcp_server_url", "TEXT"),
                ("mcp_tools_json", "TEXT"),
                ("display_name", "VARCHAR"),
            ]:
                if col_name not in columns:
                    sync_conn.execute(text(f"ALTER TABLE connections ADD COLUMN {col_name} {col_type}"))
                    logger.info("STARTUP_MIGRATION added connections.%s column", col_name)

        # Add persona_id to oauth_states (added for per-persona connections)
        if inspector.has_table("oauth_states"):
            columns = {c["name"] for c in inspector.get_columns("oauth_states")}
            if "persona_id" not in columns:
                sync_conn.execute(text("ALTER TABLE oauth_states ADD COLUMN persona_id VARCHAR"))
                logger.info("STARTUP_MIGRATION added oauth_states.persona_id column")
            else:
                logger.info("STARTUP_MIGRATION oauth_states.persona_id already exists — skipping")
            if "metadata" not in columns:
                sync_conn.execute(text("ALTER TABLE oauth_states ADD COLUMN metadata TEXT"))
                logger.info("STARTUP_MIGRATION added oauth_states.metadata column")
            else:
                logger.info("STARTUP_MIGRATION oauth_states.metadata already exists — skipping")
        else:
            logger.info("STARTUP_MIGRATION oauth_states table not found — create_all will handle it")
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
app.include_router(mcp_router, tags=["MCP"])

"""Connections service — OAuth flow and credential vending machine."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database import engine, Base
from app.routes.oauth import router as oauth_router
from app.routes.connections import router as conn_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Connections service started")
    yield
    await engine.dispose()


app = FastAPI(title="Connections Service", lifespan=lifespan)
app.include_router(oauth_router, tags=["OAuth"])
app.include_router(conn_router, tags=["Connections"])

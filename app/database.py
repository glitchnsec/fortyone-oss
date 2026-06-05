"""
Async SQLAlchemy engine and session factory.

Supports both PostgreSQL (asyncpg) and SQLite (aiosqlite) via URL scheme translation.
Use AsyncSessionLocal for production code; engine is exposed for Alembic env.py.
"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    url = settings.database_url
    # Translate sync URL schemes to async drivers
    if url == "sqlite://":
        url = "sqlite+aiosqlite://"
    elif url.startswith("sqlite:///"):
        url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    return create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,   # Verify connections before use (drops stale ones)
        pool_recycle=300,      # Recycle connections older than 5 minutes
    )


engine = _make_engine()
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# pgvector type registration is NOT needed when using SQLAlchemy ORM with the
# VECTOR column type. SQLAlchemy's bind_processor/result_processor handle
# text encoding/decoding end-to-end. Registering pgvector's asyncpg binary
# codec conflicts with SQLAlchemy's text codec, causing ValueError on queries.
# See: debug session vector-string-cast.md


async def get_db():
    """FastAPI dependency — yields an AsyncSession per request."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Verify the database connection and register ORM models.

    Migrations are run by the dedicated `migrate` Docker service (alembic upgrade head)
    before any app service starts. For in-memory SQLite (tests only), create_all is
    used instead since Alembic cannot track in-memory databases.
    """
    from app.memory import models  # noqa: F401 — registers User, Memory, Task, Message
    from app.models import auth  # noqa: F401 — registers UserSession (referenced by User.sessions relationship)

    settings = get_settings()

    if settings.database_url == "sqlite://":
        # In-memory SQLite (tests only) — Alembic can't run migrations against these
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

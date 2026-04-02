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
    return create_async_engine(url, echo=False)


engine = _make_engine()
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db():
    """FastAPI dependency — yields an AsyncSession per request."""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Run Alembic migrations to head. Falls back to create_all only for in-memory SQLite (tests)."""
    from app.memory import models  # noqa: F401 — registers User, Memory, Task, Message
    from app.models import auth  # noqa: F401 — registers UserSession (referenced by User.sessions relationship)
    settings = get_settings()

    if settings.database_url == "sqlite://":
        # In-memory SQLite (tests only) — Alembic can't track these
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return

    # Production/dev: use Alembic so migrations are tracked properly
    import subprocess
    try:
        subprocess.run(
            ["alembic", "upgrade", "head"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning("Alembic migration failed (%s), falling back to create_all", exc)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

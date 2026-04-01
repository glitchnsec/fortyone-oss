"""Async SQLAlchemy engine for the connections service."""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    url = get_settings().database_url
    if url.startswith("sqlite"):
        url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return create_async_engine(url, echo=False)


engine = _make_engine()
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

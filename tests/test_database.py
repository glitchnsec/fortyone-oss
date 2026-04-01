"""Tests for async database layer (app/database.py)."""
import pytest
from unittest.mock import patch, MagicMock


def test_url_translation_sqlite():
    with patch("app.config.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(database_url="sqlite:///./test.db")
        import importlib
        import app.database as db_module
        importlib.reload(db_module)
        # Engine URL should use aiosqlite
        assert "aiosqlite" in str(db_module.engine.url)


def test_url_translation_postgres():
    with patch("app.config.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(database_url="postgresql://user:pass@host/db")
        import importlib
        import app.database as db_module
        importlib.reload(db_module)
        assert "asyncpg" in str(db_module.engine.url)


def test_url_translation_postgres_short():
    with patch("app.config.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(database_url="postgres://user:pass@host/db")
        import importlib
        import app.database as db_module
        importlib.reload(db_module)
        assert "asyncpg" in str(db_module.engine.url)


@pytest.mark.asyncio
async def test_get_db_yields_async_session():
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.database import get_db
    gen = get_db()
    # get_db is an async generator
    import inspect
    assert inspect.isasyncgen(gen)


def test_init_db_is_coroutine():
    """init_db() must be an async coroutine (can be awaited)."""
    import inspect
    from app.database import init_db
    assert inspect.iscoroutinefunction(init_db)

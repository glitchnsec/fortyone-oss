"""
Integration test for Alembic migration 003.

Requires a live PostgreSQL + pgvector instance. Set TEST_DATABASE_URL to a
PostgreSQL URL (e.g. the docker-compose postgres service) to run these tests.

Skipped when TEST_DATABASE_URL is not set (SQLite dev environments).
"""
import os
import pytest
import subprocess
import sqlalchemy as sa

SKIP_REASON = "TEST_DATABASE_URL not set — skipping migration integration tests"


def _db_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def pg_engine():
    """Synchronous engine for schema inspection after migration."""
    url = _db_url()
    if not url:
        pytest.skip(SKIP_REASON)
    # Convert asyncpg URL to psycopg2-compatible for synchronous inspection
    sync_url = url.replace("postgresql+asyncpg://", "postgresql://")
    engine = sa.create_engine(sync_url)
    yield engine
    engine.dispose()


def _run_alembic(command: list[str]) -> subprocess.CompletedProcess:
    """Run alembic CLI with TEST_DATABASE_URL injected."""
    env = {**os.environ, "DATABASE_URL": _db_url()}
    return subprocess.run(
        ["python", "-m", "alembic"] + command,
        capture_output=True,
        text=True,
        env=env,
    )


def test_migration_003_upgrade_head(pg_engine):
    """Running alembic upgrade head creates all expected tables and columns."""
    result = _run_alembic(["upgrade", "head"])
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr}"

    inspector = sa.inspect(pg_engine)

    # personas table must exist
    assert inspector.has_table("personas"), "personas table not created"
    persona_cols = {c["name"] for c in inspector.get_columns("personas")}
    for col in ("id", "user_id", "name", "description", "tone_notes", "is_active", "created_at"):
        assert col in persona_cols, f"personas.{col} missing"

    # memories must have embedding and persona_tag columns
    memory_cols = {c["name"] for c in inspector.get_columns("memories")}
    assert "embedding" in memory_cols, "memories.embedding column missing"
    assert "persona_tag" in memory_cols, "memories.persona_tag column missing"

    # messages must have channel and persona_tag columns
    message_cols = {c["name"] for c in inspector.get_columns("messages")}
    assert "channel" in message_cols, "messages.channel column missing"
    assert "persona_tag" in message_cols, "messages.persona_tag column missing"


def test_migration_003_idempotent(pg_engine):
    """Running alembic upgrade head a second time does not error."""
    result = _run_alembic(["upgrade", "head"])
    assert result.returncode == 0, f"Second alembic upgrade head failed:\n{result.stderr}"


def test_migration_003_pgvector_extension(pg_engine):
    """pgvector extension must be installed in the database."""
    with pg_engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        ).fetchone()
    assert row is not None, "pgvector extension not installed"

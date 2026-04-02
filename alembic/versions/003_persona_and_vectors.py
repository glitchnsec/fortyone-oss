"""persona_and_vectors

Revision ID: 003_persona_and_vectors
Revises: 002_auth_columns
Create Date: 2026-04-02

Adds:
  - pgvector extension (server-side)
  - personas table (new)
  - memories.embedding VECTOR(1536) column
  - memories.persona_tag String column
  - messages.channel String column
  - messages.persona_tag String column

All operations are idempotent — safe to run against databases that already
have some columns (e.g. from a partial run).
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "003_persona_and_vectors"
down_revision: Union[str, Sequence[str], None] = "002_auth_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns(table)]
    return column in cols


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table_name)


def _is_postgres() -> bool:
    """Return True when running against PostgreSQL (not SQLite)."""
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # 1. Enable pgvector extension — must come BEFORE adding VECTOR column
    #    IF NOT EXISTS makes this safe to run multiple times
    #    Only runs on PostgreSQL — SQLite has no extension support
    if _is_postgres():
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. Add embedding column to memories (VECTOR(1536) for text-embedding-3-small)
    #    Only on PostgreSQL — SQLite does not support vector columns
    if _is_postgres() and not _column_exists("memories", "embedding"):
        op.execute(
            "ALTER TABLE memories ADD COLUMN embedding vector(1536)"
        )

    # 3. Add persona_tag column to memories
    if not _column_exists("memories", "persona_tag"):
        op.add_column(
            "memories",
            sa.Column("persona_tag", sa.String(), nullable=True),
        )

    # 4. Add channel column to messages (nullable, default "sms" for legacy rows)
    if not _column_exists("messages", "channel"):
        op.add_column(
            "messages",
            sa.Column("channel", sa.String(), nullable=True, server_default="sms"),
        )

    # 5. Add persona_tag column to messages — records active persona at send time.
    #    Plans 04 and 05 read this to determine last_persona for inheritance (per D-08).
    if not _column_exists("messages", "persona_tag"):
        op.add_column(
            "messages",
            sa.Column("persona_tag", sa.String(), nullable=True),
        )

    # 6. Create personas table
    if not _table_exists("personas"):
        op.create_table(
            "personas",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("tone_notes", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_personas_user_id", "personas", ["user_id"])

    # 7. HNSW index on memories.embedding for cosine similarity
    #    Only valid after the column exists — conditional on column presence
    #    (HNSW: no training step, works on empty table, O(log n) query)
    #    Only on PostgreSQL — SQLite has no HNSW index support
    if _is_postgres():
        bind = op.get_bind()
        indexes = [idx["name"] for idx in sa.inspect(bind).get_indexes("memories")]
        if "ix_memories_embedding_cosine" not in indexes:
            op.execute(
                """
                CREATE INDEX ix_memories_embedding_cosine
                ON memories
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
                """
            )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding_cosine")
    op.drop_index("ix_personas_user_id", table_name="personas")
    op.drop_table("personas")
    op.drop_column("messages", "persona_tag")
    op.drop_column("messages", "channel")
    op.drop_column("memories", "persona_tag")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS embedding")
    # NOTE: Do NOT drop the vector extension — other tables may use it

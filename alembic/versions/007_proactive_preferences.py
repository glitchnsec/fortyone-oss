"""proactive_preferences

Revision ID: 007_proactive_preferences
Revises: 006_admin_role
Create Date: 2026-04-07

Adds:
  - proactive_preferences table for per-user per-category settings
  - Unique index on (user_id, category_name)

All operations are idempotent -- safe to run against databases that already
have the table (e.g. from a partial run).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007_proactive_preferences"
down_revision: Union[str, Sequence[str], None] = "006_admin_role"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def _index_exists(name: str) -> bool:
    """Check whether an index with the given name exists."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name in inspector.get_table_names():
        for idx in inspector.get_indexes(table_name):
            if idx["name"] == name:
                return True
    return False


def upgrade() -> None:
    if not _table_exists("proactive_preferences"):
        op.create_table(
            "proactive_preferences",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("category_name", sa.String(), nullable=False),
            sa.Column("enabled", sa.Boolean(), server_default=sa.text("1")),
            sa.Column("window_start_hour", sa.Float(), nullable=True),
            sa.Column("window_end_hour", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not _index_exists("uq_proactive_pref_user_category"):
        op.create_index(
            "uq_proactive_pref_user_category",
            "proactive_preferences",
            ["user_id", "category_name"],
            unique=True,
        )


def downgrade() -> None:
    if _index_exists("uq_proactive_pref_user_category"):
        op.drop_index("uq_proactive_pref_user_category", table_name="proactive_preferences")
    if _table_exists("proactive_preferences"):
        op.drop_table("proactive_preferences")

"""admin_role

Revision ID: 006_admin_role
Revises: 005_agent_models
Create Date: 2026-04-07

Adds:
  - roles table with 'user' and 'admin' seed rows
  - users.role_id FK to roles
  - users.deleted_at (soft-delete)
  - users.suspended_at (suspension)
  - Indexes on created_at for users, messages, tasks (analytics queries)

All operations are idempotent -- safe to run against databases that already
have some tables/columns (e.g. from a partial run).
"""
from typing import Sequence, Union
import uuid

import sqlalchemy as sa
from alembic import op

revision: str = "006_admin_role"
down_revision: Union[str, Sequence[str], None] = "005_agent_models"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns(table)]
    return column in cols


def _index_exists(name: str) -> bool:
    """Check whether an index with the given name exists (PostgreSQL + SQLite)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # Iterate all tables and their indexes
    for table_name in inspector.get_table_names():
        for idx in inspector.get_indexes(table_name):
            if idx["name"] == name:
                return True
    return False


def upgrade() -> None:
    # ── roles table ─────────────────────────────────────────────────────
    if not _table_exists("roles"):
        op.create_table(
            "roles",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), unique=True, nullable=False),
        )

    # Seed user/admin rows if table is empty
    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT count(*) FROM roles")).scalar()
    if count == 0:
        user_role_id = str(uuid.uuid4())
        admin_role_id = str(uuid.uuid4())
        bind.execute(
            sa.text("INSERT INTO roles (id, name) VALUES (:id, :name)"),
            {"id": user_role_id, "name": "user"},
        )
        bind.execute(
            sa.text("INSERT INTO roles (id, name) VALUES (:id, :name)"),
            {"id": admin_role_id, "name": "admin"},
        )

    # ── users.role_id FK ────────────────────────────────────────────────
    if not _column_exists("users", "role_id"):
        op.add_column("users", sa.Column("role_id", sa.String(), nullable=True))
        # Set existing users to 'user' role
        bind.execute(sa.text(
            "UPDATE users SET role_id = (SELECT id FROM roles WHERE name = 'user') WHERE role_id IS NULL"
        ))

    # ── users.deleted_at ────────────────────────────────────────────────
    if not _column_exists("users", "deleted_at"):
        op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    # ── users.suspended_at ──────────────────────────────────────────────
    if not _column_exists("users", "suspended_at"):
        op.add_column("users", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))

    # ── Analytics indexes on created_at ─────────────────────────────────
    if not _index_exists("ix_users_created_at"):
        op.create_index("ix_users_created_at", "users", ["created_at"])

    if not _index_exists("ix_messages_created_at"):
        op.create_index("ix_messages_created_at", "messages", ["created_at"])

    if not _index_exists("ix_tasks_created_at"):
        op.create_index("ix_tasks_created_at", "tasks", ["created_at"])


def downgrade() -> None:
    for idx_name in ("ix_tasks_created_at", "ix_messages_created_at", "ix_users_created_at"):
        if _index_exists(idx_name):
            table = idx_name.replace("ix_", "").rsplit("_", 1)[0]
            op.drop_index(idx_name, table_name=table)

    if _column_exists("users", "suspended_at"):
        op.drop_column("users", "suspended_at")
    if _column_exists("users", "deleted_at"):
        op.drop_column("users", "deleted_at")
    if _column_exists("users", "role_id"):
        op.drop_column("users", "role_id")
    if _table_exists("roles"):
        op.drop_table("roles")

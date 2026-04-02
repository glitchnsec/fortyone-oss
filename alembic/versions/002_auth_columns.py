"""auth_columns

Revision ID: 002_auth_columns
Revises: 329b9515d7cf
Create Date: 2026-04-01

Adds email/password auth columns to users table and creates user_sessions table.
Migration is additive — email and password_hash are nullable for backward compat
(SMS-only users created before this migration have no email).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "002_auth_columns"
down_revision: Union[str, Sequence[str], None] = "329b9515d7cf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    """Check if a column already exists (handles brownfield databases)."""
    bind = op.get_bind()
    columns = [c["name"] for c in sa.inspect(bind).get_columns(table)]
    return column in columns


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table_name)


def upgrade() -> None:
    """Add auth columns to users and create user_sessions table — idempotent."""
    if not _column_exists("users", "email"):
        op.add_column("users", sa.Column("email", sa.String(), nullable=True))
        op.create_index("ix_users_email", "users", ["email"], unique=True)

    if not _column_exists("users", "password_hash"):
        op.add_column("users", sa.Column("password_hash", sa.String(), nullable=True))

    if not _column_exists("users", "phone_verified"):
        op.add_column(
            "users",
            sa.Column("phone_verified", sa.Boolean(), server_default="false", nullable=False),
        )

    if not _column_exists("users", "assistant_name"):
        op.add_column("users", sa.Column("assistant_name", sa.String(), nullable=True))

    if not _table_exists("user_sessions"):
        op.create_table(
            "user_sessions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("token_hash", sa.String(), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])


def downgrade() -> None:
    """Reverse auth columns and drop user_sessions table."""
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_column("users", "email")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "phone_verified")
    op.drop_column("users", "assistant_name")

"""slack_user_id

Revision ID: 009_slack_user_id
Revises: 008_custom_agents
Create Date: 2026-04-08

Adds:
  - slack_user_id column (nullable, unique, indexed) to users table
  - pending_slack_link column (nullable text) to users table

All operations are idempotent -- safe to run against databases that already
have the columns (e.g. from a partial run).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009_slack_user_id"
down_revision: Union[str, Sequence[str], None] = "008_custom_agents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    columns = [c["name"] for c in sa.inspect(bind).get_columns(table)]
    return column in columns


def upgrade() -> None:
    if not _column_exists("users", "slack_user_id"):
        op.add_column("users", sa.Column("slack_user_id", sa.String(), nullable=True))
        op.create_unique_constraint("uq_users_slack_user_id", "users", ["slack_user_id"])
        op.create_index("ix_users_slack_user_id", "users", ["slack_user_id"])

    if not _column_exists("users", "pending_slack_link"):
        op.add_column("users", sa.Column("pending_slack_link", sa.Text(), nullable=True))


def downgrade() -> None:
    if _column_exists("users", "pending_slack_link"):
        op.drop_column("users", "pending_slack_link")

    if _column_exists("users", "slack_user_id"):
        op.drop_index("ix_users_slack_user_id", table_name="users")
        op.drop_constraint("uq_users_slack_user_id", "users", type_="unique")
        op.drop_column("users", "slack_user_id")

"""Add archived_at to tasks + feature_milestones table.

Revision ID: 010_task_archive_milestones
Revises: 009_slack_user_id
Create Date: 2026-04-09

Adds:
  - archived_at column (nullable DateTime) to tasks table
  - follow_up_sent_at column (nullable DateTime) to tasks table
  - feature_milestones table for tracking feature discovery (D-07)

All operations are idempotent -- safe to run against databases that already
have the columns/table (e.g. from a partial run).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010_task_archive_milestones"
down_revision: Union[str, Sequence[str], None] = "009_slack_user_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table: str) -> bool:
    bind = op.get_bind()
    return table in sa.inspect(bind).get_table_names()


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    columns = [c["name"] for c in sa.inspect(bind).get_columns(table)]
    return column in columns


def upgrade() -> None:
    # 1. Add archived_at to tasks table
    if not _column_exists("tasks", "archived_at"):
        op.add_column("tasks", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))

    # 2. Add follow_up_sent_at to tasks table (for D-15 remind task lifecycle)
    if not _column_exists("tasks", "follow_up_sent_at"):
        op.add_column("tasks", sa.Column("follow_up_sent_at", sa.DateTime(timezone=True), nullable=True))

    # 3. Create feature_milestones table
    if not _table_exists("feature_milestones"):
        op.create_table(
            "feature_milestones",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("milestone_name", sa.String(), nullable=False),
            sa.Column("achieved_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("user_id", "milestone_name", name="uq_user_milestone"),
        )


def downgrade() -> None:
    if _table_exists("feature_milestones"):
        op.drop_table("feature_milestones")
    if _column_exists("tasks", "follow_up_sent_at"):
        op.drop_column("tasks", "follow_up_sent_at")
    if _column_exists("tasks", "archived_at"):
        op.drop_column("tasks", "archived_at")

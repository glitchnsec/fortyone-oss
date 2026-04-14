"""Add welcome_sms_sent column to users table.

Revision ID: 014_welcome_sms_sent
Revises: 013_msg_meta_task_sessions
Create Date: 2026-04-11

Tracks whether the one-time welcome SMS was sent after onboarding step 3
(assistant name configuration). Prevents duplicate welcome messages.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "014_welcome_sms_sent"
down_revision: Union[str, None] = "013_msg_meta_task_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    from alembic import op
    ctx = op.get_context()
    insp = sa.inspect(ctx.connection)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def upgrade() -> None:
    if not _column_exists("users", "welcome_sms_sent"):
        op.add_column("users", sa.Column("welcome_sms_sent", sa.Boolean(), server_default="0", nullable=True))


def downgrade() -> None:
    if _column_exists("users", "welcome_sms_sent"):
        op.drop_column("users", "welcome_sms_sent")

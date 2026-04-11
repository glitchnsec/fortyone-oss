"""Add metadata_json to messages table + task_sessions table.

Revision ID: 013_msg_meta_task_sessions
Revises: 012_oauth_state_metadata
Create Date: 2026-04-11

CONV-01: Store tool_calls + tool_results as metadata on outbound messages.
CONV-03/D-11: Persistent task sessions for multi-turn tool-calling workflows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '013_msg_meta_task_sessions'
down_revision: Union[str, Sequence[str], None] = '012_oauth_state_metadata'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(conn, table, column):
    insp = sa.inspect(conn)
    return any(c["name"] == column for c in insp.get_columns(table))


def _table_exists(conn, table):
    insp = sa.inspect(conn)
    return table in insp.get_table_names()


def upgrade() -> None:
    conn = op.get_bind()

    # Add metadata_json to messages (D-01: tool_calls + tool_results JSON)
    if _column_exists(conn, "messages", "metadata_json"):
        pass  # Already exists — idempotent
    else:
        op.add_column("messages", sa.Column("metadata_json", sa.Text(), nullable=True))

    # Create task_sessions table (D-11: persistent multi-turn task sessions)
    if not _table_exists(conn, "task_sessions"):
        op.create_table(
            "task_sessions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("session_id", sa.String(), nullable=False, unique=True, index=True),
            sa.Column("original_intent", sa.Text(), nullable=False),
            sa.Column("gathered_context", sa.Text(), nullable=True),
            sa.Column("pending_action", sa.Text(), nullable=True),
            sa.Column("tools_called", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), server_default="in_progress"),
            sa.Column("channel", sa.String(), nullable=True),
            sa.Column("persona_tag", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("last_active", sa.DateTime(timezone=True)),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "task_sessions"):
        op.drop_table("task_sessions")
    if _column_exists(conn, "messages", "metadata_json"):
        op.drop_column("messages", "metadata_json")

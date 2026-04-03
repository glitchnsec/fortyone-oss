"""agent_models

Revision ID: 005_agent_models
Revises: 004_personality_notes
Create Date: 2026-04-03

Adds:
  - goals table (user goal tracking with OKR/SMART/custom frameworks)
  - action_log table (audit trail for proactive agent actions)
  - pending_actions table (actions awaiting user confirmation)
  - user_profiles table (structured TELOS profile entries)
  - users.proactive_settings_json column

All operations are idempotent — safe to run against databases that already
have some tables/columns (e.g. from a partial run).
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "005_agent_models"
down_revision: Union[str, Sequence[str], None] = "004_personality_notes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns(table)]
    return column in cols


def upgrade() -> None:
    # ── goals ────────────────────────────────────────────────────────────
    if not _table_exists("goals"):
        op.create_table(
            "goals",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("persona_id", sa.String(), sa.ForeignKey("personas.id"), nullable=True),
            sa.Column("framework", sa.String(), nullable=False, server_default="custom"),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("target_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(), server_default="active"),
            sa.Column("parent_goal_id", sa.String(), sa.ForeignKey("goals.id"), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("version", sa.Integer(), server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    # ── action_log ───────────────────────────────────────────────────────
    if not _table_exists("action_log"):
        op.create_table(
            "action_log",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("action_type", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("outcome", sa.String(), nullable=True),
            sa.Column("trigger", sa.String(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )

    # ── pending_actions ──────────────────────────────────────────────────
    if not _table_exists("pending_actions"):
        op.create_table(
            "pending_actions",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("action_type", sa.String(), nullable=False),
            sa.Column("action_params_json", sa.Text(), nullable=False),
            sa.Column("risk_level", sa.String(), nullable=False),
            sa.Column("status", sa.String(), server_default="pending"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )

    # ── user_profiles ────────────────────────────────────────────────────
    if not _table_exists("user_profiles"):
        op.create_table(
            "user_profiles",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("section", sa.String(), nullable=False),
            sa.Column("label", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("persona_id", sa.String(), sa.ForeignKey("personas.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    # ── users.proactive_settings_json ────────────────────────────────────
    if not _column_exists("users", "proactive_settings_json"):
        op.add_column("users", sa.Column("proactive_settings_json", sa.Text(), nullable=True))


def downgrade() -> None:
    if _column_exists("users", "proactive_settings_json"):
        op.drop_column("users", "proactive_settings_json")
    if _table_exists("user_profiles"):
        op.drop_table("user_profiles")
    if _table_exists("pending_actions"):
        op.drop_table("pending_actions")
    if _table_exists("action_log"):
        op.drop_table("action_log")
    if _table_exists("goals"):
        op.drop_table("goals")

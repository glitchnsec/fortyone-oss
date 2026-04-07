"""custom_agents

Revision ID: 008_custom_agents
Revises: 007_proactive_preferences
Create Date: 2026-04-07

Adds:
  - custom_agents table for user-defined webhook, prompt, and yaml_script agents

All operations are idempotent -- safe to run against databases that already
have the table (e.g. from a partial run).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008_custom_agents"
down_revision: Union[str, Sequence[str], None] = "007_proactive_preferences"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    if not _table_exists("custom_agents"):
        op.create_table(
            "custom_agents",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("agent_type", sa.String(), nullable=False),
            sa.Column("config_json", sa.Text(), nullable=False),
            sa.Column("parameters_schema_json", sa.Text(), nullable=True),
            sa.Column("risk_level", sa.String(), server_default=sa.text("'low'")),
            sa.Column("enabled", sa.Boolean(), server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if _table_exists("custom_agents"):
        op.drop_table("custom_agents")

"""Seed default proactive preferences for existing users.

Revision ID: 011_default_proactive_prefs
Revises: 010_task_archive_milestones
Create Date: 2026-04-09

For existing users who have no ProactivePreference rows, insert enabled=False
rows for all categories EXCEPT profile_nudge and feature_discovery (which
default to enabled). Users who already have preference rows are untouched.

Idempotent — safe to run multiple times. Only inserts where no row exists.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone
import uuid


# revision identifiers
revision: str = "011_default_proactive_prefs"
down_revision: str = "010_task_archive_milestones"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Categories that should default to DISABLED for all users
DISABLED_BY_DEFAULT = [
    "morning_briefing",
    "evening_recap",
    "weekly_digest",
    "goal_coaching",
    "day_checkin",
    "insight_observation",
    "afternoon_followup",
]


def upgrade() -> None:
    conn = op.get_bind()

    # Check tables exist
    insp = sa.inspect(conn)
    if "users" not in insp.get_table_names():
        return
    if "proactive_preferences" not in insp.get_table_names():
        return

    # Get all user IDs
    users = conn.execute(sa.text("SELECT id FROM users")).fetchall()
    now = datetime.now(timezone.utc)

    for (user_id,) in users:
        for category in DISABLED_BY_DEFAULT:
            exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM proactive_preferences "
                    "WHERE user_id = :uid AND category_name = :cat LIMIT 1"
                ),
                {"uid": user_id, "cat": category},
            ).fetchone()

            if exists:
                # Update existing row to disabled
                conn.execute(
                    sa.text(
                        "UPDATE proactive_preferences SET enabled = :enabled, updated_at = :updated "
                        "WHERE user_id = :uid AND category_name = :cat"
                    ),
                    {"enabled": False, "updated": now, "uid": user_id, "cat": category},
                )
            else:
                # Insert new disabled row
                conn.execute(
                    sa.text(
                        "INSERT INTO proactive_preferences "
                        "(id, user_id, category_name, enabled, created_at, updated_at) "
                        "VALUES (:id, :uid, :cat, :enabled, :created, :updated)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "uid": user_id,
                        "cat": category,
                        "enabled": False,
                        "created": now,
                        "updated": now,
                    },
                )


def downgrade() -> None:
    # Revert: set disabled-by-default categories back to enabled
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    for category in DISABLED_BY_DEFAULT:
        conn.execute(
            sa.text(
                "UPDATE proactive_preferences "
                "SET enabled = :enabled, updated_at = :updated "
                "WHERE category_name = :cat AND enabled = :disabled"
            ),
            {"enabled": True, "disabled": False, "updated": now, "cat": category},
        )

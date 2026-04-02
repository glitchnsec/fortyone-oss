"""personality_notes

Revision ID: 004_personality_notes
Revises: 003_persona_and_vectors
Create Date: 2026-04-02

Adds:
  - users.personality_notes Text column (free-text personality/tone instructions)

Idempotent: checks column existence before adding.
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "004_personality_notes"
down_revision: Union[str, Sequence[str], None] = "003_persona_and_vectors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns(table)]
    return column in cols


def upgrade() -> None:
    if not _column_exists("users", "personality_notes"):
        op.add_column("users", sa.Column("personality_notes", sa.Text(), nullable=True))


def downgrade() -> None:
    if _column_exists("users", "personality_notes"):
        op.drop_column("users", "personality_notes")

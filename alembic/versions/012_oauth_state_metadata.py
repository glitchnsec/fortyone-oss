"""oauth_state_metadata

Revision ID: 012_oauth_state_metadata
Revises: 011_default_proactive_prefs
Create Date: 2026-04-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '012_oauth_state_metadata'
down_revision: Union[str, Sequence[str], None] = '011_default_proactive_prefs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('oauth_states'):
        return
    columns = {c['name'] for c in inspector.get_columns('oauth_states')}
    if 'metadata' not in columns:
        op.add_column('oauth_states', sa.Column('metadata', sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table('oauth_states'):
        return
    columns = {c['name'] for c in inspector.get_columns('oauth_states')}
    if 'metadata' in columns:
        op.drop_column('oauth_states', 'metadata')

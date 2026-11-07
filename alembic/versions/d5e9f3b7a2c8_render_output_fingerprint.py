"""render output fingerprint

Revision ID: d5e9f3b7a2c8
Revises: c4d8e2a6f1b7
Create Date: 2026-11-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e9f3b7a2c8'
down_revision: Union[str, Sequence[str], None] = 'c4d8e2a6f1b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('render_outputs', sa.Column('fingerprint', sa.String(length=32), nullable=True))
    op.create_index(op.f('ix_render_outputs_fingerprint'), 'render_outputs', ['fingerprint'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_render_outputs_fingerprint'), table_name='render_outputs')
    op.drop_column('render_outputs', 'fingerprint')

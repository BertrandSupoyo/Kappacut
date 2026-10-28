"""job worker_id

Revision ID: 9a3f5c7e1d24
Revises: 7c41e9a2b5d3
Create Date: 2026-10-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a3f5c7e1d24'
down_revision: Union[str, Sequence[str], None] = '7c41e9a2b5d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('jobs', sa.Column('worker_id', sa.String(length=96), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('jobs', 'worker_id')

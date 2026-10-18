"""project clip_count + content_type

Revision ID: 7c41e9a2b5d3
Revises: 2f820a438654
Create Date: 2026-10-18 03:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c41e9a2b5d3'
down_revision: Union[str, Sequence[str], None] = '2f820a438654'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects', sa.Column('clip_count', sa.Integer(), nullable=True))
    op.add_column('projects', sa.Column('content_type', sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'content_type')
    op.drop_column('projects', 'clip_count')

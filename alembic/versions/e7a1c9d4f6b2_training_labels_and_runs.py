"""training labels and runs

Revision ID: e7a1c9d4f6b2
Revises: d5e9f3b7a2c8
Create Date: 2026-11-04 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e7a1c9d4f6b2'
down_revision: Union[str, Sequence[str], None] = 'd5e9f3b7a2c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'video_labels',
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('moments', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('source_bytes', sa.BigInteger(), nullable=True),
        sa.Column('done', sa.Boolean(), nullable=False),
        sa.Column('blind', sa.Boolean(), nullable=False),
        sa.Column('revealed', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('project_id'),
    )
    op.create_table(
        'training_runs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('score', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('training_runs')
    op.drop_table('video_labels')

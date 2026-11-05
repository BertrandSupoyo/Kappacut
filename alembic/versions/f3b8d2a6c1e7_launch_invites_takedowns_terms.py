"""launch: invites, takedowns, terms acceptance, job error detail

Revision ID: f3b8d2a6c1e7
Revises: e7a1c9d4f6b2
Create Date: 2026-11-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f3b8d2a6c1e7'
down_revision: Union[str, Sequence[str], None] = 'e7a1c9d4f6b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('terms_accepted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('terms_version', sa.String(length=16), nullable=True))
    op.add_column('jobs', sa.Column('error_detail', sa.Text(), nullable=True))
    op.create_table(
        'invites',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('email', postgresql.CITEXT(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )
    op.create_table(
        'takedowns',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=True),
        sa.Column('user_email', postgresql.CITEXT(), nullable=False),
        sa.Column('project_name', sa.String(length=255), nullable=False),
        sa.Column('reference', sa.Text(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_takedowns_user_email'), 'takedowns', ['user_email'], unique=False)
    op.create_index(op.f('ix_takedowns_user_id'), 'takedowns', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_takedowns_user_id'), table_name='takedowns')
    op.drop_index(op.f('ix_takedowns_user_email'), table_name='takedowns')
    op.drop_table('takedowns')
    op.drop_table('invites')
    op.drop_column('jobs', 'error_detail')
    op.drop_column('users', 'terms_version')
    op.drop_column('users', 'terms_accepted_at')

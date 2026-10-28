"""render output: one row per file

A re-render writes the same object key again but added a second row, so the storage quota
counted the file's bytes twice. Drop the stale duplicates (keeping the newest row, which
describes the object as it is now), then make it a constraint.

Revision ID: c4d8e2a6f1b7
Revises: 9a3f5c7e1d24
Create Date: 2026-10-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4d8e2a6f1b7'
down_revision: Union[str, Sequence[str], None] = '9a3f5c7e1d24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "DELETE FROM render_outputs a USING render_outputs b "
        "WHERE a.project_id = b.project_id AND a.filename = b.filename "
        "AND (a.created_at, a.id) < (b.created_at, b.id)"
    )
    op.create_unique_constraint(
        'uq_render_outputs_project_filename', 'render_outputs', ['project_id', 'filename']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_render_outputs_project_filename', 'render_outputs', type_='unique')

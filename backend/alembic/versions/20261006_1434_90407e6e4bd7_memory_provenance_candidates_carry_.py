"""memory provenance: candidates carry where they came from

Revision ID: 90407e6e4bd7
Revises: f2957e9513b4
Create Date: 2026-10-06 14:34:48.158183

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '90407e6e4bd7'
down_revision: str | None = 'f2957e9513b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A server default is required, not cosmetic: the table already has rows, and
    # `ADD COLUMN ... NOT NULL` without one fails on every existing candidate.
    # The default is then dropped so the column's shape matches the model, where
    # the value is supplied by the application rather than by the database.
    op.add_column(
        'memory_candidates',
        sa.Column(
            'structured',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column('memory_candidates', 'structured', server_default=None)


def downgrade() -> None:
    op.drop_column('memory_candidates', 'structured')

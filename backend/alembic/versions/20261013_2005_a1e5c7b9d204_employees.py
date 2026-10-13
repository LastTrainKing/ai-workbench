"""employees: the row that makes a persona a thing the user owns

Revision ID: a1e5c7b9d204
Revises: 3f1c9d7a52be
Create Date: 2026-10-13 20:05:00.000000

## Why a table

Every other entity in this product already has one — a project, a task, a memory, a
deliverable. The persona was the only concept that lived as text: a system prompt assembled
per request. Text cannot be granted a permission, cannot own a deliverable, cannot be
paused, and cannot be told apart from a different persona in the run history. The brief
requires all four.

## Why the scope fields are JSONB and not join tables

``knowledge_scope``, ``memory_scope``, ``permission_profile`` and ``model_policy`` are small
documents that are always read whole, never queried by element, and replaced as a unit when
the employee is edited. Four join tables would add four migrations and buy no query anyone
wants. The one list that *is* queried — the roster by status — has its own index.

## Idempotent, like the rest of this chain

``employees`` does not exist on any real database yet, but the migrations in this repository
are written to be re-runnable: every one checks the catalogue before acting. That is not
defensive habit — the desktop launcher runs ``alembic upgrade head`` on every start, against
databases at unknown revisions, and a migration that assumed its own starting state once
left a user's install unable to boot. ``downgrade`` drops the table, because unlike the
audit tables this one owns no history: the deliverables and runs that reference an employee
reference it by id, and dropping the row they point at is the documented consequence of
rolling this feature back.

## The unique constraint is the product decision

``uq_employees_user_id_name`` means a name refers to exactly one employee. "让研究员看看这份
材料" has to be unambiguous, and the service turns the violation into a sentence rather than
letting it surface as a driver error.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1e5c7b9d204"
down_revision: str | None = "3f1c9d7a52be"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "employees"

_STATUSES = "'idle','working','waiting_approval','waiting_user','paused','failed','archived'"


def _columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("role_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("avatar", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("mission", sa.Text(), nullable=False, server_default=""),
        sa.Column("persona", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "responsibilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "out_of_scope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("service_scope", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "knowledge_scope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "memory_scope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "permission_profile",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "skills",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "model_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="idle"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_from_template", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE in set(inspector.get_table_names()):
        # Already there. A previous run of this migration, or a database restored from a
        # backup taken after it. Either way there is nothing to do and nothing to guess.
        return

    op.create_table(_TABLE, *_columns())
    op.create_check_constraint(
        "employees_status_valid", _TABLE, f"status IN ({_STATUSES})"
    )
    op.create_unique_constraint("uq_employees_user_id_name", _TABLE, ["user_id", "name"])
    op.create_index("ix_employees_user_id", _TABLE, ["user_id"])
    op.create_index("ix_employees_status", _TABLE, ["status"])
    op.create_index("ix_employees_created_at", _TABLE, ["created_at"])
    op.create_index("ix_employees_user_id_archived", _TABLE, ["user_id", "archived"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    for index in (
        "ix_employees_user_id_archived",
        "ix_employees_created_at",
        "ix_employees_status",
        "ix_employees_user_id",
    ):
        op.drop_index(index, table_name=_TABLE)
    op.drop_table(_TABLE)

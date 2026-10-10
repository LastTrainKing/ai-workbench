"""tool call reconciliation: enough context to check what an invocation did

Revision ID: 3f1c9d7a52be
Revises: 6a1884572148
Create Date: 2026-10-10 09:10:00.000000

## Why these two columns exist

The previous migration made an interrupted invocation *visible* — a `RUNNING` row that
recovery turns into `UNKNOWN` instead of silence. Visible is not the same as resolvable.
`UNKNOWN` says "nobody knows"; the next question is "can anyone find out", and for that the
record has to carry enough to go and look.

`working_directory` is the missing half of the arguments. `file_write("out.txt", ...)`
names a different file in every run, and reconciliation happens in a **later process**, with
no run in flight and no context to ask. Resolving `out.txt` against the wrong directory
would not just fail to check the effect — it would check a different file and report a
confident answer about it. Recorded at intent time, because that is the only moment the
answer exists.

`reconciled_at` separates two different things that were both "when it ended". `finished_at`
is when the tool's own process observed the outcome. `reconciled_at` is when somebody else
went and looked. A row can legitimately have the second without the first — that is exactly
the `UNKNOWN` that was resolved afterwards — and a reader who saw only `finished_at` would
conclude the invocation had been observed to finish when it never was.

## Idempotent on purpose

Same reasoning as the previous migrations: `tool_calls` exists on every real database, so
each column is added **only if absent**. `downgrade` drops only these two — dropping the
table would take the audit trail with it, which is the one thing this whole line of work
exists to prevent.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f1c9d7a52be"
down_revision: str | None = "6a1884572148"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tool_calls"
_NEW_COLUMNS: dict[str, sa.Column] = {
    "working_directory": sa.Column("working_directory", sa.String(length=1024), nullable=True),
    "reconciled_at": sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        raise RuntimeError(
            f"表 {_TABLE!r} 不存在；这不是本迁移能猜测修复的状态。"
            "请先用之前的迁移建立 schema。"
        )
    existing = {c["name"] for c in inspector.get_columns(_TABLE)}
    for name, column in _NEW_COLUMNS.items():
        if name not in existing:
            op.add_column(_TABLE, column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    existing = {c["name"] for c in inspector.get_columns(_TABLE)}
    for name in reversed(list(_NEW_COLUMNS)):
        if name in existing:
            op.drop_column(_TABLE, name)

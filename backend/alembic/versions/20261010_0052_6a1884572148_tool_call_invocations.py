"""tool call invocations: a record that survives the crash it happens in

Revision ID: 6a1884572148
Revises: 6068bb13c474
Create Date: 2026-10-10 00:52:00.000000

## Why these columns exist

`tool_calls` rows were written **after** the tool returned. So the window between a tool
starting and its outcome being recorded left no row at all. A process killed inside that
window produced an agent run with no evidence it had ever called anything — and re-running
the goal would repeat the tool, because nothing said it had already run.

Four columns close the window:

    invocation_id   minted before the tool runs, so recovery can name the invocation
    started_at      when the tool was handed to the executor
    finished_at     when its outcome was written — null means the process did not get there
    mutates_state   whether re-running could repeat a side effect

`invocation_id` is unique and **nullable**: rows written before this migration have no id,
and PostgreSQL allows any number of NULLs in a unique index, so they coexist rather than
colliding. New rows always carry one.

## Idempotent on purpose

Same reasoning as the previous migrations: the table already exists on every real database,
so the columns are added **only if absent** and an existing column is left alone.
`downgrade` drops only the columns this migration added — dropping the table would take the
audit trail with it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6a1884572148"
down_revision: str | None = "6068bb13c474"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tool_calls"
_NEW_COLUMNS: dict[str, sa.Column] = {
    "invocation_id": sa.Column("invocation_id", sa.String(length=64), nullable=True),
    "started_at": sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    "finished_at": sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    "mutates_state": sa.Column(
        "mutates_state", sa.Boolean(), nullable=False, server_default=sa.false()
    ),
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

    indexes = {i["name"] for i in inspector.get_indexes(_TABLE)}
    constraints = {c["name"] for c in inspector.get_unique_constraints(_TABLE)}
    if "ix_tool_calls_invocation_id" not in indexes and "uq_tool_calls_invocation_id" not in constraints:
        op.create_index(
            "ix_tool_calls_invocation_id", _TABLE, ["invocation_id"], unique=True
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    existing = {c["name"] for c in inspector.get_columns(_TABLE)}
    indexes = {i["name"] for i in inspector.get_indexes(_TABLE)}
    if "ix_tool_calls_invocation_id" in indexes:
        op.drop_index("ix_tool_calls_invocation_id", table_name=_TABLE)
    for name in reversed(list(_NEW_COLUMNS)):
        if name in existing:
            op.drop_column(_TABLE, name)

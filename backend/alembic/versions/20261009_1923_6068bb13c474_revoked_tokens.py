"""revoked tokens, recorded durably

Revision ID: 6068bb13c474
Revises: 81cce5214260
Create Date: 2026-10-09 19:23:00.000000

## Why this table exists

Logout used to write the token's `jti` to Redis and nothing else. `is_token_revoked`
read Redis, and when Redis was unreachable the read returned `None`, which the caller
interpreted as "not revoked". A logout therefore stopped working for as long as the cache
was down — the token stayed usable for the rest of its 24 hours, and nothing said so.

A revocation list whose only copy is a cache is not a revocation list. This table is the
authority; Redis stays in front of it as a fast path. `is_token_revoked` now answers
`revoked` / `not_revoked` / `storage_unavailable`, and the request is refused in the third
case rather than allowed.

## Idempotent on purpose

The same reasoning as the previous migration: an existing database may already have this
table if it was ever created by `create_all`, so the table is created **only if absent**
and an existing one is checked against the columns this migration would have created.
`downgrade` drops it, and refuses if it holds rows — deleting the record of who logged
out is a security decision, not a rollback.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6068bb13c474"
down_revision: str | None = "81cce5214260"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "revoked_tokens"
_COLUMNS = ("id", "jti", "subject", "expires_at", "revoked_at")


def _create_if_absent() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    present = set(inspector.get_table_names())

    if _TABLE in present:
        existing = {c["name"] for c in inspector.get_columns(_TABLE)}
        missing = [c for c in _COLUMNS if c not in existing]
        if missing:
            raise RuntimeError(
                f"表 {_TABLE!r} 已存在但缺少列 {missing}；"
                "这不是本迁移能猜测修复的状态，请先人工核对。"
            )
        return

    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("jti", name="uq_revoked_tokens_jti"),
    )
    op.create_index("ix_revoked_tokens_jti", _TABLE, ["jti"], unique=True)
    op.create_index("ix_revoked_tokens_expires_at", _TABLE, ["expires_at"])


def upgrade() -> None:
    _create_if_absent()


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in set(inspector.get_table_names()):
        return
    rows = bind.execute(sa.text(f'select count(*) from "{_TABLE}"')).scalar_one()
    if rows:
        raise RuntimeError(
            f"拒绝降级：表 {_TABLE!r} 中有 {rows} 行撤销记录。"
            "删除它们会让已登出的 Token 重新可用，这是安全决策，不是回滚；"
            "如确需删除，请先备份并人工执行。"
        )
    op.drop_table(_TABLE)

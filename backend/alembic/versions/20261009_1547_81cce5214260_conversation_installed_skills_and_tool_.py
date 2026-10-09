"""conversation, installed skills and tool audit tables

Revision ID: 81cce5214260
Revises: 90407e6e4bd7
Create Date: 2026-10-09 15:47:11.492183

## Why this migration exists, and why it is not a plain `create_table`

These four tables — ``conversations``, ``messages``, ``installed_skills``,
``tool_audit_log`` — were only ever created by ``Base.metadata.create_all`` at startup.
No migration mentioned them. Measured on a database built from migrations alone:

    model tables 33, alembic-created 29
    missing: ['conversations', 'installed_skills', 'messages', 'tool_audit_log']

That is the drift: the existing database's ``alembic_version`` reads the old head and
*looks* up to date, while four of its tables exist outside the migration history. Rebuild
from migrations and you lose them; that is not a state anyone can call "up to date".

## The compatibility problem

The live database already has all four tables, with real rows (``tool_audit_log`` had
3,021). A migration that runs ``create_table`` unconditionally fails on it. Stamping the
version without doing anything is the other wrong answer — it records "done" without
checking whether the schema actually agrees.

So each table is created **only if it is absent**, and an existing table is checked
against the columns this migration would have created. A table that exists but is missing
a column is drift the migration cannot fix by guessing, and it raises with the table and
the missing columns named. That is a real check, not a stamp.

`downgrade` refuses to drop a table that holds rows. The four tables carry user
conversations, installed skills and the audit trail, so a downgrade on a live database
would be a data-loss operation wearing the clothes of a rollback.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '81cce5214260'
down_revision: str | None = '90407e6e4bd7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector():  # noqa: ANN202 - alembic's Inspector type is not exported cleanly
    return sa.inspect(op.get_bind())


def _create_table_if_absent(name: str, columns: list[str], create) -> None:  # noqa: ANN001
    """Create ``name``, or verify an existing one carries ``columns``.

    `columns` is the list of column names this migration would create. An existing table
    missing one of them is a schema the migration cannot reason about, and the error says
    which table and which columns — a message that can be acted on, unlike a raw
    ``DuplicateTable``.
    """
    inspector = _inspector()
    if name not in inspector.get_table_names():
        create()
        return

    present = {column["name"] for column in inspector.get_columns(name)}
    missing = [column for column in columns if column not in present]
    if missing:
        raise RuntimeError(
            f"表 {name!r} 已存在但缺少列 {missing}。"
            "这表示数据库结构与迁移历史不一致，迁移无法安全猜测如何处理；"
            "请先人工核对，不要直接 stamp。"
        )


def _create_index_if_absent(name: str, table: str, columns: list[str], *, unique: bool = False) -> None:
    inspector = _inspector()
    existing = {index["name"] for index in inspector.get_indexes(table)}
    if name in existing:
        return
    op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    _create_table_if_absent(
        "conversations",
        ["user_id", "title", "id", "created_at", "updated_at"],
        lambda: op.create_table(
            "conversations",
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("title", sa.String(length=120), nullable=False),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_conversations_user_id_users"), ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        ),
    )
    _create_index_if_absent(op.f("ix_conversations_created_at"), "conversations", ["created_at"])
    _create_index_if_absent(op.f("ix_conversations_user_id"), "conversations", ["user_id"])
    _create_index_if_absent("ix_conversations_user_updated", "conversations", ["user_id", "updated_at"])

    _create_table_if_absent(
        "installed_skills",
        [
            "user_id", "name", "description", "when_to_use", "body", "skill_type", "scripts",
            "params", "rendered_body", "source", "source_commit", "license", "version",
            "status", "verified_at", "verification", "use_count", "success_count",
            "last_used_at", "rating", "review_note", "enabled", "id", "created_at", "updated_at",
        ],
        lambda: op.create_table(
            "installed_skills",
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("when_to_use", sa.Text(), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("skill_type", sa.String(length=16), nullable=False),
            sa.Column("scripts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("rendered_body", sa.Text(), nullable=False),
            sa.Column("source", sa.String(length=400), nullable=False),
            sa.Column("source_commit", sa.String(length=80), nullable=False),
            sa.Column("license", sa.String(length=80), nullable=False),
            sa.Column("version", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("verified_at", sa.String(length=40), nullable=False),
            sa.Column("verification", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("use_count", sa.Integer(), nullable=False),
            sa.Column("success_count", sa.Integer(), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rating", sa.Float(), nullable=True),
            sa.Column("review_note", sa.Text(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_installed_skills_user_id_users"), ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_installed_skills")),
        ),
    )
    _create_index_if_absent(op.f("ix_installed_skills_created_at"), "installed_skills", ["created_at"])
    _create_index_if_absent(op.f("ix_installed_skills_user_id"), "installed_skills", ["user_id"])
    _create_index_if_absent("ix_skill_user_name", "installed_skills", ["user_id", "name"], unique=True)
    _create_index_if_absent("ix_skill_user_status", "installed_skills", ["user_id", "status"])

    _create_table_if_absent(
        "tool_audit_log",
        [
            "ai_run_id", "step_index", "tool_name", "category", "decision", "reason",
            "risk_level", "permission_level", "target", "arguments", "status", "exit_code",
            "output_bytes", "truncated", "error_message", "latency_ms", "confirmed_by_user",
            "created_at", "id",
        ],
        lambda: op.create_table(
            "tool_audit_log",
            sa.Column("ai_run_id", sa.Uuid(), nullable=True),
            sa.Column("step_index", sa.Integer(), nullable=False),
            sa.Column("tool_name", sa.String(length=64), nullable=False),
            sa.Column("category", sa.String(length=32), nullable=False),
            sa.Column("decision", sa.String(length=16), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("risk_level", sa.String(length=16), nullable=False),
            sa.Column("permission_level", sa.Integer(), nullable=False),
            sa.Column("target", sa.Text(), nullable=False),
            sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("exit_code", sa.Integer(), nullable=True),
            sa.Column("output_bytes", sa.Integer(), nullable=False),
            sa.Column("truncated", sa.Boolean(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=False),
            sa.Column("latency_ms", sa.Integer(), nullable=False),
            sa.Column("confirmed_by_user", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.ForeignKeyConstraint(["ai_run_id"], ["ai_runs.id"], name=op.f("fk_tool_audit_log_ai_run_id_ai_runs"), ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_audit_log")),
        ),
    )
    _create_index_if_absent(op.f("ix_tool_audit_log_ai_run_id"), "tool_audit_log", ["ai_run_id"])
    _create_index_if_absent(op.f("ix_tool_audit_log_created_at"), "tool_audit_log", ["created_at"])
    _create_index_if_absent(op.f("ix_tool_audit_log_tool_name"), "tool_audit_log", ["tool_name"])
    _create_index_if_absent("ix_tool_audit_run_step", "tool_audit_log", ["ai_run_id", "step_index"])
    _create_index_if_absent("ix_tool_audit_tool_created", "tool_audit_log", ["tool_name", "created_at"])

    _create_table_if_absent(
        "messages",
        [
            "conversation_id", "user_id", "role", "kind", "content", "questions",
            "build_run_id", "decided_for_you", "retrieval", "answered_at", "id",
            "created_at", "updated_at",
        ],
        lambda: op.create_table(
            "messages",
            sa.Column("conversation_id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("role", sa.String(length=12), nullable=False),
            sa.Column("kind", sa.String(length=12), nullable=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("questions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("build_run_id", sa.Uuid(), nullable=True),
            sa.Column("decided_for_you", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("retrieval", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["build_run_id"], ["build_runs.id"], name=op.f("fk_messages_build_run_id_build_runs"), ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], name=op.f("fk_messages_conversation_id_conversations"), ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_messages_user_id_users"), ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        ),
    )
    _create_index_if_absent(op.f("ix_messages_build_run_id"), "messages", ["build_run_id"])
    _create_index_if_absent("ix_messages_conversation_created", "messages", ["conversation_id", "created_at"])
    _create_index_if_absent(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"])
    _create_index_if_absent(op.f("ix_messages_created_at"), "messages", ["created_at"])
    _create_index_if_absent(op.f("ix_messages_user_id"), "messages", ["user_id"])


def downgrade() -> None:
    """Drops these tables only if they are empty.

    A rollback that deletes the user's conversations, installed skills and audit trail is
    not a rollback. On a database with rows in any of them this raises and says so; the
    decision to destroy that data is a person's to make, explicitly, not a side effect of
    `alembic downgrade`.
    """
    inspector = _inspector()
    present = set(inspector.get_table_names())
    bind = op.get_bind()

    for table in ("messages", "tool_audit_log", "installed_skills", "conversations"):
        if table not in present:
            continue
        rows = bind.execute(sa.text(f'select count(*) from "{table}"')).scalar_one()
        if rows:
            raise RuntimeError(
                f"拒绝降级：表 {table!r} 中有 {rows} 行数据。"
                "这些是用户的会话、技能与审计记录，删除是不可逆的；"
                "如确需删除，请先备份并人工执行。"
            )

    for table in ("messages", "tool_audit_log", "installed_skills", "conversations"):
        if table in present:
            op.drop_table(table)

"""AI engineering models.

``Prompt`` / ``PromptVersion`` implement prompt versioning: business code always
asks for a prompt *by name* and receives the current version, and every AI run
records exactly which prompt version produced it.

``AIRun`` is the execution trace: request, provider, model, prompt version,
retrieved context, tool calls, token usage, latency and errors.  It answers the
question "what did the AI actually do?".
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import RunStatus, RunType
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Prompt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "prompts"

    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    versions: Mapped[list[PromptVersion]] = relationship(
        back_populates="prompt", cascade="all, delete-orphan", order_by="PromptVersion.version"
    )


class PromptVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("prompt_id", "version", name="uq_prompt_versions_prompt_id_version"),
        Index("ix_prompt_versions_current", "prompt_id", "is_current"),
    )

    prompt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("prompts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    prompt: Mapped[Prompt] = relationship(back_populates="versions")


class AIRun(Base, UUIDPrimaryKeyMixin):
    """One AI execution. Never deleted implicitly — it is the audit trail."""

    __tablename__ = "ai_runs"
    __table_args__ = (
        Index("ix_ai_runs_owner_created", "owner_id", "created_at"),
        Index("ix_ai_runs_type_created", "run_type", "created_at"),
    )

    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    knowledge_base_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="SET NULL"), nullable=True
    )
    parent_run_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    run_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RunType.RAG_ANSWER.value, index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RunStatus.RUNNING.value, index=True
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    prompt_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    response_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tools_used: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    retrieval_meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    retrieval_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_estimate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tool_calls: Mapped[list[ToolCall]] = relationship(
        back_populates="ai_run", cascade="all, delete-orphan"
    )
    sources: Mapped[list[AIRunSource]] = relationship(
        back_populates="ai_run", cascade="all, delete-orphan"
    )
    agent_run: Mapped[AgentRun | None] = relationship(
        back_populates="ai_run", cascade="all, delete-orphan", uselist=False
    )


class AgentRun(Base, UUIDPrimaryKeyMixin):
    """Multi-step agent execution attached to an ``AIRun`` trace."""

    __tablename__ = "agent_runs"

    ai_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ai_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RunStatus.RUNNING.value)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    steps_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    plan: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    scratchpad: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    final_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_entity_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    ai_run: Mapped[AIRun] = relationship(back_populates="agent_run")


class ToolCall(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_ai_run_step", "ai_run_id", "step_index"),)

    ai_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="SUCCEEDED")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    #: Stable id for this invocation, minted **before** the tool runs.
    #:
    #: It is what makes the record usable after a crash: recovery can find a row in
    #: `RUNNING` and know which invocation it was, without guessing from the arguments.
    #: Unique, so a retry of the same invocation cannot create a second row that looks
    #: like a second execution.
    invocation_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True, unique=True
    )
    #: When the tool was handed to the executor, and when its outcome was written. A row
    #: with `started_at` and no `finished_at` is the window this column exists for.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: True when the tool can change something — a file, a row, a process. Recovery
    #: refuses to re-run these from an `UNKNOWN` state, because it cannot know whether the
    #: effect already happened.
    mutates_state: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    #: The working directory relative paths in `arguments` were resolved against.
    #:
    #: Without it the arguments are not enough to check anything: `file_write("out.txt")`
    #: names a different file in every run, and reconciliation — which happens in a later
    #: process, with no run in flight — would have to guess which one. Recorded at intent
    #: time, because that is the only moment the answer exists.
    working_directory: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    #: When a later check of the world decided what this invocation did, if it did.
    #:
    #: `finished_at` is when the tool's own process observed the outcome. This is when
    #: somebody else went and looked. A row can legitimately have this set and
    #: `finished_at` NULL — that is the `UNKNOWN` that was resolved afterwards — and the
    #: two must not be confused, which is why they are two columns.
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ai_run: Mapped[AIRun] = relationship(back_populates="tool_calls")


class AIRunSource(Base):
    """Which chunks/documents an AI run actually used (citation + provenance)."""

    __tablename__ = "ai_run_sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ai_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("document_chunks.id", ondelete="SET NULL"), nullable=True
    )
    document_title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")

    ai_run: Mapped[AIRun] = relationship(back_populates="sources")


class ToolAuditLog(Base, UUIDPrimaryKeyMixin):
    """One row per computer-tool invocation, whether it ran or not.

    Separate from ``tool_calls`` on purpose. ``tool_calls`` records what the
    *agent* did as part of a run and is written after the fact. This table
    records what the *system* decided and what the machine actually experienced:
    the permission verdict, the risk classification, the resolved target, the
    exit code and the truncation flag.

    A denied or declined invocation is the most important row in the table, so it
    is written too — an audit trail that only contains the operations that were
    allowed cannot answer "what did it try to do?".
    """

    __tablename__ = "tool_audit_log"
    __table_args__ = (
        Index("ix_tool_audit_run_step", "ai_run_id", "step_index"),
        Index("ix_tool_audit_tool_created", "tool_name", "created_at"),
    )

    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ai_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="general")

    #: allow | confirm | deny
    decision: Mapped[str] = mapped_column(String(16), nullable=False, default="allow")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: low | medium | high | critical
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    permission_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    #: The concrete thing acted on — a path, a command, a host. Kept so the log
    #: answers "which file?" without having to parse the arguments back out.
    target: Mapped[str] = mapped_column(Text, nullable=False, default="")
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: SUCCEEDED | FAILED | DENIED | DECLINED | TIMEOUT | CANCELLED
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="SUCCEEDED")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Set when the user was asked and answered, so "who allowed this?" is answerable.
    confirmed_by_user: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

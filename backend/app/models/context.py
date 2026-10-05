"""Personal Context Layer: profile, memory, preferences.

Four things that are deliberately kept apart, because merging them into one
vector store is how "an AI that knows me" turns into "an AI that recalls
irrelevant fragments":

    Profile       who the user is            structured, one row, always loaded
    Memory        facts worth keeping        structured, few, confirmed
    Knowledge     files the user supplied    the existing RAG system, untouched
    Project       what they are working on   derived from projects/tasks, not new

Memory is the part that needs discipline. The rule this module is built around:

    **Recording something wrong is worse than recording nothing.**

So a memory is a row with a kind, a scope, a confidence and a status, and it
only becomes `active` after either the model is very confident or the user says
so. There is no path that turns a conversation into embeddings.
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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Who the user is. One row per user, always loaded, always compressed."""

    __tablename__ = "user_profile"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: Occupation or role, in the user's own words.
    identity: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: Life or career stage: student, job hunting, employed, ...
    stage: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    domain: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: Free-form list of what they are currently aiming at.
    current_goals: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")
    bio: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Set when the user has been through the first-run wizard.
    onboarded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def digest(self) -> str:
        """A short, stable rendering used as the always-on context block.

        Compressed on purpose: the profile is carried into every call, so it has
        to be cheap. Empty fields are dropped rather than emitted as blanks.
        """
        parts = []
        if self.identity:
            parts.append(self.identity)
        if self.stage:
            parts.append(self.stage)
        if self.domain:
            parts.append(f"领域：{self.domain}")
        if self.current_goals:
            parts.append("当前目标：" + "；".join(self.current_goals[:4]))
        if self.bio:
            parts.append(self.bio[:200])
        return "，".join(parts)


class UserPreference(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Behavioural preferences. These change defaults; they never change safety."""

    __tablename__ = "user_preferences"
    __table_args__ = (Index("ix_user_preferences_scope", "user_id", "scope", "key", unique=True),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False, default=dict)
    #: global, or project:<uuid> for a preference that only applies there.
    scope: Mapped[str] = mapped_column(String(64), nullable=False, default="global")
    #: Where it came from: user, agent, or a confirmed memory.
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="user")


class MemoryEntry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One thing worth remembering about the user."""

    __tablename__ = "memory_entries"
    __table_args__ = (
        Index("ix_memory_user_status", "user_id", "status"),
        Index("ix_memory_user_kind", "user_id", "kind"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: identity | goal | preference | skill | constraint | fact | decision
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="fact")
    #: global | project:<uuid> | knowledge:<uuid>
    scope: Mapped[str] = mapped_column(String(64), nullable=False, default="global")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    structured: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)
    #: conversation | document | agent | manual
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="conversation")
    #: active | archived | rejected. Only `active` is ever retrieved.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "kind": self.kind,
            "scope": self.scope,
            "content": self.content,
            "structured": self.structured,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "status": self.status,
            "confirmed_by_user": self.confirmed_by_user,
            "use_count": self.use_count,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class MemoryCandidate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A memory the system proposes but will not store without a human decision.

    This table is the difference between "an AI that decided things about me" and
    "an AI that asks". Anything the evaluator is not confident about lands here.
    """

    __tablename__ = "memory_candidates"
    __table_args__ = (Index("ix_memory_candidates_user_status", "user_id", "status"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    proposed_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="fact")
    proposed_scope: Mapped[str] = mapped_column(String(64), nullable=False, default="global")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: Why the system thinks this is worth remembering, shown to the user.
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.6)
    #: pending | approved | rejected
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set when the candidate is a conflict with an existing memory, so the UI can
    #: show both sides instead of silently replacing one.
    conflicts_with: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "proposed_kind": self.proposed_kind,
            "proposed_scope": self.proposed_scope,
            "content": self.content,
            "reason": self.reason,
            "confidence": round(self.confidence, 3),
            "status": self.status,
            "conflicts_with": self.conflicts_with,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ContextSnapshot(Base, UUIDPrimaryKeyMixin):
    """What was actually injected into one model call.

    This is the transparency guarantee. "It knows me" is only trustworthy if the
    user can see exactly which facts were used, and correct them.
    """

    __tablename__ = "context_snapshots"
    __table_args__ = (Index("ix_context_snapshots_run", "run_id"),)

    run_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: What kind of call this was: planner | executor | rag | agent
    purpose: Mapped[str] = mapped_column(String(24), nullable=False, default="agent")
    profile_digest: Mapped[str] = mapped_column(Text, nullable=False, default="")
    memory_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    knowledge_chunk_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    project_digest: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Rendered block, so the UI can show the exact text that was sent.
    rendered: Mapped[str] = mapped_column(Text, nullable=False, default="")
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    def as_dict(self, *, include_rendered: bool = True) -> dict[str, Any]:
        payload = {
            "id": str(self.id),
            "run_id": self.run_id,
            "purpose": self.purpose,
            "profile_digest": self.profile_digest,
            "memory_ids": self.memory_ids,
            "knowledge_chunk_ids": self.knowledge_chunk_ids,
            "project_digest": self.project_digest,
            "token_estimate": self.token_estimate,
            "truncated": self.truncated,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_rendered:
            payload["rendered"] = self.rendered
        return payload


__all__ = [
    "ContextSnapshot",
    "MemoryCandidate",
    "MemoryEntry",
    "UserPreference",
    "UserProfile",
]

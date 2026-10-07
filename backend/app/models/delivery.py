"""The delivery layer: BuildRun and Deliverable.

This is the layer the other two exist to serve.

   认知层  what it knows       documents · concepts · memory
   行动层  what it can do      planning · tools · execution
   交付层  what it produced    BuildRun · Deliverable · verification · acceptance

Before this module the product could *do* things but had nothing that treated the
result as the point. An agent run ended in a state string; the file it wrote was a
side effect. Nothing recorded whether the file was any good, and nothing recorded
whether the person who asked for it was satisfied.

**The acceptance fields are the whole design.** `Deliverable.accepted` and
`Deliverable.feedback` are not metadata — they are the only place in the system where
the user states what they actually wanted. A conversation turn is a guess about
preference; a rejection with a reason is a fact about it.

    "图表太少"  →  a preference, stated by the person who knows
    "接受"      →  a confirmation that this is what good looks like

Everything else in the product is machinery for producing that moment.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BuildStatus(StrEnum):
    """The states one "build me this" request moves through.

    A single enum rather than a pile of booleans, because the interesting bugs live
    in the impossible combinations — "executing and completed", "failed and
    waiting_user" — and an enum makes them unrepresentable.
    """

    DRAFT = "DRAFT"
    PLANNING = "PLANNING"
    READY = "READY"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    WAITING_USER = "WAITING_USER"
    COMPLETED = "COMPLETED"
    #: The run did not succeed, but it did leave files behind.
    #:
    #: This state exists because "FAILED" was previously reported for a run that had
    #: produced a 70 KB deck, and the deck was registered with `verified=True`
    #: anyway. Two facts that contradict each other, both written down, and the one
    #: the user reads is the verdict. Naming the middle case is how the record stops
    #: contradicting itself: the files are real, the run did not finish, and both
    #: are true.
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    LIMIT_REACHED = "LIMIT_REACHED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            BuildStatus.COMPLETED,
            BuildStatus.PARTIAL,
            BuildStatus.FAILED,
            BuildStatus.CANCELLED,
            BuildStatus.LIMIT_REACHED,
        }

    @property
    def is_active(self) -> bool:
        """True while the system is still working, which is what the home page lists."""
        return self in {
            BuildStatus.PLANNING,
            BuildStatus.READY,
            BuildStatus.EXECUTING,
            BuildStatus.VERIFYING,
            BuildStatus.WAITING_USER,
        }


#: What kind of thing the user asked for. Drives which writer the agent reaches for.
DELIVERABLE_KINDS = ("pptx", "docx", "xlsx", "pdf", "md", "csv", "report", "code", "mixed")


class BuildRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One "build me this" request, from the sentence to the accepted artifact."""

    __tablename__ = "build_runs"
    __table_args__ = (
        Index("ix_build_runs_user_status", "user_id", "status"),
        Index("ix_build_runs_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )

    #: The user's own words. Never rewritten — the plan is a translation of this, and
    #: keeping the original lets the UI show what was actually asked for.
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    #: Which material this run may use. Explicit rather than "everything", because
    #: "which of my documents did it read" is a question the user is entitled to answer.
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    deliverable_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="mixed")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=BuildStatus.DRAFT.value, index=True
    )

    #: The agent's plan, in the shape `ai/agent/plan.py` already produces.
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: What was actually injected into this run's prompts.
    context_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("context_snapshots.id", ondelete="SET NULL"), nullable=True
    )

    #: The AI run this build drove. The agent runtime is reused, not reimplemented,
    #: so this is the bridge to it — and it is the `ai_runs` row, not the
    #: `agent_runs` row, because that is the id the recorder hands back and the one
    #: tool calls hang off.
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="SET NULL"), nullable=True
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    result_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")

    #: Steps completed, for the home page's real progress indicator. Derived from the
    #: plan and the event stream, stored so the list view does not have to replay events.
    steps_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    steps_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    deliverables: Mapped[list[Deliverable]] = relationship(
        back_populates="build_run", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def progress(self) -> float:
        """Real progress, or 0 when there is nothing honest to report.

        Deliberately not a time-based estimate: a bar that advances while nothing
        happens is a lie the user catches immediately.
        """
        if self.steps_total <= 0:
            return 1.0 if self.status == BuildStatus.COMPLETED.value else 0.0
        return min(1.0, self.steps_done / self.steps_total)


    def _deliverable_count(self) -> int:
        """Count attached deliverables without triggering a lazy load.

        A freshly flushed object has no loaded collection, and touching it in async
        SQLAlchemy raises `MissingGreenlet` — an error that surfaces as a 500 on the
        create endpoint. Reading the inspector first means the count is 0 for an
        object that has none, and correct for one that was loaded from a query.
        """
        from sqlalchemy import inspect as sa_inspect

        state = sa_inspect(self)
        if "deliverables" in state.unloaded:
            return 0
        return len(self.deliverables)

    def as_dict(self, *, include_plan: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": str(self.id),
            "goal": self.goal,
            "title": self.title or self.goal[:60],
            "status": self.status,
            "deliverable_kind": self.deliverable_kind,
            "scope": self.scope,
            "project_id": str(self.project_id) if self.project_id else None,
            "steps_total": self.steps_total,
            "steps_done": self.steps_done,
            "progress": round(self.progress, 3),
            "result_summary": self.result_summary,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "deliverable_count": self._deliverable_count(),
        }
        if include_plan:
            payload["plan"] = self.plan
            payload["context_snapshot_id"] = (
                str(self.context_snapshot_id) if self.context_snapshot_id else None
            )
            payload["ai_run_id"] = str(self.ai_run_id) if self.ai_run_id else None
        return payload


class Deliverable(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A real file the user can open, and what they thought of it.

    Two independent judgements live on this row and they must not be conflated:

    * `verified` — the *machine* checked the file (it exists, it opens, it has the
      expected page count). Set by the agent, from evidence.
    * `accepted` — the *user* decided it is what they wanted. Null until they say.

    A file can be perfectly verified and completely unwanted. Collapsing the two would
    lose exactly the information the product is built to collect.
    """

    __tablename__ = "deliverables"
    __table_args__ = (
        Index("ix_deliverables_run", "build_run_id"),
        Index("ix_deliverables_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    build_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("build_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )

    path: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="md")
    title: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    #: Version chain: a redo is version n+1 pointing at n, so the history of "what
    #: changed after I said 图表太少" is a walk up the chain rather than a guess.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deliverables.id", ondelete="SET NULL"), nullable=True
    )

    #: Machine verification. See `VerificationResult` for the shape.
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: User acceptance. Null means they have not said — which is different from "no".
    accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: Why they rejected it, in their words. The raw material for memory.
    feedback: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Which preferences and memories were in play when this was produced, so a later
    #: comparison can say what changed.
    applied_preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    style: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    build_run: Mapped[BuildRun | None] = relationship(back_populates="deliverables")

    def as_dict(self, *, include_verification: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": str(self.id),
            "build_run_id": str(self.build_run_id) if self.build_run_id else None,
            "path": self.path,
            "kind": self.kind,
            "title": self.title,
            "size_bytes": self.size_bytes,
            "version": self.version,
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "verified": self.verified,
            "accepted": self.accepted,
            "feedback": self.feedback,
            "style": self.style,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_verification:
            payload["verification"] = self.verification
            payload["applied_preferences"] = self.applied_preferences
        return payload


def make_verification(
    checks: list[dict[str, Any]], *, run_state: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Assemble a verification result from individual checks.

    `passed` is derived, never passed in: a result that claims to have passed while
    containing a failed check is the exact failure this structure exists to prevent.

    **`run_state` is a sibling of `checks`, not another check.** The two answer
    different questions and the whole point of this shape is that they cannot be
    confused:

    * `checks` — *is this file any good?* It exists, it reads back, it has the page
      count it claims. This is what `passed` is derived from.
    * `run_state` — *did the run that produced it get all the way through?*

    A file can be perfectly good while the run that made it did not finish; both
    facts are true at once. Folding the second into the first meant a run that
    ended `PARTIAL` produced a deliverable the UI labelled 「未校验」 — the system
    telling the user something false about their artifact because something else,
    elsewhere, had gone wrong. The user's own words for the resulting confusion
    were that the file was plainly fine and the screen disagreed.
    """
    normalised = [
        {
            "name": str(check.get("name") or "unnamed"),
            "ok": bool(check.get("ok")),
            "detail": str(check.get("detail") or ""),
        }
        for check in checks
    ]
    payload: dict[str, Any] = {
        "checks": normalised,
        "passed": bool(normalised) and all(check["ok"] for check in normalised),
        "check_count": len(normalised),
        "failed_count": sum(1 for check in normalised if not check["ok"]),
    }
    if run_state is not None:
        payload["run_state"] = {
            "state": str(run_state.get("state") or ""),
            #: Whether the *run* finished normally. Deliberately named apart from
            #: `passed`: one is about the artifact, the other about the process.
            "ok": bool(run_state.get("ok")),
            "detail": str(run_state.get("detail") or ""),
        }
    return payload


def describe_run_state(status: str) -> dict[str, Any]:
    """The run's terminal state as a sentence, for the column beside the file's own.

    Derived from `BuildStatus` rather than written per call site, so the wording and
    the `ok` flag cannot drift apart from the enum — and so "the run finished" has
    exactly one definition in the codebase.
    """
    try:
        state = BuildStatus(status)
    except ValueError:
        return {
            "state": status,
            "ok": False,
            "detail": f"这次运行的终态是 {status or '未知'}，不是系统认识的完成状态。",
        }

    if state is BuildStatus.COMPLETED:
        return {"state": state.value, "ok": True, "detail": "这次运行走完了全部步骤。"}
    if state is BuildStatus.PARTIAL:
        return {
            "state": state.value,
            "ok": False,
            "detail": "这次运行没有走完全部步骤 —— 文件本身是好的，但产出它的过程没有正常结束。",
        }
    if state is BuildStatus.FAILED:
        return {
            "state": state.value,
            "ok": False,
            "detail": "这次运行失败了 —— 文件已经落盘，但这次执行没有走完。",
        }
    if state is BuildStatus.CANCELLED:
        return {"state": state.value, "ok": False, "detail": "这次运行被取消了。"}
    if state is BuildStatus.LIMIT_REACHED:
        return {"state": state.value, "ok": False, "detail": "这次运行达到了资源上限而停止。"}
    if state is BuildStatus.WAITING_USER:
        return {
            "state": state.value,
            "ok": False,
            # True for both ways this state arises — the agent finished every step
            # but could not verify the criteria, or it finished without producing a
            # file the collector could see. In neither case did the run reach
            # "verified complete", and in neither case did anything fail.
            "detail": "这次运行没有走到「已验证完成」，需要你自己看一眼。",
        }
    return {"state": state.value, "ok": False, "detail": f"这次运行还在进行中（{state.value}）。"}


__all__ = [
    "DELIVERABLE_KINDS",
    "BuildRun",
    "BuildStatus",
    "Deliverable",
    "describe_run_state",
    "make_verification",
]

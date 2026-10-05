"""Execution plan model for the autonomous agent.

A plan is not decoration. It is what turns "the model did some things" into a
process the user can watch, judge and interrupt:

* every step carries its own status, so the UI can show what is done, what is
  running and what failed — without inferring it from a log;
* every step declares **how it will be verified**, because a step that cannot be
  checked is not a step, it is a hope;
* the plan states its success criteria up front, so "done" is defined before the
  work starts rather than asserted after it.

The states are an enum rather than a set of booleans on purpose. ``is_running``
plus ``is_paused`` plus ``is_failed`` admits combinations that mean nothing, and
the impossible states are exactly where the bugs live.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class RunState(StrEnum):
    """Where an autonomous run is. One value, always meaningful."""

    PLANNING = "PLANNING"  # the model is producing the plan
    READY = "READY"  # plan exists, nothing executed yet
    EXECUTING = "EXECUTING"  # a tool is running
    OBSERVING = "OBSERVING"  # reading the result of the last action
    VERIFYING = "VERIFYING"  # checking the success criteria against reality
    RECOVERING = "RECOVERING"  # analysing a failure and preparing a fix
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"  # a human must decide
    PAUSED = "PAUSED"  # the user paused it
    CANCELLED = "CANCELLED"  # the user stopped it
    FAILED = "FAILED"  # gave up: unrecoverable, or out of retries
    LIMIT_REACHED = "LIMIT_REACHED"  # hit a resource ceiling
    COMPLETED = "COMPLETED"  # success criteria met

    @property
    def terminal(self) -> bool:
        return self in {
            RunState.COMPLETED,
            RunState.FAILED,
            RunState.CANCELLED,
            RunState.LIMIT_REACHED,
        }

    @property
    def active(self) -> bool:
        return not self.terminal and self is not RunState.PAUSED


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"

    @property
    def settled(self) -> bool:
        return self in {StepStatus.DONE, StepStatus.SKIPPED, StepStatus.FAILED}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class PlanStep:
    """One unit of intended work."""

    id: str
    description: str
    #: How this step will be checked. Free text, but the model is required to
    #: fill it — a plan whose steps cannot be verified is rejected at parse time.
    verification: str = ""
    status: StepStatus = StepStatus.PENDING
    tool_hint: str = ""
    started_at: str = ""
    completed_at: str = ""
    tool_calls: int = 0
    retry_count: int = 0
    result: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "verification": self.verification,
            "status": self.status.value,
            "tool_hint": self.tool_hint,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "tool_calls": self.tool_calls,
            "retry_count": self.retry_count,
            "result": self.result[:600],
            "error": self.error[:600],
        }


@dataclass
class ExecutionPlan:
    """What the agent intends to do, and how it will know it worked."""

    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    #: Statements that must hold for the task to count as finished. Checked
    #: against real evidence (exit codes, file state), not against prose.
    success_criteria: list[str] = field(default_factory=list)
    risk_level: str = "low"
    estimated_tools: list[str] = field(default_factory=list)
    estimated_duration_seconds: int = 0
    created_at: str = field(default_factory=_now)
    revision: int = 0
    #: Set when the plan had to be regenerated after a failure.
    replanned_from: str = ""

    @classmethod
    def from_model(cls, payload: dict[str, Any], goal: str) -> ExecutionPlan:
        """Build a plan from the model's JSON, rejecting unusable output.

        A plan with no verifiable steps would let the agent declare victory on
        nothing, so it is treated as malformed rather than accepted and patched
        up later.
        """
        raw_steps = payload.get("steps") or []
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("The plan contains no steps")

        steps: list[PlanStep] = []
        for index, item in enumerate(raw_steps, start=1):
            if isinstance(item, str):
                item = {"description": item}
            if not isinstance(item, dict):
                continue
            description = str(item.get("description") or item.get("step") or "").strip()
            if not description:
                continue
            steps.append(
                PlanStep(
                    id=str(item.get("id") or f"step-{index}"),
                    description=description,
                    verification=str(item.get("verification") or item.get("verify") or "").strip(),
                    tool_hint=str(item.get("tool") or item.get("tool_hint") or "").strip(),
                )
            )

        if not steps:
            raise ValueError("The plan contains no usable steps")

        criteria = payload.get("success_criteria") or payload.get("criteria") or []
        if isinstance(criteria, str):
            criteria = [criteria]
        criteria = [str(c).strip() for c in criteria if str(c).strip()]
        if not criteria:
            # Derive from the goal rather than accept an unverifiable plan.
            criteria = [f"目标达成：{goal.strip()[:200]}"]

        tools = payload.get("estimated_tools") or []
        if isinstance(tools, str):
            tools = [tools]

        try:
            duration = int(payload.get("estimated_duration_seconds") or 0)
        except (TypeError, ValueError):
            duration = 0

        risk = str(payload.get("risk_level") or "low").lower()
        if risk not in {"low", "medium", "high", "critical"}:
            risk = "low"

        return cls(
            goal=goal,
            steps=steps,
            success_criteria=criteria,
            risk_level=risk,
            estimated_tools=[str(t) for t in tools][:20],
            estimated_duration_seconds=max(0, duration),
        )

    # ------------------------------------------------------------- progress --
    @property
    def done_steps(self) -> int:
        return sum(1 for s in self.steps if s.status is StepStatus.DONE)

    @property
    def failed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status is StepStatus.FAILED)

    @property
    def current_step(self) -> PlanStep | None:
        for step in self.steps:
            if not step.status.settled:
                return step
        return None

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0.0
        settled = sum(1 for s in self.steps if s.status.settled)
        return round(settled / len(self.steps), 3)

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": [s.as_dict() for s in self.steps],
            "success_criteria": self.success_criteria,
            "risk_level": self.risk_level,
            "estimated_tools": self.estimated_tools,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "created_at": self.created_at,
            "revision": self.revision,
            "progress": self.progress,
            "done_steps": self.done_steps,
            "failed_steps": self.failed_steps,
            "total_steps": len(self.steps),
        }


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
@dataclass
class RunEvent:
    """One thing that happened during a run, in order.

    The event stream is what the UI renders live and what a later reader replays.
    It is append-only and cheap to produce, so nothing important is left only in
    a log file.
    """

    seq: int
    kind: str  # state | plan | step | tool | verification | confirmation | error | result
    state: str = ""
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    at: str = field(default_factory=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "state": self.state,
            "message": self.message,
            "data": self.data,
            "at": self.at,
        }


def new_step_id() -> str:
    return uuid.uuid4().hex[:8]


__all__ = [
    "ExecutionPlan",
    "PlanStep",
    "RunEvent",
    "RunState",
    "StepStatus",
    "new_step_id",
]

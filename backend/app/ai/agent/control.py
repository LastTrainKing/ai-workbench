"""Live control of a running agent: pause, resume, cancel, and the event stream.

Cancellation here is real, not a database flag. The controller is checked between
steps *and* the running tool's asyncio task is cancelled, which propagates into
``_run_process`` where the child process tree is killed. A "cancelled" run that
leaves a build compiling in the background would be a lie.

Pause works the other way round: it blocks at the next checkpoint rather than
interrupting a tool mid-flight. Stopping a file write halfway through would leave
the workspace inconsistent, which is worse than waiting for the current step to
finish.

The event stream is append-only and in memory. It is what the UI polls while a run
is live, and it is copied into the run record when the run ends, so the history
survives without needing a table per event kind.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.ai.agent.plan import ExecutionPlan, RunEvent, RunState
from app.core.logging import get_logger

logger = get_logger(__name__)

#: How many events to keep per run. Generous enough to replay a long run, bounded
#: so a runaway loop cannot exhaust memory.
MAX_EVENTS = 2000


class RunCancelledError(Exception):
    """Raised inside the run when the user cancels it."""


class RunPausedError(Exception):
    """Internal signal; the loop resumes from the same point."""


@dataclass
class RunController:
    """Owns the live state of one autonomous run."""

    run_id: str
    #: Whose run this is. Required, and not defaulted: `GET /agent/runs` used to
    #: return every run in the process to every authenticated caller, because the
    #: controller had no idea who started it. A default of None would let that
    #: bug come back silently, so callers have to say.
    owner_id: uuid.UUID
    state: RunState = RunState.PLANNING
    plan: ExecutionPlan | None = None
    events: list[RunEvent] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float = 0.0

    #: Tool calls, files changed and retries, for the run summary.
    tool_calls: int = 0
    files_modified: int = 0
    retries: int = 0
    confirmations_requested: int = 0

    #: Set while a tool is running, so cancel can end it.
    current_task: asyncio.Task[Any] | None = None
    current_tool: str = ""
    current_step_id: str = ""

    #: True once `_execute_run` has finished with this run entirely.
    #:
    #: **`state` is not this signal, and treating it as one produced two false
    #: records.** The agent sets a terminal state and then keeps working: it snapshots
    #: the workspace, generates a final report (another model call), and only then does
    #: the service persist the outcome and release the employee. Anything that watched
    #: `state` alone therefore concluded "done" while the run was still writing — the
    #: build run was stamped COMPLETED and its deliverables were collected from a run
    #: that had not finished.
    #:
    #: Measured on a real run: the build run read COMPLETED, `ai_runs.status` was still
    #: RUNNING with `finished_at` NULL, and the employee stayed 工作中. The process
    #: exited before the wrap-up ran, so nothing ever corrected it.
    settled: bool = False

    _resume: asyncio.Event = field(default_factory=asyncio.Event)
    _cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    _seq: int = 0
    #: The state to return to when a paused run is resumed.
    _state_before_pause: RunState | None = None

    def __post_init__(self) -> None:
        self._resume.set()  # running by default

    # ------------------------------------------------------------------ state --
    def set_state(self, state: RunState, message: str = "") -> None:
        if self.state is state and not message:
            return
        self.state = state
        self.emit("state", message=message, state=state.value)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def paused(self) -> bool:
        return not self._resume.is_set()

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished_at or time.monotonic()
        return round(end - self.started_at, 1)

    # ---------------------------------------------------------------- control --
    def pause(self) -> bool:
        if self.state.terminal:
            return False
        # Remember where we were, so resume returns to the right state rather than
        # guessing EXECUTING.
        if self.state is not RunState.PAUSED:
            self._state_before_pause = self.state
        self._resume.clear()
        self.state = RunState.PAUSED
        self.emit("state", message="已暂停，等待用户恢复", state=RunState.PAUSED.value)
        return True

    def resume(self) -> bool:
        if self.cancelled or self.state.terminal:
            return False
        self._resume.set()
        self.state = self._state_before_pause or RunState.EXECUTING
        self.emit("state", message="已恢复", state=self.state.value)
        return True

    def cancel(self) -> bool:
        """Stop the run and whatever it is currently doing."""
        if self.state.terminal:
            return False
        self._cancelled.set()
        self._resume.set()  # release a paused run so it can observe the cancel
        # End the running tool. `_run_process` turns this into a process-tree kill.
        task = self.current_task
        if task is not None and not task.done():
            task.cancel()
        self.state = RunState.CANCELLED
        self.emit("state", message="已被用户取消", state=RunState.CANCELLED.value)
        return True

    # --------------------------------------------------------------- checkpoints --
    async def checkpoint(self) -> None:
        """Called between steps. Blocks while paused; raises when cancelled."""
        if self._cancelled.is_set():
            raise RunCancelledError("The run was cancelled")
        if not self._resume.is_set():
            await self._resume.wait()
        if self._cancelled.is_set():
            raise RunCancelledError("The run was cancelled")

    # ----------------------------------------------------------------- events --
    def emit(
        self,
        kind: str,
        *,
        message: str = "",
        state: str = "",
        data: dict[str, Any] | None = None,
    ) -> RunEvent:
        self._seq += 1
        event = RunEvent(
            seq=self._seq,
            kind=kind,
            state=state or self.state.value,
            message=message,
            data=data or {},
        )
        self.events.append(event)
        if len(self.events) > MAX_EVENTS:
            # Keep the beginning (the plan) and the end (what happened).
            head = self.events[:200]
            tail = self.events[-(MAX_EVENTS - 200) :]
            self.events = head + tail
        return event

    def events_since(self, seq: int) -> list[dict[str, Any]]:
        return [e.as_dict() for e in self.events if e.seq > seq]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state.value,
            "paused": self.paused,
            "elapsed_seconds": self.elapsed_seconds,
            "tool_calls": self.tool_calls,
            "files_modified": self.files_modified,
            "retries": self.retries,
            "confirmations_requested": self.confirmations_requested,
            "current_tool": self.current_tool,
            "current_step_id": self.current_step_id,
            "plan": self.plan.as_dict() if self.plan else None,
            "event_count": len(self.events),
        }


class RunRegistry:
    """The runs this process is currently driving."""

    def __init__(self) -> None:
        self._runs: dict[str, RunController] = {}

    def create(self, run_id: str, owner_id: uuid.UUID) -> RunController:
        controller = RunController(run_id=run_id, owner_id=owner_id)
        self._runs[run_id] = controller
        return controller

    def get(self, run_id: str) -> RunController | None:
        return self._runs.get(run_id)

    def drop(self, run_id: str) -> None:
        self._runs.pop(run_id, None)

    def active(self) -> list[RunController]:
        return [c for c in self._runs.values() if not c.state.terminal]

    def all(self) -> list[RunController]:
        return list(self._runs.values())

    def owned_by(self, owner_id: uuid.UUID) -> list[RunController]:
        """Only this user's runs.

        The registry is process-wide, but a run belongs to the person who started
        it. Returning `all()` to a caller was how one account saw another's runs.
        """
        return [c for c in self._runs.values() if c.owner_id == owner_id]


_registry: RunRegistry | None = None


def get_run_registry() -> RunRegistry:
    global _registry
    if _registry is None:
        _registry = RunRegistry()
    return _registry


__all__ = [
    "MAX_EVENTS",
    "RunCancelledError",
    "RunController",
    "RunPausedError",
    "RunRegistry",
    "get_run_registry",
]

"""Confirmation broker — how a high-risk operation reaches a human.

The executor refuses to guess. When a tool's verdict is CONFIRM it calls a handler
and waits; this module is that handler for agent runs.

The flow, end to end:

1. the executor decides a call needs approval and calls the handler
2. the broker registers a pending request against the run and publishes it
3. the run's trace shows the request; the UI renders it as a dialog with the
   operation, the target, the reason, the risk and the impact
4. the user answers through the API, which resolves the future
5. the executor proceeds or declines, and the answer is written to the audit row

The wait is bounded by ``policy.confirmation_timeout_seconds``. An unanswered
request is a *timeout*, not an implicit yes — the safe default for "nobody was
looking" is to do nothing.

Kept in-process on purpose: this is a local desktop application with one user, and
a pending approval is meaningless after a restart. Anything durable would be
inventing a distributed problem that does not exist here.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.ai.tools.executor import ConfirmationRequest
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PendingConfirmation:
    """One unanswered request."""

    id: str
    run_id: str
    tool: str
    operation: str
    target: str
    reason: str
    risk: str
    impact: str
    detail: str
    arguments: dict[str, Any]
    created_at: float = field(default_factory=time.monotonic)
    future: asyncio.Future[bool] | None = None
    answered: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        """What the UI renders. Deliberately includes the *why*, not just the what."""
        return {
            "id": self.id,
            "run_id": self.run_id,
            "tool": self.tool,
            "operation": self.operation or self.tool,
            "target": self.target,
            "reason": self.reason,
            "risk": self.risk,
            "impact": self.impact,
            "detail": self.detail,
            "arguments": self.arguments,
            "age_seconds": round(time.monotonic() - self.created_at, 1),
            "answered": self.answered,
        }


class ConfirmationBroker:
    """Holds the pending approvals for this process."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingConfirmation] = {}
        self._by_run: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def request(self, request: ConfirmationRequest) -> bool:
        """Called by the executor. Blocks until answered or timed out."""
        pending = PendingConfirmation(
            id=uuid.uuid4().hex[:16],
            run_id=request.run_id,
            tool=request.tool,
            operation=request.verdict.operation,
            target=request.verdict.target,
            reason=request.verdict.reason,
            risk=request.verdict.risk.value,
            impact=request.verdict.impact,
            detail=request.verdict.detail,
            arguments=request.arguments,
        )
        pending.future = asyncio.get_running_loop().create_future()

        async with self._lock:
            self._pending[pending.id] = pending
            self._by_run.setdefault(pending.run_id, []).append(pending.id)

        logger.info(
            "confirmation_requested",
            extra={
                "confirmation_id": pending.id,
                "run_id": pending.run_id,
                "tool": pending.tool,
                "risk": pending.risk,
                "target": pending.target[:200],
            },
        )

        try:
            return await pending.future
        finally:
            async with self._lock:
                self._pending.pop(pending.id, None)
                ids = self._by_run.get(pending.run_id, [])
                if pending.id in ids:
                    ids.remove(pending.id)

    async def resolve(self, confirmation_id: str, approved: bool) -> bool:
        """Answer a pending request. Returns False when it is unknown or stale."""
        async with self._lock:
            pending = self._pending.get(confirmation_id)
            if pending is None or pending.future is None or pending.future.done():
                return False
            pending.answered = approved
            pending.future.set_result(approved)

        logger.info(
            "confirmation_answered",
            extra={"confirmation_id": confirmation_id, "approved": approved},
        )
        return True

    def for_run(self, run_id: str) -> list[dict[str, Any]]:
        ids = self._by_run.get(run_id, [])
        return [self._pending[i].as_dict() for i in ids if i in self._pending]

    def all_pending(self) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self._pending.values()]

    def cancel_run(self, run_id: str) -> int:
        """Decline everything still waiting for a run that has ended."""
        ids = list(self._by_run.get(run_id, []))
        cancelled = 0
        for confirmation_id in ids:
            pending = self._pending.get(confirmation_id)
            if pending and pending.future and not pending.future.done():
                pending.answered = False
                pending.future.set_result(False)
                cancelled += 1
        return cancelled


#: Process-wide broker. One desktop, one user, one set of approvals.
_broker: ConfirmationBroker | None = None


def get_broker() -> ConfirmationBroker:
    global _broker
    if _broker is None:
        _broker = ConfirmationBroker()
    return _broker


__all__ = ["ConfirmationBroker", "PendingConfirmation", "get_broker"]

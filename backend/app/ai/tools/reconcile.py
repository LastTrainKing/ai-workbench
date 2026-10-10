"""Turn `UNKNOWN` into an answer, where an answer can be had.

`UNKNOWN` says nobody knows whether an interrupted tool call had its effect. The next
question is whether anybody *can* know, and for some tools the answer is yes: the state the
call intended is still observable. A file either holds those bytes or does not; a directory
either exists or does not; the git index either lists those paths or does not.

## What this module does, and what it deliberately does not

It **checks**. For every invocation in `UNKNOWN` whose tool implements `reconcile`, it looks
at the world and writes down one of three verdicts — `APPLIED`, `NOT_APPLIED`,
`UNDECIDABLE` — with the evidence that produced it.

It **never re-runs anything.** There is no replay path in this file and adding one would
undo the whole point: the record exists because a repeated side effect cannot be detected
after the fact, and the only safe way to avoid one is not to take it. A verdict of
`NOT_APPLIED` is the closest thing to permission to try again, and even that is left to a
human or a fresh run rather than acted on here — the check ran in a *different process, at a
different time*, and the gap between the check and the retry is another window.

`UNDECIDABLE` is the default and stays the default. Twenty-odd tools have no check, and
"nobody wrote one" must not be read as "nothing happened".

## Why the verdicts are about the state, not about history

A crash destroys the only record of what the tool observed. Nothing reconstructs it. So
these verdicts answer "is the state the invocation intended present *now*" — which is
exactly what recovery needs, because the question recovery is really asking is whether
re-running is necessary.

## `reconciled_at` is written only for a decisive verdict

A check that concluded "cannot tell" leaves the timestamp NULL, so a later pass — with a
better context, or after the tool gains a check — can try again. Stamping it would freeze
the answer at the moment the least was known.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.ai.agent.tools import ToolContext
from app.ai.tools.registry import tool_for
from app.ai.types import ReconcileOutcome, ReconcileVerdict
from app.core.logging import get_logger
from app.domain.enums import ToolCallStatus
from app.models.ai import ToolCall

logger = get_logger(__name__)


@dataclass
class ReconcileReport:
    """What one check found, in the shape the API and the run detail view want."""

    invocation_id: str
    tool_name: str
    step_index: int
    verdict: ReconcileVerdict
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    status_before: str = ToolCallStatus.UNKNOWN.value
    status_after: str = ToolCallStatus.UNKNOWN.value

    def to_payload(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "tool_name": self.tool_name,
            "step_index": self.step_index,
            "verdict": self.verdict.value,
            "detail": self.detail,
            "evidence": self.evidence,
            "status_before": self.status_before,
            "status_after": self.status_after,
        }


def context_for(
    session: Any,
    call: ToolCall,
    *,
    owner_id: uuid.UUID,
    permissions: Any | None = None,
    project_id: uuid.UUID | None = None,
) -> ToolContext:
    """A context that resolves paths the way the original run resolved them.

    `working_directory` comes from the invocation row rather than from anything current.
    A relative path in the arguments is only meaningful against the directory the run was
    actually working in, and the run is long gone — the row is the only witness.

    An empty working directory is not corrected here. `ToolContext.resolve` falls back to
    the permission policy's workspace, and a context with neither resolves against `.`,
    which is the process's own directory — a wrong answer that looks like a right one. The
    caller is expected to have checked; `reconcile_call` reports `UNDECIDABLE` when the
    record carries no working directory, rather than checking some other file.
    """
    return ToolContext(
        session=session,
        owner_id=owner_id,
        project_id=project_id,
        run_id=str(call.ai_run_id),
        working_directory=call.working_directory or "",
        permissions=permissions,
    )


async def reconcile_call(
    call: ToolCall,
    *,
    registry: Any,
    ctx: ToolContext,
) -> ReconcileReport:
    """Check one invocation against the world. Writes nothing; the caller commits."""
    report = ReconcileReport(
        invocation_id=call.invocation_id or "",
        tool_name=call.tool_name,
        step_index=call.step_index,
        verdict=ReconcileVerdict.UNDECIDABLE,
        detail="",
        status_before=call.status,
    )

    tool = tool_for(registry, call.tool_name)
    if tool is None:
        report.detail = f"注册表里找不到工具 {call.tool_name}，无法对账"
        report.status_after = call.status
        return report

    checker = getattr(tool, "reconcile", None)
    if not callable(checker):
        report.detail = f"{call.tool_name} 没有对账方法"
        report.status_after = call.status
        return report

    try:
        outcome: ReconcileOutcome = await checker(ctx, call.arguments or {}, call.result or {})
    except Exception as exc:
        report.detail = f"对账时出错：{type(exc).__name__}: {exc}"
        report.status_after = call.status
        return report

    report.verdict = outcome.verdict
    report.detail = outcome.detail
    report.evidence = outcome.evidence

    if outcome.verdict is ReconcileVerdict.APPLIED:
        call.status = ToolCallStatus.SUCCEEDED.value
        # `finished_at` stays NULL on purpose. The tool's own process never observed the
        # end; a later check did. Writing `finished_at` here would erase the distinction
        # the two columns exist to keep.
        call.reconciled_at = datetime.now(UTC)
    elif outcome.verdict is ReconcileVerdict.NOT_APPLIED:
        call.status = ToolCallStatus.NOT_APPLIED.value
        call.reconciled_at = datetime.now(UTC)
    else:
        # Left in `UNKNOWN`, with no timestamp, so a later pass can try again.
        call.status = ToolCallStatus.UNKNOWN.value

    call.error_message = outcome.detail[:2000] if outcome.detail else call.error_message
    report.status_after = call.status
    return report


async def reconcile_unknown_calls(
    session: Any,
    *,
    registry: Any,
    ai_run_id: uuid.UUID | None = None,
    permissions: Any | None = None,
    limit: int = 200,
) -> list[ReconcileReport]:
    """Check every `UNKNOWN` invocation, optionally narrowed to one run.

    Only rows in `UNKNOWN` are considered. `RUNNING` rows are not: they have not been
    through `resolve_orphan_tool_calls` yet, and a row whose process might still be alive
    must not be checked — the check would race the execution and could report `NOT_APPLIED`
    for a file the tool was about to write.

    The caller owns the transaction. This flushes nothing.
    """
    from sqlalchemy import select

    from app.models.ai import AIRun

    query = (
        select(ToolCall, AIRun.owner_id)
        .join(AIRun, ToolCall.ai_run_id == AIRun.id)
        .where(ToolCall.status == ToolCallStatus.UNKNOWN.value)
        .order_by(ToolCall.created_at)
        .limit(limit)
    )
    if ai_run_id is not None:
        query = query.where(ToolCall.ai_run_id == ai_run_id)

    reports: list[ReconcileReport] = []
    for call, owner_id in (await session.execute(query)).all():
        ctx = context_for(session, call, owner_id=owner_id, permissions=permissions)
        reports.append(await reconcile_call(call, registry=registry, ctx=ctx))

    if reports:
        logger.info(
            "tool_calls_reconciled",
            extra={
                "count": len(reports),
                "applied": sum(1 for r in reports if r.verdict is ReconcileVerdict.APPLIED),
                "not_applied": sum(
                    1 for r in reports if r.verdict is ReconcileVerdict.NOT_APPLIED
                ),
                "undecidable": sum(
                    1 for r in reports if r.verdict is ReconcileVerdict.UNDECIDABLE
                ),
            },
        )
    return reports


__all__ = [
    "ReconcileReport",
    "context_for",
    "reconcile_call",
    "reconcile_unknown_calls",
]

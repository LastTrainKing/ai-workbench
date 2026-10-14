"""Running a BuildRun: from the goal to a registered deliverable.

This is the piece that makes the loop self-driving rather than demonstrable.

Before this module a BuildRun was a record: it held a goal, a status and a place to
attach files. Something else had to attach them. Now the run starts the agent, waits
for it, finds what it actually produced, and registers each artifact with its
verification.

**How the produced files are found.** Not by scanning the workspace for anything
newer than the run's start time — that would pick up whatever else happened to be
touched, including the user's own edits. The `tool_calls` table records every tool
invocation with its arguments, so the answer is exact: the paths that were passed to
`document_write` and `file_write` during this run, that succeeded. A file the agent
claims to have written and a file that exists are two different claims, and this
registers only the ones where both hold.

**Why this is a background task.** An agent run takes minutes. Holding an HTTP request
open for it would time out, and blocking the event loop would freeze every other
request. The run is registered synchronously — so the caller gets an id immediately —
and the work happens on its own session.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent.plan import RunState

# The set lives with the registry that knows which capabilities exist — see the
# note there. Re-exported below so existing importers keep working.
from app.ai.tools.registry import ARTIFACT_TOOLS
from app.core.logging import get_logger
from app.models.ai import ToolAuditLog
from app.models.delivery import (
    BuildRun,
    BuildStatus,
    describe_run_state,
    make_verification,
)
from app.services.delivery_service import DeliveryService

logger = get_logger(__name__)

#: States in which there is nothing left to watch.
#:
#: Taken from the enum rather than written out, because writing them out is how the
#: previous version ended up comparing lowercase strings against uppercase values
#: and never terminating. `RunState` is the source of truth; this derives from it.
TERMINAL_STATES: frozenset[str] = frozenset(
    {
        RunState.COMPLETED.value,
        RunState.FAILED.value,
        RunState.CANCELLED.value,
        RunState.LIMIT_REACHED.value,
        RunState.WAITING_CONFIRMATION.value,
    }
)

#: How long to wait between polls of the agent run.
POLL_SECONDS = 3.0
#: Ceiling on the wait. Beyond this the run is marked LIMIT_REACHED rather than
#: polling forever — a stuck run should surface, not hang.
MAX_WAIT_SECONDS = 30 * 60
#: After the agent reports a terminal state, how long to keep waiting for it to actually
#: finish. The gap is real work — the final report is another model call, and the wrap-up
#: that persists the outcome and releases the employee runs after it — so this is generous
#: on purpose. It is only here so a wrap-up that never completes does not park the run in
#: EXECUTING for the full `MAX_WAIT_SECONDS`.
SETTLE_GRACE_SECONDS = 180.0

#: Strong references to the watcher tasks.
#:
#: The event loop holds only a weak reference to a running task, so a task nobody
#: else references can be garbage-collected mid-flight. For a watcher that means the
#: run silently never reaches a terminal state. Holding them here for the task's
#: lifetime is the documented fix.
_WATCHERS: set[asyncio.Task[None]] = set()

#: Suffix to kind, for registering the right type.
KIND_BY_SUFFIX = {
    ".pptx": "pptx",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pdf": "pdf",
    ".md": "md",
    ".csv": "csv",
    ".txt": "md",
}


def kind_for(path: str) -> str:
    return KIND_BY_SUFFIX.get(Path(path).suffix.lower(), "code")


async def execute_build_run(
    session: AsyncSession,
    *,
    build_run_id: uuid.UUID,
    context: str = "",
) -> dict[str, Any]:
    """Start the agent for a BuildRun and return immediately.

    The run record is updated synchronously so the UI can navigate to it and watch;
    the work itself happens in a background task on its own session.
    """
    from app.services.autonomous_service import start_run

    service = DeliveryService(session)
    run = await session.get(BuildRun, build_run_id)
    if run is None:
        raise ValueError(f"build run not found: {build_run_id}")

    await service.set_status(run, BuildStatus.PLANNING)

    # The goal is passed through unchanged. The plan is a translation of the user's
    # words; rewriting them here would mean the run works on something the user did
    # not ask for.
    # Which employee this run belongs to, if the caller named one. It arrives in the
    # run's `scope` because that is where "what this run is about" is recorded, and it
    # is what narrows the policy the agent runs under — see
    # `autonomous_service._build_agent`. Absent, the run uses the user's own policy,
    # which is exactly what every run did before employees existed.
    employee_id: uuid.UUID | None = None
    raw_employee = (run.scope or {}).get("employee_id")
    if raw_employee:
        try:
            employee_id = uuid.UUID(str(raw_employee))
        except (TypeError, ValueError):
            logger.warning(
                "build_run_employee_id_unreadable",
                extra={"build_run_id": str(run.id), "value": str(raw_employee)[:64]},
            )

    started = await start_run(
        session,
        owner_id=run.user_id,
        goal=run.goal,
        context=context,
        project_id=run.project_id,
        # The format the user asked for travels into the planner, so "make me a PPT"
        # cannot be satisfied with a markdown outline.
        deliverable_kind=run.deliverable_kind,
        employee_id=employee_id,
    )

    # The foreign key wants the row's UUID; the controller wants the short id.
    run.ai_run_id = uuid.UUID(started["db_run_id"])
    await service.set_status(run, BuildStatus.EXECUTING)
    await session.flush()

    logger.info(
        "build_run_started",
        extra={"build_run_id": str(run.id), "run_id": started["run_id"]},
    )

    task = asyncio.create_task(
        _watch(build_run_id, run.user_id, started["run_id"], started["db_run_id"])
    )
    _WATCHERS.add(task)
    task.add_done_callback(_WATCHERS.discard)
    return {"build_run_id": str(run.id), "run_id": started["run_id"], "state": started["state"]}


#: The agent's terminal states, as the executor spells them.
_AGENT_STATE_TO_BUILD_STATUS = {
    "COMPLETED": BuildStatus.COMPLETED,
    "FAILED": BuildStatus.FAILED,
    "CANCELLED": BuildStatus.CANCELLED,
    "LIMIT_REACHED": BuildStatus.LIMIT_REACHED,
    "WAITING_CONFIRMATION": BuildStatus.WAITING_USER,
}


def decide_final_status(
    state: str,
    *,
    unsettled: bool,
    collected: int,
    grace_seconds: int = SETTLE_GRACE_SECONDS,
) -> tuple[BuildStatus, str]:
    """What this run's terminal status is, and the sentence that goes with it.

    **Pulled out of `_watch` so it can be tested without running an agent.** This
    decision is where the product's most expensive mistakes have lived — a run that
    declared success and kept writing was reported COMPLETED, and a run that produced
    nothing was reported COMPLETED too — and a decision table that can only be exercised
    by driving a real model is a decision table that is only ever tested by accident.

    The rules, in order:

    * `unsettled` — the agent set a terminal state and never stopped. Files it wrote are
      real; the run did not close out. Saying COMPLETED would read a verdict off a state
      the run had already moved past.
    * COMPLETED with `collected == 0` — a run whose whole point was a file finished
      without one. Not a completed delivery, and not a failure either.
    * WAITING_USER — every step ran, but nothing objective was proven. Deliberately not
      folded into PARTIAL: that would put a failure in the record that never happened.
    * not COMPLETED but files exist — PARTIAL. The files are real and the run did not
      finish, and both statements are true at once.
    """
    final = _AGENT_STATE_TO_BUILD_STATUS.get(state.upper(), BuildStatus.FAILED)

    if unsettled and final is BuildStatus.COMPLETED:
        return BuildStatus.WAITING_USER, (
            f"运行报告了 {state}，但收尾没有在 {int(grace_seconds)} 秒内完成，"
            "所以这次运行没有得出可确认的结论。已经产出的文件不受影响，可以正常查看。"
        )
    if final is BuildStatus.COMPLETED and collected == 0:
        return BuildStatus.WAITING_USER, (
            "运行结束，但没有检测到产出文件。可能它只是回答了问题，或写到了别的位置。"
        )
    if final is BuildStatus.WAITING_USER:
        return BuildStatus.WAITING_USER, (
            "步骤全部完成，但最终核验没能取得客观证据，所以这次运行没有得出"
            "可验证的结论。产出文件都在，需要你自己看一眼。"
        )
    if final is not BuildStatus.COMPLETED and collected:
        return BuildStatus.PARTIAL, (
            f"运行终态是 {state}，但已产出 {collected} 个文件。"
            "这些文件真实存在、也能读回；没有走完的是这次运行。"
        )
    return final, (f"产出 {collected} 个文件" if collected else "")


async def _watch(
    build_run_id: uuid.UUID,
    owner_id: uuid.UUID,
    controller_id: str,
    db_run_id: str,
) -> None:
    """Wait for the agent to finish, then collect what it produced.

    Runs on its own session: the request that started the run has already returned,
    and reusing its session would mean touching an object another task owns.

    Two ids are needed and they are not interchangeable: `controller_id` addresses
    the live controller, `db_run_id` addresses the row that tool calls hang off.
    Passing the wrong one is a silent failure — the watcher would poll a controller
    that never exists and collect from a run that has no calls.
    """
    from app.infrastructure.database import session_scope
    from app.services.autonomous_service import get_controller

    try:
        deadline = asyncio.get_event_loop().time() + MAX_WAIT_SECONDS
        state = "unknown"
        #: When the run first claimed a terminal state, and whether it ever finished.
        terminal_since = 0.0
        unsettled = False
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(POLL_SECONDS)
            controller = get_controller(controller_id)
            if controller is None:
                # The controller is gone, which means the run ended — or the process
                # restarted. Either way there is nothing left to watch.
                state = "gone"
                break
            state = controller.state.value

            # Mirror the plan's progress onto the run row.
            #
            # The workbench shows a step counter, and without this it reads 0/0 for the
            # whole run — a progress indicator that never moves is worse than none,
            # because it looks like nothing is happening while the agent is working.
            # The controller holds the authoritative plan, so it is read rather than
            # reconstructed from events.
            await _sync_progress(build_run_id, controller)

            if state.upper() not in TERMINAL_STATES:
                continue
            if controller.settled:
                break

            # **Terminal is not finished.** The agent sets its verdict and then keeps
            # going: it snapshots the workspace, generates the final report (another
            # model call), and only afterwards does the service persist the outcome and
            # release the employee. Breaking here on `state` alone is what stamped a
            # build run COMPLETED while `ai_runs.status` was still RUNNING and the
            # employee was still 工作中 — the watcher collected deliverables out of a run
            # that had not stopped writing.
            now = asyncio.get_event_loop().time()
            if terminal_since == 0.0:
                terminal_since = now
            elif now - terminal_since > SETTLE_GRACE_SECONDS:
                unsettled = True
                break

        async with session_scope() as session:
            service = DeliveryService(session)
            run = await session.get(BuildRun, build_run_id)
            if run is None:
                return

            if state == "gone":
                await service.set_status(
                    run, BuildStatus.FAILED, error="运行已结束或服务已重启，无法确认结果"
                )
                return

            # Retried, because the agent's own session commits its audit rows
            # asynchronously and the watcher can reach here first. Reading an
            # empty result once and concluding "it produced nothing" is how a run
            # that wrote a 70 KB deck reported "no output detected".
            #
            # Each attempt re-loads the run rather than reusing the object from
            # before the rollback: `rollback()` expires every instance in the
            # session, and touching an expired attribute performs lazy IO — which in
            # async SQLAlchemy raises `MissingGreenlet` instead of loading. That is
            # exactly how this loop killed the watcher on its second attempt and left
            # the run `EXECUTING` forever.
            collected = 0
            for attempt in range(4):
                collected = await _collect(
                    session,
                    service,
                    build_run_id=build_run_id,
                    ai_run_id=db_run_id,
                )
                if collected:
                    break
                await asyncio.sleep(2 + attempt * 2)
                await session.rollback()

            run = await session.get(BuildRun, build_run_id)
            if run is None:
                return

            # 决定这次运行的终态。规则表在 `decide_final_status` —— 它能被单独测试，
            # 而这一段以前只能靠真的跑一个模型来验证。
            final, summary = decide_final_status(
                state, unsettled=unsettled, collected=collected
            )
            await service.set_status(run, final, summary=summary)

            # Now that the run has its final status, attach it to each deliverable
            # beside — never inside — the file's own verdict.
            await _stamp_run_state(session, build_run_id)

            logger.info(
                "build_run_finished",
                extra={"build_run_id": str(build_run_id), "state": state, "deliverables": collected},
            )
    except Exception as exc:
        # The watcher dying must not strand the run.
        #
        # This used to be a log line and nothing else, and the consequence was a run
        # that stayed `EXECUTING` forever while its agent had already finished — the
        # one state that is indistinguishable from "still working". Whatever went
        # wrong here, the run has to end up in a terminal state that says so.
        reason = f"{type(exc).__name__}: {exc}"
        import traceback

        logger.warning(
            "build_run_watch_failed",
            extra={"build_run_id": str(build_run_id), "error": reason, "traceback": traceback.format_exc()[:2500]},
        )
        try:
            from app.infrastructure.database import session_scope

            async with session_scope() as session:
                run = await session.get(BuildRun, build_run_id)
                if run is not None and run.status not in {
                    status.value for status in BuildStatus if status.is_terminal
                }:
                    run.status = BuildStatus.FAILED.value
                    run.error = f"运行监视失败，结果无法确认：{reason}"[:500]
                    run.result_summary = (
                        "监视这次运行的过程出错了。产出物可能已经在工作区里，但这次运行不能算完成。"
                    )
                    await session.commit()
        except Exception as nested:
            logger.error(
                "build_run_watch_failure_not_recorded",
                extra={"build_run_id": str(build_run_id), "error": f"{type(nested).__name__}: {nested}"},
            )


async def _sync_progress(build_run_id: uuid.UUID, controller: Any) -> None:
    """Copy the plan's step counts onto the run row.

    Best-effort: a failure here must not stop the watcher, because the run finishing
    matters more than the progress bar being current.
    """
    from app.infrastructure.database import session_scope

    plan = getattr(controller, "plan", None)
    if plan is None:
        return
    steps = getattr(plan, "steps", None) or []
    total = len(steps)
    if total == 0:
        return

    # Persist the plan itself, not just its size.
    #
    # The workbench's first column lists the steps with their verification criteria,
    # and without this it has a heading and nothing under it — the plan lived only in
    # the in-process controller. Serialised through the plan's own `as_dict` so the
    # stored shape is the same one the API already returns.
    try:
        plan_payload = plan.as_dict() if hasattr(plan, "as_dict") else {}
    except Exception:
        plan_payload = {}

    # A step counts as done once the plan says so; the controller tracks this per
    # step, and falling back to the state keeps a plan with no statuses honest.
    done = sum(
        1
        for step in steps
        if str(getattr(step, "status", "")).lower() in ("done", "completed", "succeeded", "skipped")
    )

    try:
        async with session_scope() as session:
            run = await session.get(BuildRun, build_run_id)
            if run is None:
                return
            changed = False
            if run.steps_total != total or run.steps_done != done:
                run.steps_total = total
                run.steps_done = done
                changed = True
            if plan_payload and run.plan != plan_payload:
                run.plan = plan_payload
                changed = True
            if changed:
                await session.flush()
    except Exception as exc:
        logger.debug("build_run_progress_sync_failed", extra={"error": str(exc)})


async def _collect(
    session: AsyncSession,
    service: DeliveryService,
    *,
    build_run_id: uuid.UUID,
    ai_run_id: str,
) -> int:
    """Register every file the run actually wrote, with verification.

    Reads the tool-call log rather than the filesystem. A directory scan would find
    files this run did not produce — the user's own edits, another run's output —
    and a file the agent *said* it wrote is not evidence that it did. The
    intersection is: a successful call to a writing tool, naming a path that exists.

    The run is loaded **here**, from its id, rather than being passed in. The caller
    retries this after a `rollback()`, and a `rollback()` expires every instance in
    the session — so a `run` object captured before it is a trap: reading any of its
    attributes afterwards performs lazy IO, and lazy IO in async SQLAlchemy raises
    `MissingGreenlet`. Taking the id instead makes that impossible.
    """
    run = await session.get(BuildRun, build_run_id)
    if run is None:
        logger.warning("build_run_missing_at_collect", extra={"build_run_id": str(build_run_id)})
        return 0

    # Read the audit log, not `tool_calls`.
    #
    # Both tables exist and only one of them is written for agent tool executions:
    # the executor records an audit row for every invocation, while `tool_calls` is
    # written by the AI tracing recorder, which the autonomous path does not call.
    # Reading the wrong one produced a run that had plainly succeeded — the file was
    # on disk — and reported "no output detected".
    try:
        run_uuid = uuid.UUID(str(ai_run_id))
    except ValueError:
        logger.warning("build_run_bad_id", extra={"ai_run_id": ai_run_id})
        return 0

    # The preferences that were in play, copied from the plan so the deliverable
    # itself can answer "was my preference applied to this file?".
    plan = run.plan if isinstance(run.plan, dict) else {}
    applied = {
        "hard_constraints": plan.get("hard_constraints") or {},
        "preference_outcomes": plan.get("preference_outcomes") or [],
    }

    stmt = (
        select(ToolAuditLog)
        .where(ToolAuditLog.ai_run_id == run_uuid)
        .order_by(ToolAuditLog.created_at.asc())
    )
    calls = list((await session.execute(stmt)).scalars())
    if not calls:
        logger.info("build_run_no_tool_calls", extra={"ai_run_id": ai_run_id})

    seen: set[str] = set()
    registered = 0
    for call in calls:
        if call.tool_name not in ARTIFACT_TOOLS:
            continue
        # A denied call left no file behind, and neither did one that errored.
        if (call.decision or "").lower() != "allow":
            continue
        if (call.status or "").upper() not in ("SUCCEEDED", "OK"):
            continue

        arguments = call.arguments if isinstance(call.arguments, dict) else {}
        # `arguments` holds what the model passed; `target` holds what the tool
        # resolved it to. Preferring the argument and ignoring `target` meant the
        # collector saw nothing, because the audit row's path lives in `target`.
        raw = arguments.get("path") or getattr(call, "target", "") or ""
        if not raw:
            continue

        target = Path(str(raw))
        if not target.is_absolute():
            # Tool arguments are relative to the workspace, which is where they were
            # resolved from in the first place.
            try:
                from app.ai.tools.permissions import default_workspace

                target = default_workspace() / target
            except Exception:
                continue

        target = target.resolve(strict=False)
        if str(target) in seen:
            continue
        seen.add(str(target))

        if not target.is_file():
            # The call succeeded but the file is not there now. Worth knowing about
            # rather than skipping silently.
            logger.warning(
                "build_run_artifact_missing",
                extra={"path": str(target), "ai_run_id": ai_run_id},
            )
            continue

        try:
            item = await service.record_deliverable(
                run=run,
                path=target,
                kind=kind_for(str(target)),
                title=target.stem,
                style=str(arguments.get("style") or ""),
                applied_preferences=applied,
            )
            # File-level verification only. `verify_deliverable` already sets
            # `item.verified` from the checks it runs — exists, reads back, has the
            # page count it claims.
            #
            # The run's completeness is deliberately *not* mixed in here. It used to
            # be, as an extra `run_state` check with `ok: False`, which forced
            # `passed` false and stamped a perfectly readable file as 「未校验」. A
            # run that ends PARTIAL is a fact about the process, not evidence about
            # the artifact, so it does not get a vote on the artifact's verdict.
            #
            # It is recorded — by `_stamp_run_state`, after the run's final status is
            # decided. Not here: at this moment the row still says EXECUTING, because
            # collection runs before the status mapping below.
            await service.verify_deliverable(owner_id=run.user_id, deliverable_id=item.id)
            registered += 1
        except Exception as exc:
            logger.warning(
                "build_run_artifact_failed",
                extra={"path": str(target), "error": f"{type(exc).__name__}: {exc}"},
            )

    return registered


async def _stamp_run_state(session: AsyncSession, build_run_id: uuid.UUID) -> None:
    """Record the run's final state on each of its deliverables.

    **Separate from `_collect` on purpose.** Collection happens before the run's
    final status is decided — at that moment the row still says `EXECUTING`, so a
    stamp taken there would report "still running" for a run that has already
    ended. The authoritative value only exists after `set_status`, which is why
    this runs afterwards.

    The file's own verdict is preserved exactly: `checks` are carried over
    unchanged and `passed` is recomputed from them alone. The run's state travels
    in `run_state`, a sibling field. That separation is the whole point — see
    `make_verification`.

    Best-effort by design: a failure here leaves the deliverables with their
    file-level verdict intact, which is the part that must not be lost. It is
    logged rather than swallowed silently.
    """
    from app.models.delivery import Deliverable

    try:
        run = await session.get(BuildRun, build_run_id)
        if run is None:
            logger.warning("run_state_stamp_missing_run", extra={"build_run_id": str(build_run_id)})
            return
        note = describe_run_state(run.status)
        rows = list(
            (
                await session.execute(
                    select(Deliverable).where(Deliverable.build_run_id == build_run_id)
                )
            ).scalars()
        )
        for item in rows:
            existing = item.verification if isinstance(item.verification, dict) else {}
            item.verification = make_verification(
                list(existing.get("checks") or []), run_state=note
            )
            # Unchanged rule: only the file-level checks decide this.
            item.verified = bool(item.verification["passed"])
        if rows:
            await session.flush()
    except Exception as exc:
        logger.warning(
            "run_state_stamp_failed",
            extra={"build_run_id": str(build_run_id), "error": f"{type(exc).__name__}: {exc}"},
        )


__all__ = ["ARTIFACT_TOOLS", "execute_build_run", "kind_for"]

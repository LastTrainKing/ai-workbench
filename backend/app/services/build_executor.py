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
from app.core.logging import get_logger
from app.models.ai import ToolAuditLog
from app.models.delivery import BuildRun, BuildStatus
from app.services.delivery_service import DeliveryService

logger = get_logger(__name__)

#: Tools whose successful invocation means a file now exists at a known path.
ARTIFACT_TOOLS = {"document_write", "file_write"}

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
    started = await start_run(
        session,
        owner_id=run.user_id,
        goal=run.goal,
        context=context,
        project_id=run.project_id,
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

            if state.upper() in TERMINAL_STATES:
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

            collected = await _collect(session, service, run=run, ai_run_id=db_run_id)

            mapping = {
                "COMPLETED": BuildStatus.COMPLETED,
                "FAILED": BuildStatus.FAILED,
                "CANCELLED": BuildStatus.CANCELLED,
                "LIMIT_REACHED": BuildStatus.LIMIT_REACHED,
                "WAITING_CONFIRMATION": BuildStatus.WAITING_USER,
            }
            final = mapping.get(state.upper(), BuildStatus.FAILED)

            if final is BuildStatus.COMPLETED and collected == 0:
                # Finishing without producing a file is not a completed delivery for a
                # run that asked for one. Saying so is more useful than reporting success
                # for a task whose whole point was the artifact.
                final = BuildStatus.WAITING_USER
                await service.set_status(
                    run,
                    final,
                    summary="运行结束，但没有检测到产出文件。可能它只是回答了问题，或写到了别的位置。",
                )
            else:
                await service.set_status(
                    run,
                    final,
                    summary=f"产出 {collected} 个文件" if collected else "",
                )
            logger.info(
                "build_run_finished",
                extra={"build_run_id": str(build_run_id), "state": state, "deliverables": collected},
            )
    except Exception as exc:
        logger.warning(
            "build_run_watch_failed",
            extra={"build_run_id": str(build_run_id), "error": f"{type(exc).__name__}: {exc}"},
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
    run: BuildRun,
    ai_run_id: str,
) -> int:
    """Register every file the run actually wrote, with verification.

    Reads the tool-call log rather than the filesystem. A directory scan would find
    files this run did not produce — the user's own edits, another run's output —
    and a file the agent *said* it wrote is not evidence that it did. The
    intersection is: a successful call to a writing tool, naming a path that exists.
    """
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
            )
            await service.verify_deliverable(owner_id=run.user_id, deliverable_id=item.id)
            registered += 1
        except Exception as exc:
            logger.warning(
                "build_run_artifact_failed",
                extra={"path": str(target), "error": f"{type(exc).__name__}: {exc}"},
            )

    return registered


__all__ = ["ARTIFACT_TOOLS", "execute_build_run", "kind_for"]

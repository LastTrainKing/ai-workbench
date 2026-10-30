"""Autonomous run control and workspace snapshots.

Split from ``agent.py`` on purpose: that module answers "what is the agent allowed
to do", this one answers "what is it doing right now, and can I stop or undo it".
They have different lifetimes too — permissions are configuration, runs are live
processes.

The design constraint that shapes everything here: a run outlives the request that
started it. ``POST /agent/runs`` returns a ``run_id`` immediately and the work
continues on a background task, which is what makes pause, resume and cancel
reachable at all. A synchronous endpoint would leave the user with no way to
interrupt a run that had gone wrong.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession
from app.core.errors import RunNotFoundError, ValidationError
from app.core.response import ok

router = APIRouter(prefix="/agent", tags=["agent-runs"])


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class RunRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=4000)
    context: str = Field(default="", max_length=8000)
    project_id: uuid.UUID | None = None


class RollbackRequest(BaseModel):
    force: bool = Field(
        default=False,
        description="Restore even if files changed after the run. Discards those changes.",
    )


class SnapshotRequest(BaseModel):
    label: str = Field(default="", max_length=200)
    path: str = Field(default="", description="Defaults to the agent workspace")


# --------------------------------------------------------------------------- #
# Ownership
# --------------------------------------------------------------------------- #
def _owned_controller(run_id: str, user: CurrentUser) -> Any:
    """The live controller for ``run_id``, but only if it is this user's run.

    The registry is process-wide and in-memory, so every by-id endpoint here was
    reachable by anyone who knew or guessed a run id: read its plan, stream its
    events, pause it, cancel it, or roll its workspace back. `user` was already a
    parameter on all of them and was never used.

    Not-found and not-yours are deliberately the same error. Distinguishing them
    would confirm that a run id exists to a caller who has no right to know.
    """
    from app.services.autonomous_service import get_controller

    controller = get_controller(run_id)
    if controller is None or controller.owner_id != user.id:
        # 404 with its own code. Not `ValidationError`: that is 422 with
        # `code="validation_error"`, which is also what FastAPI returns for a malformed
        # request — so "not yours" and "your request was broken" were indistinguishable.
        raise RunNotFoundError("该运行不在本进程中（可能已结束、服务已重启，或不属于当前账号）")
    return controller


async def _owned_run(run_id: str, user: CurrentUser, session: DbSession) -> dict[str, Any]:
    """A run's state — live if this process has it, stored otherwise — for this user only.

    Two sources, one ownership rule. The registry knows about a run while it is running;
    the table knows about it afterwards, and after a restart it is the only source there
    is. Both are filtered by the same owner, and "not found" and "not yours" stay the same
    answer so a caller with no right to know cannot use this to learn that a run id exists.
    """
    from app.services.autonomous_service import get_controller, history_row_for

    controller = get_controller(run_id)
    if controller is not None and controller.owner_id == user.id:
        return {**controller.as_dict(), "live": True}

    row = await history_row_for(session, run_id, user.id)
    if row is None:
        # Same answer for "no such run" and "not yours" — see `RunNotFoundError`.
        raise RunNotFoundError("找不到该运行记录")
    return row


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
@router.post("/runs", summary="Start an autonomous run and return immediately")
async def start_autonomous_run(
    payload: RunRequest, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    """Begin a run and hand back its id.

    Returns as soon as the run is registered rather than when it finishes: a run
    can take minutes, and an HTTP request held open for that long cannot be
    interrupted.
    """
    from app.services.autonomous_service import start_run

    started = await start_run(
        session,
        owner_id=user.id,
        goal=payload.goal.strip(),
        context=payload.context,
        project_id=payload.project_id,
    )
    return ok(started, message="任务已开始执行")


@router.get("/runs", summary="Runs in flight now, merged with the run history")
async def list_runs(
    user: CurrentUser,
    session: DbSession,
    limit: int = 50,
    offset: int = 0,
    status_filter: str | None = None,
    live_only: bool = False,
) -> dict[str, Any]:
    """Live state and history in one list, one entry per run.

    This endpoint used to answer from the process-local registry alone. After a restart
    the registry is empty, so every run the user had ever started vanished from here
    while the rows sat in ``agent_runs`` — the database had the history and the API
    returned nothing.

    Now the database is the list and the registry is an overlay: a run that is running
    right now appears once, with its live state, and everything else appears from its row.
    ``live`` on each entry says which it is. Restart the process and the history is still
    here, because it was never the registry's to hold.
    """
    from app.services.autonomous_service import active_runs, merge_live_and_history, run_history

    # `user` is not decorative. The registry holds every run in the process, so an
    # unfiltered listing showed one account the runs another account had started.
    live = active_runs(user.id)

    if live_only:
        runs = merge_live_and_history(live, [])
        return ok({"runs": runs, "count": len(runs), "total": len(runs), "live_count": len(runs)})

    history, total = await run_history(
        session, user.id, limit=limit, offset=offset, status=status_filter
    )
    runs = merge_live_and_history(live, history)
    return ok(
        {
            "runs": runs,
            "count": len(runs),
            "total": total,
            "live_count": sum(1 for entry in runs if entry.get("live")),
            "limit": limit,
            "offset": offset,
        }
    )


@router.get("/runs/{run_id}", summary="Live state, or the stored record once the run is over")
async def get_run_state(run_id: str, user: CurrentUser, session: DbSession) -> dict[str, Any]:
    """A run's state, whether it is running now or finished before the last restart.

    Reading only the registry meant this returned an error for every run older than the
    current process — including runs the user could see in their own history. The live
    controller is still preferred when it exists, because it knows things the row does not
    (current tool, elapsed time); the row is the fallback, not a replacement.

    Ownership is checked either way — `_owned_run` applies the same rule to both sources.
    """
    return ok(await _owned_run(run_id, user, session))


@router.get("/runs/{run_id}/events", summary="Incremental event stream")
async def get_run_events(run_id: str, user: CurrentUser, since: int = 0) -> dict[str, Any]:
    """Events after ``since``, so the UI can poll without re-rendering history."""
    controller = _owned_controller(run_id, user)
    events = controller.events_since(since)
    return ok(
        {
            "events": events,
            "count": len(events),
            "last_seq": events[-1]["seq"] if events else since,
            "state": controller.state.value,
            "finished": controller.state.terminal,
        }
    )


@router.get("/runs/{run_id}/plan", summary="The execution plan")
async def get_run_plan(run_id: str, user: CurrentUser) -> dict[str, Any]:
    controller = _owned_controller(run_id, user)
    if controller.plan is None:
        return ok({"available": False, "message": "计划尚未生成"})
    return ok({"available": True, **controller.plan.as_dict()})


@router.post("/runs/{run_id}/pause", summary="Pause at the next checkpoint")
async def pause_run(run_id: str, user: CurrentUser) -> dict[str, Any]:
    """Pauses between steps, not mid-tool.

    Interrupting a file write halfway would leave the workspace inconsistent,
    which is worse than waiting for the current step to finish.
    """
    from app.services.autonomous_service import control

    _owned_controller(run_id, user)
    result = control(run_id, "pause")
    if not result["ok"]:
        # A control that cannot be applied *is* a request-level problem, so this stays a
        # ValidationError — the ownership check above is what must not be one.
        raise ValidationError(result["message"])
    return ok(result, message="已暂停")


@router.post("/runs/{run_id}/resume", summary="Resume a paused run")
async def resume_run(run_id: str, user: CurrentUser) -> dict[str, Any]:
    from app.services.autonomous_service import control

    _owned_controller(run_id, user)
    result = control(run_id, "resume")
    if not result["ok"]:
        raise ValidationError(result["message"])
    return ok(result, message="已恢复")


@router.post("/runs/{run_id}/cancel", summary="Cancel and stop the current tool")
async def cancel_run(run_id: str, user: CurrentUser) -> dict[str, Any]:
    """Cancellation reaches into the running task.

    The controller cancels the asyncio task, which propagates into the process
    runner and kills the child process tree — a cancelled build does not keep
    compiling in the background.
    """
    from app.services.autonomous_service import control

    _owned_controller(run_id, user)
    result = control(run_id, "cancel")
    if not result["ok"]:
        raise ValidationError(result["message"])
    return ok(result, message="已取消")


@router.get("/runs/{run_id}/diff", summary="What the run changed in the workspace")
async def get_run_diff(run_id: str, user: CurrentUser, unified: bool = False) -> dict[str, Any]:
    from app.services.autonomous_service import diff_for_run

    _owned_controller(run_id, user)
    return ok(await diff_for_run(run_id, unified=unified))


# --------------------------------------------------------------------------- #
# Unresolved tool calls
# --------------------------------------------------------------------------- #
async def _owned_airun(run_id: str, user: CurrentUser, session: DbSession) -> Any:
    """The stored run row, for this user only.

    Deliberately not `_owned_controller`: the calls that need reconciling belong to runs
    that are **over** — the process that drove them died. A registry lookup would find
    nothing for exactly the runs this endpoint exists for.
    """
    from sqlalchemy import select

    from app.models.ai import AIRun

    row = (
        await session.execute(
            select(AIRun).where(AIRun.run_id == run_id, AIRun.owner_id == user.id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise RunNotFoundError("找不到该运行记录")
    return row


@router.get(
    "/runs/{run_id}/unresolved",
    summary="Tool calls whose side effect could not be confirmed",
)
async def list_unresolved_calls(
    run_id: str, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    """The invocations an interruption left undecided, and what a check concluded.

    Only calls that **can change something** are listed. A read-only call is also left in
    `UNKNOWN` — nobody observed whether it ran — but nothing can have been harmed by it, and
    a list that mixes "we do not know, and it does not matter" with "we do not know, and it
    might have written a file" is a list nobody reads.

    `replay_allowed` is reported rather than left implicit. It is `false`, always: this
    system does not re-run an invocation whose outcome it could not observe. Saying so in
    the payload is the difference between a UI that has to guess and one that can state it.
    """
    from sqlalchemy import select

    from app.domain.enums import ToolCallStatus
    from app.models.ai import ToolCall

    run = await _owned_airun(run_id, user, session)
    rows = list(
        (
            await session.execute(
                select(ToolCall)
                .where(
                    ToolCall.ai_run_id == run.id,
                    ToolCall.status == ToolCallStatus.UNKNOWN.value,
                )
                .order_by(ToolCall.created_at)
            )
        ).scalars()
    )
    return ok(
        {
            "items": [
                {
                    "invocation_id": call.invocation_id,
                    "tool_name": call.tool_name,
                    "step_index": call.step_index,
                    "arguments": call.arguments,
                    "mutates_state": call.mutates_state,
                    "working_directory": call.working_directory,
                    "started_at": call.started_at.isoformat() if call.started_at else "",
                    "note": call.error_message,
                }
                for call in rows
                if call.mutates_state
            ],
            "undecided": sum(1 for call in rows if call.mutates_state),
            "replay_allowed": False,
        }
    )


@router.post(
    "/runs/{run_id}/reconcile",
    summary="Check the world and settle what each interrupted call did",
)
async def reconcile_run_calls(run_id: str, user: CurrentUser, session: DbSession) -> dict[str, Any]:
    """Look at the targets and record what can be established.

    Three verdicts per invocation — `APPLIED`, `NOT_APPLIED`, `UNDECIDABLE` — with the
    evidence that produced each. **Nothing is re-run**, and the endpoint cannot be asked to
    re-run anything: there is no replay path. A verdict of `NOT_APPLIED` is the closest
    thing to permission to try again, and acting on it is a decision for a person or for a
    fresh run that will record its own invocations.
    """
    from app.ai.tools.permissions import AgentPolicy
    from app.ai.tools.reconcile import reconcile_unknown_calls
    from app.ai.tools.registry import build_computer_registry

    run = await _owned_airun(run_id, user, session)
    registry = build_computer_registry(AgentPolicy.from_settings(mode="autonomous", level=3))
    reports = await reconcile_unknown_calls(session, registry=registry, ai_run_id=run.id)
    await session.commit()

    items = [
        report.to_payload() for report in reports if report.status_before != report.status_after
    ]
    return ok(
        {
            "checked": len(reports),
            "decided": len(items),
            "items": [report.to_payload() for report in reports],
            "changed": items,
            "replay_allowed": False,
        }
    )


@router.post("/runs/{run_id}/rollback", summary="Restore the workspace to before the run")
async def rollback_run_endpoint(
    run_id: str, payload: RollbackRequest, user: CurrentUser
) -> dict[str, Any]:
    """Refuses to clobber edits made after the run unless ``force`` is set."""
    from app.services.autonomous_service import rollback_run

    _owned_controller(run_id, user)
    result = await rollback_run(run_id, force=payload.force)
    return ok(result, message=result.get("message", ""))


# --------------------------------------------------------------------------- #
# Snapshots
# --------------------------------------------------------------------------- #
@router.post("/snapshots", summary="Take a workspace snapshot now")
async def create_snapshot(payload: SnapshotRequest, user: CurrentUser) -> dict[str, Any]:
    from app.ai.agent.snapshot import get_snapshot_manager
    from app.ai.tools.permissions import default_workspace

    manager = get_snapshot_manager()
    root = payload.path.strip() or str(default_workspace())
    snapshot = await asyncio.to_thread(manager.create, root, label=payload.label)
    return ok(snapshot.as_dict(), message=f"已创建快照 {snapshot.id}")


@router.get("/snapshots", summary="List snapshots")
async def list_snapshots(user: CurrentUser) -> dict[str, Any]:
    from app.ai.agent.snapshot import get_snapshot_manager

    snapshots = get_snapshot_manager().list()
    return ok({"snapshots": [s.as_dict() for s in snapshots], "count": len(snapshots)})


@router.get("/snapshots/{snapshot_id}", summary="One snapshot, with its file list")
async def get_snapshot(snapshot_id: str, user: CurrentUser) -> dict[str, Any]:
    from app.ai.agent.snapshot import get_snapshot_manager

    snapshot = get_snapshot_manager().get(snapshot_id)
    if snapshot is None:
        raise ValidationError("快照不存在")
    return ok(snapshot.as_dict(include_files=True))


@router.get("/snapshots/{snapshot_id}/diff", summary="Diff a snapshot against the workspace")
async def diff_snapshot(
    snapshot_id: str, user: CurrentUser, unified: bool = False
) -> dict[str, Any]:
    from app.ai.agent.snapshot import get_snapshot_manager

    manager = get_snapshot_manager()
    snapshot = manager.get(snapshot_id)
    if snapshot is None:
        raise ValidationError("快照不存在")
    return ok(await asyncio.to_thread(manager.diff, snapshot, unified=unified))


@router.post("/snapshots/{snapshot_id}/restore", summary="Restore a snapshot")
async def restore_snapshot(
    snapshot_id: str, payload: RollbackRequest, user: CurrentUser
) -> dict[str, Any]:
    from app.ai.agent.snapshot import get_snapshot_manager

    manager = get_snapshot_manager()
    snapshot = manager.get(snapshot_id)
    if snapshot is None:
        raise ValidationError("快照不存在")
    result = await asyncio.to_thread(manager.restore, snapshot, force=payload.force)
    return ok(result, message=result.get("message", ""))


@router.delete("/snapshots/{snapshot_id}", summary="Delete a snapshot")
async def delete_snapshot(snapshot_id: str, user: CurrentUser) -> dict[str, Any]:
    from app.ai.agent.snapshot import get_snapshot_manager

    if not get_snapshot_manager().delete(snapshot_id):
        raise ValidationError("快照不存在")
    return ok({"deleted": snapshot_id}, message="已删除")

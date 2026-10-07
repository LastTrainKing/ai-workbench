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
from app.core.errors import ValidationError
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
        raise ValidationError("该运行不在本进程中（可能已结束或服务已重启）")
    return controller


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


@router.get("/runs", summary="Runs currently held by this process")
async def list_active_runs(user: CurrentUser) -> dict[str, Any]:
    from app.services.autonomous_service import active_runs

    # `user` is not decorative here. The registry holds every run in the process,
    # so an unfiltered listing showed one account the runs another account had
    # started — a brand-new user saw 14 runs that were not theirs.
    runs = active_runs(user.id)
    return ok({"runs": runs, "count": len(runs)})


@router.get("/runs/{run_id}", summary="Live state, plan and counters")
async def get_run_state(run_id: str, user: CurrentUser) -> dict[str, Any]:
    controller = _owned_controller(run_id, user)
    return ok(controller.as_dict())


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

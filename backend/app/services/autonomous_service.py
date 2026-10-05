"""Starting and controlling autonomous agent runs.

A run outlives the HTTP request that started it. The endpoint returns as soon as
the run is registered, and the work continues on a background task with its own
database session — the request's session is closed the moment the response is
sent, so sharing it would produce "session is closed" errors partway through a
long run.

That separation is also what makes pause, resume and cancel meaningful: the
controller is reachable from any later request, and cancelling actually reaches
into the running task instead of setting a flag nobody reads.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent.autonomous import AutonomousAgent
from app.ai.agent.control import RunCancelledError, RunController, get_run_registry
from app.ai.agent.plan import RunState
from app.ai.agent.snapshot import get_snapshot_manager
from app.ai.providers.factory import build_llm_provider
from app.ai.tools.audit import make_audit_sink
from app.ai.tools.confirmation import get_broker
from app.ai.tools.executor import ResourceLimits, ToolExecutor
from app.ai.tools.policy_store import build_policy, capability_flags
from app.ai.tools.registry import build_computer_registry
from app.ai.tools.unified import UnifiedToolRegistry
from app.ai.tracing import RunRecorder
from app.core.logging import get_logger
from app.domain.enums import RunStatus, RunType
from app.infrastructure.database import session_scope

logger = get_logger(__name__)

#: Background tasks, kept referenced so the event loop does not garbage-collect
#: a running task mid-flight.
_tasks: dict[str, asyncio.Task[Any]] = {}


def _build_agent(session: AsyncSession, owner_id: uuid.UUID, controller: RunController) -> AutonomousAgent:
    """Assemble an autonomous agent for one run."""
    policy = build_policy()
    policy.workspace.mkdir(parents=True, exist_ok=True)

    business = __import__("app.ai.agent.tools", fromlist=["get_tool_registry"]).get_tool_registry()
    computer = build_computer_registry(policy, enabled=capability_flags())
    limits = ResourceLimits(
        max_output_bytes=policy.max_output_bytes,
        max_files_modified=policy.max_files_modified,
        max_tool_calls=policy.max_tool_calls,
        max_runtime_seconds=policy.max_runtime_seconds,
    )
    executor = ToolExecutor(policy, audit=make_audit_sink(session))
    registry = UnifiedToolRegistry(business, computer, executor, confirm=get_broker().request, limits=limits)
    llm, _ = build_llm_provider()

    return AutonomousAgent(
        llm=llm,
        registry=registry,
        policy=policy,
        controller=controller,
        session=session,
        owner_id=owner_id,
        limits=limits,
        working_directory=str(policy.workspace),
    )


async def _execute_run(
    run_id: str,
    db_run_id: uuid.UUID,
    goal: str,
    context: str,
    owner_id: uuid.UUID,
    project_id: uuid.UUID | None,
    controller: RunController,
) -> None:
    """The background half: run the agent and persist the outcome."""
    try:
        async with session_scope() as session:
            agent = _build_agent(session, owner_id, controller)
            result = await agent.run(goal, context=context)

            # Persist the plan, the event stream and the outcome onto the AI run,
            # so the run can be replayed after the process restarts.
            #
            # Wrapped separately on purpose: the agent has already done the work
            # by this point, and a bookkeeping failure must not turn a completed
            # run into a failed one. That is exactly what happened when this
            # parsed the display id ("run_ab12...") as a UUID and threw.
            try:
                await _persist_run(session, db_run_id, result)
            except Exception as exc:
                logger.error(
                    "autonomous_run_persist_failed",
                    extra={"run_id": run_id, "error": f"{type(exc).__name__}: {exc}"},
                )
            await session.commit()

            # Emitted after persistence so a replay finds the same record, and
            # outside the persistence try-block so a bookkeeping failure still
            # reports the agent's actual outcome to the UI.
            controller.emit(
                "result",
                message=f"运行结束：{result.state.value}",
                data=result.as_dict(),
            )
    except RunCancelledError:
        controller.emit("result", message="运行已取消", data={"state": RunState.CANCELLED.value})
    except Exception as exc:
        logger.error(
            "autonomous_run_failed",
            extra={"run_id": run_id, "error": f"{type(exc).__name__}: {exc}"},
        )
        controller.set_state(RunState.FAILED, f"{type(exc).__name__}: {exc}")
        controller.emit("error", message=f"{type(exc).__name__}: {exc}")
    finally:
        _tasks.pop(run_id, None)


async def _persist_run(session: AsyncSession, db_run_id: uuid.UUID, result: Any) -> None:
    """Write the plan, counters and outcome onto the AI run record.

    Takes the row's UUID. The controller's run id is a display string
    (``run_ab12...``) and is *not* a UUID — parsing it was a real bug that turned
    every completed run into a failure.
    """
    # `agent_run` is a lazy relationship. Touching it from async code without
    # eager-loading makes SQLAlchemy attempt blocking IO inside the event loop and
    # raise MissingGreenlet — which silently skipped the whole write and left
    # every completed run without a result. Load it up front instead.
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models.ai import AIRun

    stmt = select(AIRun).where(AIRun.id == db_run_id).options(selectinload(AIRun.agent_run))
    run = (await session.execute(stmt)).scalar_one_or_none()
    if run is None:
        logger.warning("autonomous_run_row_missing", extra={"run_id": str(db_run_id)})
        return

    run.status = (
        RunStatus.SUCCEEDED
        if result.state is RunState.COMPLETED
        else RunStatus.FAILED
        if result.state is RunState.FAILED
        else RunStatus.CANCELLED
    )
    run.response_text = result.answer
    run.error_message = result.error
    if run.agent_run is not None:
        run.agent_run.steps_used = result.steps_used
        run.agent_run.scratchpad = {
            "plan": result.plan.as_dict() if result.plan else None,
            "state": result.state.value,
            "verified": result.verified,
            "snapshot_id": result.snapshot_id,
            "retries": result.retries,
            "files_modified": result.files_modified,
            "verification_evidence": result.verification_evidence,
        }


async def start_run(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    goal: str,
    context: str = "",
    project_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Register the run, create the recorder, and launch the background task."""
    policy = build_policy()

    # The run record is created here, on the request's session, so the caller can
    # query it immediately; the work itself uses its own session.
    recorder = await RunRecorder.start(
        session,
        run_type=RunType.AGENT,
        owner_id=owner_id,
        provider="autonomous",
        model="",
        prompt_name="agent_planner",
        prompt_version=1,
        request_payload={"goal": goal, "mode": "autonomous", "level": int(policy.level)},
        project_id=project_id,
    )
    await recorder.attach_agent_run(goal=goal, max_steps=policy.max_tool_calls)

    controller = get_run_registry().create(recorder.run_id)
    controller.emit("state", message="任务已接收", state=RunState.PLANNING.value)

    task = asyncio.create_task(
        _execute_run(
            recorder.run_id,
            recorder.model_row.id,
            goal,
            context,
            owner_id,
            project_id,
            controller,
        )
    )
    _tasks[recorder.run_id] = task

    return {
        "run_id": recorder.run_id,
        "state": controller.state.value,
        "goal": goal,
        "policy": {"mode": policy.mode.value, "level": int(policy.level)},
    }


def get_controller(run_id: str) -> RunController | None:
    return get_run_registry().get(run_id)


def control(run_id: str, action: str) -> dict[str, Any]:
    """Pause, resume or cancel a run."""
    controller = get_run_registry().get(run_id)
    if controller is None:
        return {"ok": False, "message": "该运行不在本进程中（可能已结束或服务已重启）"}

    handlers = {
        "pause": controller.pause,
        "resume": controller.resume,
        "cancel": controller.cancel,
    }
    handler = handlers.get(action)
    if handler is None:
        return {"ok": False, "message": f"未知操作：{action}"}

    changed = handler()
    return {
        "ok": changed,
        "action": action,
        "state": controller.state.value,
        "message": "" if changed else f"当前状态 {controller.state.value} 下无法执行该操作",
    }


def snapshot_view(run_id: str) -> dict[str, Any] | None:
    controller = get_run_registry().get(run_id)
    if controller and controller.plan:
        return {"plan": controller.plan.as_dict(), "state": controller.state.value}
    return None


async def diff_for_run(run_id: str, *, unified: bool = False) -> dict[str, Any]:
    """Diff the workspace against the snapshot taken when the run started."""
    controller = get_run_registry().get(run_id)
    snapshot_id = ""
    if controller:
        for event in reversed(controller.events):
            if "snapshot_id" in (event.data or {}):
                snapshot_id = event.data["snapshot_id"]
                break

    manager = get_snapshot_manager()
    if not snapshot_id:
        # Fall back to the most recent snapshot, which is what a user asking about
        # "the last run" means.
        snapshots = manager.list()
        if not snapshots:
            return {"available": False, "message": "还没有任何快照", "files": [], "summary": {}}
        snapshot = snapshots[0]
    else:
        snapshot = manager.get(snapshot_id)
        if snapshot is None:
            return {"available": False, "message": "快照已被删除", "files": [], "summary": {}}

    result = await asyncio.to_thread(manager.diff, snapshot, unified=unified)
    return {"available": True, "run_id": run_id, **result}


async def rollback_run(run_id: str, *, force: bool = False) -> dict[str, Any]:
    """Restore the workspace to the state before the run."""
    controller = get_run_registry().get(run_id)
    snapshot_id = ""
    if controller:
        for event in reversed(controller.events):
            if "snapshot_id" in (event.data or {}):
                snapshot_id = event.data["snapshot_id"]
                break

    manager = get_snapshot_manager()
    snapshot = manager.get(snapshot_id) if snapshot_id else None
    if snapshot is None:
        snapshots = manager.list()
        if not snapshots:
            return {"restored": False, "message": "没有可用于回滚的快照"}
        snapshot = snapshots[0]

    return await asyncio.to_thread(manager.restore, snapshot, force=force)


def active_runs() -> list[dict[str, Any]]:
    return [c.as_dict() for c in get_run_registry().all()]


__all__ = [
    "active_runs",
    "control",
    "diff_for_run",
    "get_controller",
    "rollback_run",
    "snapshot_view",
    "start_run",
]

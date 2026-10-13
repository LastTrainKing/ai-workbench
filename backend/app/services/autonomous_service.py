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
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent.autonomous import AutonomousAgent
from app.ai.agent.control import RunCancelledError, RunController, get_run_registry
from app.ai.agent.plan import RunState
from app.ai.agent.snapshot import get_snapshot_manager
from app.ai.employees.policy import narrow_policy
from app.ai.providers.factory import build_llm_provider
from app.ai.tools.audit import make_audit_sink
from app.ai.tools.confirmation import get_broker
from app.ai.tools.executor import ResourceLimits, ToolExecutor
from app.ai.tools.permissions import ensure_workspace_layout
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


def _build_agent(
    session: AsyncSession,
    owner_id: uuid.UUID,
    controller: RunController,
    deliverable_kind: str = "",
    ai_run_id: uuid.UUID | None = None,
    employee: Any | None = None,
) -> AutonomousAgent:
    """Assemble an autonomous agent for one run.

    `employee` is optional, and that is deliberate: a run started without one behaves
    exactly as it did before this parameter existed. When there *is* one, its contract
    narrows the run — the same narrowing the 我的员工 page displays, applied to the thing
    that actually executes rather than to a label.
    """
    # Scoped to the owner. A shared policy file meant one account's permission level
    # applied to every account on the machine.
    policy = build_policy(owner_id)

    if employee is not None:
        # `narrow_policy` cannot widen, so an employee whose stored profile asks for more
        # than the user granted still gets the user's answer. See
        # `app/ai/employees/policy.py` and its exhaustive test.
        policy = narrow_policy(
            policy,
            {
                **(employee.permission_profile or {}),
                "skills": list(employee.skills or []),
            },
        )
        logger.info(
            "run_narrowed_by_employee",
            extra={
                "employee": employee.name,
                "level": int(policy.level),
                "tools": len(policy.allowed_tools) if policy.allowed_tools is not None else -1,
            },
        )
    # The workspace is created with its layout, not just as an empty directory:
    # the shape is part of the contract, so a run never has to invent a place to
    # put things and never has to wonder where a previous run put them.
    ensure_workspace_layout(policy.workspace)

    business = __import__("app.ai.agent.tools", fromlist=["get_tool_registry"]).get_tool_registry()
    computer = build_computer_registry(policy, enabled=capability_flags(owner_id))
    limits = ResourceLimits(
        max_output_bytes=policy.max_output_bytes,
        max_files_modified=policy.max_files_modified,
        max_tool_calls=policy.max_tool_calls,
        max_runtime_seconds=policy.max_runtime_seconds,
    )
    executor = ToolExecutor(policy, audit=make_audit_sink(session))
    registry = UnifiedToolRegistry(
        business,
        computer,
        executor,
        confirm=get_broker().request,
        limits=limits,
        allowed=policy.allowed_tools,
    )
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
        deliverable_kind=deliverable_kind,
        # So each tool invocation can be recorded against the run before it runs. The
        # runner used to write nothing to `tool_calls`, which meant an interrupted run
        # left no record of which concrete operation was in flight.
        ai_run_id=ai_run_id,
    )


async def _load_employee(
    session: AsyncSession, owner_id: uuid.UUID, employee_id: uuid.UUID | None
) -> Any | None:
    """The employee this run belongs to, or None.

    Scoped by owner on the same principle as every other read: the id arrives from the
    build run's scope, and a run must not be able to borrow another account's employee
    and inherit its contract. A miss returns None rather than raising — a run whose
    employee was deleted should still be able to finish under the user's own policy,
    which is the narrower-of-the-two answer anyway only if the user's is narrower, so
    the caller logs it.
    """
    if employee_id is None:
        return None
    from app.models.employee import Employee

    employee = await session.get(Employee, employee_id)
    if employee is None or employee.user_id != owner_id:
        logger.warning(
            "run_employee_not_found",
            extra={"employee_id": str(employee_id), "owner": str(owner_id)},
        )
        return None
    return employee


async def _release_employee(
    owner_id: uuid.UUID, employee_id: uuid.UUID | None, status: str
) -> None:
    """Move an employee to a status, on its own session, and never raise.

    Its own session rather than the run's: the run's session is held open for the whole
    run, and by the time this is called it may be mid-transaction or already poisoned by
    whatever ended the run. A status write that depends on that session succeeding is a
    status write that silently does not happen.

    Never raises, because this runs on the cleanup path. A failure here must not mask the
    run's real outcome — the caller has already decided what the run did.
    """
    if employee_id is None:
        return
    try:
        async with session_scope() as session:
            employee = await _load_employee(session, owner_id, employee_id)
            if employee is None:
                return
            employee.status = status
            await session.commit()
    except Exception as exc:
        logger.error(
            "employee_status_release_failed",
            extra={
                "employee_id": str(employee_id),
                "status": status,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )


async def _execute_run(
    run_id: str,
    db_run_id: uuid.UUID,
    goal: str,
    context: str,
    owner_id: uuid.UUID,
    project_id: uuid.UUID | None,
    controller: RunController,
    deliverable_kind: str = "",
    employee_id: uuid.UUID | None = None,
) -> None:
    """The background half: run the agent and persist the outcome."""
    #: What the roster should say once this returns. `failed` until the agent's verdict
    #: says otherwise, because "we do not know yet" must never read as success.
    roster_status = "failed"
    try:
        async with session_scope() as session:
            employee = await _load_employee(session, owner_id, employee_id)
            if employee is not None:
                # Visible while it works. A roster that says 空闲 while the employee is
                # mid-task is the same class of lie as a progress bar that never moves.
                await _release_employee(owner_id, employee_id, "working")

            agent = _build_agent(
                session, owner_id, controller, deliverable_kind, db_run_id, employee
            )
            result = await agent.run(goal, context=context)

            if result.state.value == "COMPLETED":
                roster_status = "idle"

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

            # An autonomous run is a strong signal about the user: it states a
            # goal in their own words and the outcome shows what they actually
            # wanted. Worth offering to the evaluator, which will usually decline.
            await _consider_run_memories(session, owner_id=owner_id, goal=goal, result=result)

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
        # **The release lives here, not on the happy path.** Every way out of the `try`
        # above — including the ones that raise — has to put the employee back, or a
        # failure mid-run leaves it 工作中 forever with nothing left to correct it.
        await _release_employee(owner_id, employee_id, roster_status)

        # Set last, so nothing can observe "settled" before the roster is truthful.
        # `build_executor._watch` waits on this rather than on `state`, which is what
        # stops it from collecting deliverables out of a run that is still writing.
        controller.settled = True

        _tasks.pop(run_id, None)


async def _consider_run_memories(
    session: AsyncSession, *, owner_id: uuid.UUID, goal: str, result: Any
) -> None:
    """Offer an autonomous run to the memory evaluator.

    Only the goal is offered, not the transcript: a run's goal is what the user
    wanted, while its tool calls are mechanics. And only a run that finished,
    because a failed run says more about the environment than about the person.
    """
    from app.ai.memory import MemoryEvaluator
    from app.ai.providers.factory import build_llm_provider

    if result.state is not RunState.COMPLETED:
        return
    try:
        llm, _ = build_llm_provider()
        evaluator = MemoryEvaluator(session, llm, user_id=owner_id)
        verdict = await evaluator.evaluate(
            goal, context=f"用户让 Agent 完成的任务（已成功）：{goal}", source="agent"
        )
        logger.info("memory_verdict_from_run", extra={"routing": verdict.routing.value})
    except Exception as exc:
        logger.warning("memory_pass_failed", extra={"error": f"{type(exc).__name__}: {exc}"})


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

    # **The finish time, which nothing was writing.**
    #
    # The first attempt at this read `run.started_at` — a column that does not exist on
    # `AIRun` (it belongs to `ToolCall`). That raised `AttributeError`, which the caller's
    # try/except swallowed, so **none of this function ran**: no status, no answer, no
    # finished_at. Measured afterwards: every autonomous run since had `finished_at` NULL
    # and `total_tokens` 0, while a row written before the change had both.
    #
    # The agent already measures its own wall clock, so use that rather than recomputing
    # from a column that is not there.
    now = datetime.now(UTC)
    run.finished_at = now
    if result.elapsed_seconds:
        run.latency_ms = int(result.elapsed_seconds * 1000)

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
    deliverable_kind: str = "",
    employee_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Register the run, create the recorder, and launch the background task."""
    policy = build_policy(owner_id)

    # The real provider and model, not the permission mode.
    #
    # This said `provider="autonomous"` with an empty model, and a self-check caught it:
    # half of the run history claimed a provider that does not exist. `autonomous` is a
    # *permission level* — "complete the task within the granted scope" — and using it as
    # a provider name means the history cannot answer "which model produced this", which
    # is the one question a run record exists to answer.
    llm, llm_info = build_llm_provider()

    # The run record is created here, on the request's session, so the caller can
    # query it immediately; the work itself uses its own session.
    recorder = await RunRecorder.start(
        session,
        run_type=RunType.AGENT,
        owner_id=owner_id,
        provider=llm_info.kind,
        model=getattr(llm, "model", "") or llm_info.model,
        prompt_name="agent_planner",
        prompt_version=1,
        request_payload={
            "goal": goal,
            "mode": "autonomous",
            "level": int(policy.level),
            # So the run history can answer "who did this" — which is the first thing
            # anyone asks when a run produces something unexpected.
            "employee_id": str(employee_id) if employee_id else None,
        },
        project_id=project_id,
    )
    await recorder.attach_agent_run(goal=goal, max_steps=policy.max_tool_calls)

    controller = get_run_registry().create(recorder.run_id, owner_id)
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
            deliverable_kind,
            employee_id,
        )
    )
    _tasks[recorder.run_id] = task

    return {
        # The short id used by the controller registry and the event stream.
        "run_id": recorder.run_id,
        # The database primary key. Different value, different purpose: the first
        # addresses a live controller, the second addresses a row. Returning both
        # keeps callers from guessing which one they were handed.
        "db_run_id": str(recorder.model_row.id),
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


def active_runs(owner_id: uuid.UUID) -> list[dict[str, Any]]:
    """The runs this user is currently being driven for them.

    Scoped by owner on purpose. The registry is process-wide, so an unscoped
    listing handed every user everyone else's active runs — the isolation test
    caught it (a brand-new account saw 14 runs belonging to `demo`).
    """
    return [c.as_dict() for c in get_run_registry().owned_by(owner_id)]


async def run_history(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Completed and in-flight agent runs from the database, newest first.

    This is the half the API was missing. ``GET /agent/runs`` read only the process-local
    registry, so after a restart every run the user had ever started disappeared from the
    endpoint while the rows sat untouched in ``agent_runs``. "Where did my runs go" had
    the answer "they are in the database and nothing reads them".

    Scoped by ``AgentRun.owner_id``, which is its own column rather than something reached
    through a join, so isolation does not depend on the join being written correctly.
    """
    from sqlalchemy import func, select

    from app.models.ai import AgentRun, AIRun

    base = (
        select(AgentRun, AIRun)
        .join(AIRun, AgentRun.ai_run_id == AIRun.id)
        .where(AgentRun.owner_id == owner_id)
    )
    if status:
        base = base.where(AIRun.status == status)

    total = int(
        (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    )
    rows = (
        await session.execute(base.order_by(AIRun.created_at.desc()).limit(limit).offset(offset))
    ).all()

    return [_history_row(agent_run, ai_run) for agent_run, ai_run in rows], total


#: ``RunStatus`` is what the row records; ``RunState`` is what the run was doing. They are
#: different vocabularies — ``SUCCEEDED`` is a row status and not a valid ``RunState``, so
#: handing the raw column to the UI would put a value on screen that
#: ``describeState()`` has no case for. The scratchpad holds the real ``RunState`` for a
#: run that got far enough to write one; this maps the rest.
_STATUS_TO_STATE: dict[str, str] = {
    RunStatus.RUNNING.value: "EXECUTING",
    RunStatus.SUCCEEDED.value: "COMPLETED",
    RunStatus.FAILED.value: "FAILED",
    RunStatus.CANCELLED.value: "CANCELLED",
    RunStatus.TIMEOUT.value: "FAILED",
}


def _resolve_state(status: str, scratchpad_state: str | None) -> str:
    """Which `RunState` to report for a stored run.

    The scratchpad holds the finer-grained state the run last set — it distinguishes
    `LIMIT_REACHED` from `CANCELLED`, which the row status cannot. So a terminal scratchpad
    state wins: it is strictly more informative and it agrees with the row.

    A **non-terminal** scratchpad state on a row that has ended is stale, and reporting it
    would tell the user a finished run is still going. That is not hypothetical: the startup
    reconcile marked an interrupted run's row `FAILED` while its scratchpad still said
    `EXECUTING`, and the history endpoint showed `EXECUTING` for a run that was over.
    """
    terminal_states = {"COMPLETED", "FAILED", "CANCELLED", "LIMIT_REACHED"}
    mapped = _STATUS_TO_STATE.get(status, status)
    if scratchpad_state and scratchpad_state in terminal_states:
        return scratchpad_state
    if status != RunStatus.RUNNING.value:
        # The row says the run ended, so the state must say so too.
        return mapped
    return scratchpad_state or mapped


def _history_row(agent_run: Any, ai_run: Any) -> dict[str, Any]:
    scratchpad = agent_run.scratchpad if isinstance(agent_run.scratchpad, dict) else {}
    state = _resolve_state(ai_run.status, scratchpad.get("state"))
    return {
        "run_id": ai_run.run_id,
        "db_run_id": str(ai_run.id),
        "goal": agent_run.goal,
        "status": ai_run.status,
        "state": state,
        "live": False,
        "steps_used": agent_run.steps_used,
        "max_steps": agent_run.max_steps,
        "verified": scratchpad.get("verified"),
        "snapshot_id": scratchpad.get("snapshot_id"),
        "retries": scratchpad.get("retries"),
        "provider": ai_run.provider,
        "model": ai_run.model,
        "error": ai_run.error_message or "",
        "created_at": ai_run.created_at.isoformat() if ai_run.created_at else None,
        "updated_at": ai_run.finished_at.isoformat() if ai_run.finished_at else None,
    }


async def history_row_for(
    session: AsyncSession, run_id: str, owner_id: uuid.UUID
) -> dict[str, Any] | None:
    """One stored run by its display id, but only if it belongs to ``owner_id``.

    The owner is part of the query, not a check applied afterwards — the same rule the
    list endpoint follows, so a run id guessed from a log cannot be used to read someone
    else's record.
    """
    from sqlalchemy import select

    from app.models.ai import AgentRun, AIRun

    row = (
        await session.execute(
            select(AgentRun, AIRun)
            .join(AIRun, AgentRun.ai_run_id == AIRun.id)
            .where(AgentRun.owner_id == owner_id, AIRun.run_id == run_id)
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    agent_run, ai_run = row
    return _history_row(agent_run, ai_run)


def merge_live_and_history(
    live: list[dict[str, Any]], history: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """One entry per run: the database row, with live state laid over it.

    Keyed on ``run_id``, because the same run is legitimately in both places — it is in
    the registry while it runs and in the table from the moment it starts. Listing both
    would show the same run twice, once "running" and once as whatever the row says.

    The database is the spine and the registry is an overlay, not the other way round: a
    live run with no row is appended (better a run with no history than a run that is
    invisible), but a row is never dropped because the registry no longer knows about it —
    that is precisely what a restart does.
    """
    merged: dict[str, dict[str, Any]] = {row["run_id"]: dict(row) for row in history}

    for item in live:
        run_id = item["run_id"]
        entry = merged.get(run_id)
        if entry is None:
            entry = {
                "run_id": run_id,
                "db_run_id": None,
                "goal": "",
                "status": item.get("state", ""),
                "state": item.get("state", ""),
                "live": True,
                "steps_used": item.get("tool_calls", 0),
                "max_steps": 0,
                "verified": None,
                "snapshot_id": None,
                "retries": item.get("retries"),
                "provider": "",
                "model": "",
                "error": "",
                "created_at": None,
                "updated_at": None,
            }
            merged[run_id] = entry
        else:
            entry["live"] = True
            entry["state"] = item.get("state", entry.get("state", ""))
        entry["paused"] = item.get("paused")
        entry["current_tool"] = item.get("current_tool")
        entry["tool_calls"] = item.get("tool_calls")
        entry["elapsed_seconds"] = item.get("elapsed_seconds")

    return sorted(
        merged.values(),
        key=lambda entry: (entry.get("created_at") or "", entry["run_id"]),
        reverse=True,
    )


__all__ = [
    "active_runs",
    "control",
    "diff_for_run",
    "get_controller",
    "history_row_for",
    "merge_live_and_history",
    "rollback_run",
    "run_history",
    "snapshot_view",
    "start_run",
]

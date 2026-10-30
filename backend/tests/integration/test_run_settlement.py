"""A run that has a verdict is not yet a run that has finished.

This file exists because of a defect a real end-to-end run exposed, and the defect is
worth stating plainly because it is easy to reintroduce:

    the agent sets a terminal state, and then keeps working.

After `set_state(RunState.COMPLETED)` the agent snapshots the workspace, generates its
final report — another model call — and only then does the service persist the outcome and
release the employee. So `controller.state` is a *verdict*, not a *completion*, and
anything that treats it as one acts on a run that is still writing.

Measured on the real run that found this: the build run read COMPLETED, `ai_runs.status`
was still RUNNING with `finished_at` NULL, and the employee was still 工作中. The process
exited inside that window and nothing ever corrected either record.

`RunController.settled` is the completion signal. These tests hold the two apart.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio

from app.ai.agent.control import RunController
from app.ai.agent.plan import RunState
from app.models.employee import Employee
from app.models.user import User
from app.services.autonomous_service import _release_employee


@pytest_asyncio.fixture
async def owner(session: Any) -> User:
    user = User(
        email=f"settle_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
        username=f"settle_{uuid.uuid4().hex[:8]}",
        hashed_password="x",
        display_name="Settle Owner",
    )
    session.add(user)
    await session.flush()
    return user


async def _employee(session: Any, owner: User, status: str = "idle") -> Employee:
    employee = Employee(
        user_id=owner.id,
        name=f"员工-{uuid.uuid4().hex[:6]}",
        role_id="assistant",
        mission="测试用",
        skills=["file_read"],
        status=status,
    )
    session.add(employee)
    await session.commit()
    return employee


# --------------------------------------------------------------------------- #
# the invariant: a verdict is not a completion
# --------------------------------------------------------------------------- #
def test_a_terminal_state_does_not_settle_the_controller() -> None:
    """The regression guard, and the whole point of the flag.

    If this ever fails, `build_executor._watch` will again stamp COMPLETED on a run that
    is mid-write — and it will look correct, because the state really is terminal.
    """
    controller = RunController(run_id="run_test", owner_id=uuid.uuid4())
    assert controller.settled is False

    controller.set_state(RunState.COMPLETED, "成功标准已满足")

    assert controller.state is RunState.COMPLETED
    assert controller.settled is False, "终态不等于已结束"


def test_settled_survives_a_later_state_change() -> None:
    """Nothing the agent does after finishing may un-settle it.

    `settled` is written once, in `_execute_run`'s `finally`. A state change after that
    would be a second writer, and the watcher would go back to waiting on a run that is
    already over.
    """
    controller = RunController(run_id="run_test", owner_id=uuid.uuid4())
    controller.settled = True
    controller.set_state(RunState.FAILED, "晚到的失败")
    assert controller.settled is True


# --------------------------------------------------------------------------- #
# the release
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_release_moves_the_employee(session: Any, owner: User) -> None:
    employee = await _employee(session, owner, status="working")
    await _release_employee(owner.id, employee.id, "idle")

    await session.refresh(employee)
    assert employee.status == "idle"


@pytest.mark.asyncio
async def test_release_is_quiet_when_the_employee_is_gone(session: Any, owner: User) -> None:
    """Cleanup must not raise: it runs after the outcome is already decided.

    A deleted employee is the ordinary case here — the user retires one mid-run — and an
    exception would replace the run's real outcome with a bookkeeping error.
    """
    await _release_employee(owner.id, uuid.uuid4(), "idle")


@pytest.mark.asyncio
async def test_release_ignores_an_employee_owned_by_someone_else(session: Any, owner: User) -> None:
    """The id arrives from caller-supplied data, so ownership is re-checked on write."""
    other = User(
        email=f"other_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
        username=f"other_{uuid.uuid4().hex[:8]}",
        hashed_password="x",
        display_name="Other",
    )
    session.add(other)
    await session.flush()
    employee = await _employee(session, other, status="working")

    await _release_employee(owner.id, employee.id, "idle")

    await session.refresh(employee)
    assert employee.status == "working", "别人的员工不能被这次运行改动"


# --------------------------------------------------------------------------- #
# the restart
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_restart_returns_a_stuck_employee_to_the_roster(session: Any, owner: User) -> None:
    """`working` is driven by an in-process task, so a restart makes it false."""
    from app.main import _reconcile_stuck_employees

    stuck = await _employee(session, owner, status="working")
    await _reconcile_stuck_employees()

    await session.refresh(stuck)
    assert stuck.status == "idle"


@pytest.mark.asyncio
async def test_restart_releases_a_pending_approval(session: Any, owner: User) -> None:
    """The confirmation queue lives in memory too — nothing is actually waiting."""
    from app.main import _reconcile_stuck_employees

    parked = await _employee(session, owner, status="waiting_approval")
    await _reconcile_stuck_employees()

    await session.refresh(parked)
    assert parked.status == "idle"


@pytest.mark.asyncio
async def test_restart_leaves_a_finished_run_alone(session: Any, owner: User) -> None:
    """`waiting_user` means "the work is done, you should look at it".

    That stays true across a restart — the deliverable is on disk — so resetting it would
    erase the one state that tells the user there is something to collect.
    """
    from app.main import _reconcile_stuck_employees

    waiting = await _employee(session, owner, status="waiting_user")
    await _reconcile_stuck_employees()

    await session.refresh(waiting)
    assert waiting.status == "waiting_user"


# --------------------------------------------------------------------------- #
# the outcome actually reaches the database
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_persisting_a_result_writes_the_finish_time(session: Any, owner: User) -> None:
    """`_persist_run` must actually run, and this is how that is checked.

    It was not checked, and it did not run. The first version of the finish-time fix read
    `run.started_at` — a column `AIRun` does not have, because `started_at` belongs to
    `ToolCall`. That raised `AttributeError`, the caller's try/except swallowed it, and the
    whole function was skipped: no status, no answer, no `finished_at`, `total_tokens` 0.

    Nothing failed. The run still reported COMPLETED, because the build executor reads the
    in-memory controller rather than this row. The only trace was one log line whose
    structured fields do not render. Measured afterwards: every autonomous run since had
    `finished_at` NULL, while a row written before the change had a real timestamp.

    So this asserts the one thing that was missing: after persisting, the row has an end
    time and a duration. A future `AttributeError` here fails a test instead of a log line.
    """
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.ai.agent.plan import RunState
    from app.models.ai import AIRun
    from app.services.autonomous_service import _persist_run

    run = AIRun(
        run_id=f"run_{uuid.uuid4().hex[:12]}",
        owner_id=owner.id,
        run_type="agent",
        status="running",
        created_at=datetime.now(UTC),
    )
    session.add(run)
    await session.commit()

    result = SimpleNamespace(
        state=RunState.COMPLETED,
        answer="done",
        error="",
        steps_used=2,
        plan=None,
        verified=True,
        snapshot_id="",
        retries=0,
        files_modified=1,
        verification_evidence=[],
        elapsed_seconds=12.5,
    )

    await _persist_run(session, run.id, result)
    await session.commit()

    await session.refresh(run)
    assert run.finished_at is not None, "落库没有写结束时间 —— 这条断言就是它缺失的那条"
    assert run.latency_ms == 12500, run.latency_ms
    assert run.response_text == "done"
    assert run.status == "SUCCEEDED"

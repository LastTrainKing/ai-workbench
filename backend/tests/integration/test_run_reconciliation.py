"""A run whose process died must not look like a run that is still working.

`BuildRun` is driven by an in-process task and watched by a second one. When the
process is replaced — restart, crash, deploy — both die with it and the row keeps
whatever status it had. Nothing revisits it, so the workbench shows `EXECUTING`
forever while the plan says every step finished.

Observed exactly like that: a run with `6/6 DONE` in its plan, no controller in the
registry, no deliverable registered, and `EXECUTING` in the database. Twelve such rows
were found on the first startup that looked for them.

There is no way to recover the result of a task that no longer exists, so the honest
outcome is to say so once, at startup.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.infrastructure.database import session_scope
from app.main import _reconcile_interrupted_runs
from app.models.delivery import BuildRun, BuildStatus
from app.models.user import User


async def _user_id() -> uuid.UUID:
    async with session_scope() as session:
        existing = (await session.execute(select(User).limit(1))).scalar_one_or_none()
        if existing is not None:
            return existing.id
        from app.core.security import hash_password

        suffix = uuid.uuid4().hex[:8]
        user = User(
            email=f"run_{suffix}@aiworkbench.dev",
            username=f"run_{suffix}",
            display_name="Run Test",
            hashed_password=hash_password("test1234"),
        )
        session.add(user)
        await session.flush()
        return user.id


async def _a_run(user_id: uuid.UUID, status: str) -> uuid.UUID:
    async with session_scope() as session:
        run = BuildRun(
            user_id=user_id,
            goal="[reconcile-test] 做点什么",
            title="reconcile-test",
            deliverable_kind="pptx",
            status=status,
        )
        session.add(run)
        await session.flush()
        return run.id


@pytest.mark.asyncio
async def test_an_interrupted_run_is_closed_and_says_why():
    user_id = await _user_id()
    run_id = await _a_run(user_id, BuildStatus.EXECUTING.value)

    await _reconcile_interrupted_runs()

    async with session_scope() as session:
        run = await session.get(BuildRun, run_id)
        assert run is not None
        assert run.status == BuildStatus.FAILED.value
        assert run.finished_at is not None
        assert "重启" in run.error
        assert run.result_summary, "a closed run must say what happened to its output"


@pytest.mark.asyncio
async def test_a_finished_run_is_left_alone():
    """Reconciliation must not rewrite history it has no business touching."""
    user_id = await _user_id()
    completed = await _a_run(user_id, BuildStatus.COMPLETED.value)
    partial = await _a_run(user_id, BuildStatus.PARTIAL.value)

    await _reconcile_interrupted_runs()

    async with session_scope() as session:
        assert (await session.get(BuildRun, completed)).status == BuildStatus.COMPLETED.value
        assert (await session.get(BuildRun, partial)).status == BuildStatus.PARTIAL.value


@pytest.mark.asyncio
async def test_every_active_state_is_reconciled():
    """Any state that means "the system is still working" is a state a restart can strand."""
    user_id = await _user_id()
    ids = {}
    for state in BuildStatus:
        if state.is_active:
            ids[state] = await _a_run(user_id, state.value)

    await _reconcile_interrupted_runs()

    async with session_scope() as session:
        for state, run_id in ids.items():
            run = await session.get(BuildRun, run_id)
            assert run.status == BuildStatus.FAILED.value, f"{state.value} was left open"

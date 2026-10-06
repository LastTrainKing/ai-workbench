"""The watcher must survive its own retry loop.

`_watch` retries artifact collection after a `rollback()`, because the agent's audit
rows commit asynchronously and the watcher can arrive first. `rollback()` expires every
instance in the session, and in async SQLAlchemy **touching an expired attribute
performs lazy IO, which raises `MissingGreenlet` instead of loading**.

So a `run` object captured before the rollback is a trap. Reading `run.plan` on the
second attempt raised, the watcher died, and the run stayed `EXECUTING` forever while
its agent had already finished — a trivial five-step run never reached a terminal
state, and the one demo that matters most could not be completed.

`_collect` now takes the run's **id** and loads it itself, which makes the trap
impossible rather than merely avoided.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.infrastructure.database import session_scope
from app.models.delivery import BuildRun
from app.models.user import User
from app.services.build_executor import _collect
from app.services.delivery_service import DeliveryService


async def _user_id() -> uuid.UUID:
    async with session_scope() as session:
        existing = (await session.execute(select(User).limit(1))).scalar_one_or_none()
        if existing is not None:
            return existing.id
        from app.core.security import hash_password

        suffix = uuid.uuid4().hex[:8]
        user = User(
            email=f"collect_{suffix}@aiworkbench.dev",
            username=f"collect_{suffix}",
            display_name="Collect Test",
            hashed_password=hash_password("test1234"),
        )
        session.add(user)
        await session.flush()
        return user.id


async def _a_run(user_id: uuid.UUID) -> uuid.UUID:
    async with session_scope() as session:
        run = BuildRun(
            user_id=user_id,
            goal="[collect-test] 做点什么",
            title="collect-test",
            deliverable_kind="md",
            status="COMPLETED",
            plan={"hard_constraints": {"slide_min": 10}, "preference_outcomes": []},
        )
        session.add(run)
        await session.flush()
        return run.id


@pytest.mark.asyncio
async def test_collect_works_after_a_rollback_has_expired_the_run():
    """The regression: this raised `MissingGreenlet` and killed the watcher."""
    user_id = await _user_id()
    run_id = await _a_run(user_id)

    async with session_scope() as session:
        service = DeliveryService(session)
        # Warm the identity map, exactly as the watcher does before its first attempt.
        loaded = await session.get(BuildRun, run_id)
        assert loaded is not None and loaded.plan

        await session.rollback()  # expires everything, as the retry loop does

        # Must not raise. The run is re-loaded from its id inside `_collect`.
        registered = await _collect(
            session,
            service,
            build_run_id=run_id,
            ai_run_id=str(uuid.uuid4()),
            run_state="COMPLETED",
        )
        assert registered == 0  # no audit rows for that id, but no crash either


@pytest.mark.asyncio
async def test_collect_is_harmless_for_a_missing_run():
    """A deleted run must produce a count of zero, not an exception."""
    async with session_scope() as session:
        registered = await _collect(
            session,
            DeliveryService(session),
            build_run_id=uuid.uuid4(),
            ai_run_id=str(uuid.uuid4()),
        )
    assert registered == 0

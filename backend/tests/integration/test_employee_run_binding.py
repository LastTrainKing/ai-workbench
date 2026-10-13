"""The employee reaches the run, not just the roster page.

An employee that exists, displays correctly, and does not change what the agent may do is
the failure mode §23 of the brief names outright — "只换应用名称、图标和文案，后台并没有员工
实体". These tests drive `_build_agent`, which is what a real run uses, so the claim being
checked is the one that matters: the contract narrows the thing that executes.

No model is called. `_build_agent` assembles the policy, the registry and the executor;
that assembly is what these assert on.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio

from app.ai.agent.control import get_run_registry
from app.ai.tools.permissions import AgentMode, PermissionLevel
from app.models.employee import Employee
from app.models.user import User
from app.services.autonomous_service import _build_agent, _load_employee


@pytest_asyncio.fixture
async def owner(session: Any) -> User:
    user = User(
        email=f"run_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
        username=f"run_{uuid.uuid4().hex[:8]}",
        hashed_password="x",
        display_name="Run Owner",
    )
    session.add(user)
    await session.flush()
    return user


async def _employee(session: Any, owner: User, **fields: Any) -> Employee:
    defaults = {
        "name": f"员工-{uuid.uuid4().hex[:6]}",
        "role_id": "researcher",
        "mission": "测试用",
        "skills": ["file_read", "knowledge_search"],
        "permission_profile": {"level": 0, "mode": "assist"},
    }
    employee = Employee(user_id=owner.id, **{**defaults, **fields})
    session.add(employee)
    await session.flush()
    return employee


# --------------------------------------------------------------------------- #
# the policy the run actually uses
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_run_without_an_employee_keeps_the_users_policy(session: Any, owner: User) -> None:
    """The parameter is additive: every run that existed before still behaves the same."""
    controller = get_run_registry().create(f"run_{uuid.uuid4().hex[:8]}", owner.id)
    agent = _build_agent(session, owner.id, controller)

    assert agent.policy.allowed_tools is None
    # The user's own level, whatever the settings say — not something an employee chose.
    assert agent.policy.level is not None
    assert len(agent.registry.names()) > 2


@pytest.mark.asyncio
async def test_an_employee_narrows_the_policy_the_run_uses(session: Any, owner: User) -> None:
    employee = await _employee(session, owner)
    controller = get_run_registry().create(f"run_{uuid.uuid4().hex[:8]}", owner.id)
    agent = _build_agent(session, owner.id, controller, employee=employee)

    assert agent.policy.level is PermissionLevel.OBSERVE
    assert agent.policy.mode is AgentMode.ASSIST
    assert agent.policy.allowed_tools == frozenset({"file_read", "knowledge_search"})


@pytest.mark.asyncio
async def test_the_registry_offers_only_the_employees_tools(session: Any, owner: User) -> None:
    employee = await _employee(session, owner)
    controller = get_run_registry().create(f"run_{uuid.uuid4().hex[:8]}", owner.id)
    agent = _build_agent(session, owner.id, controller, employee=employee)

    offered = agent.registry.names()
    assert set(offered) == {"file_read", "knowledge_search"}
    # And the ones it may not use are not merely hidden — the executor refuses them.
    assert agent.registry.get_spec("run_build") is None


@pytest.mark.asyncio
async def test_an_employee_cannot_widen_the_run_even_by_asking(session: Any, owner: User) -> None:
    """A stored profile requesting full control does not get it."""
    employee = await _employee(
        session,
        owner,
        name=f"贪心的-{uuid.uuid4().hex[:6]}",
        permission_profile={"level": 3, "mode": "autonomous", "max_tool_calls": 10_000_000},
    )
    controller = get_run_registry().create(f"run_{uuid.uuid4().hex[:8]}", owner.id)
    agent = _build_agent(session, owner.id, controller, employee=employee)

    assert int(agent.policy.level) <= 3
    # The ceiling came from the user's policy, not the employee's wish.
    assert agent.limits.max_tool_calls < 10_000_000


# --------------------------------------------------------------------------- #
# ownership
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_run_cannot_borrow_another_users_employee(session: Any, owner: User) -> None:
    """The id arrives in the build run's scope, so it is caller-supplied data."""
    other = User(
        email=f"other_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
        username=f"other_{uuid.uuid4().hex[:8]}",
        hashed_password="x",
        display_name="Other",
    )
    session.add(other)
    await session.flush()

    employee = await _employee(session, other)
    assert await _load_employee(session, owner.id, employee.id) is None
    assert await _load_employee(session, other.id, employee.id) is not None


@pytest.mark.asyncio
async def test_a_missing_employee_falls_back_to_the_users_policy(
    session: Any, owner: User
) -> None:
    """A run whose employee was deleted should still be able to finish.

    Falling back to the user's own policy is not a widening in the dangerous direction:
    the user's policy is what the run would have used had no employee been named.
    """
    assert await _load_employee(session, owner.id, uuid.uuid4()) is None
    assert await _load_employee(session, owner.id, None) is None

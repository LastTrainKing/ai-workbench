"""A run belongs to the person who started it.

The registry that holds live runs is process-wide — that is inherent, one process
drives all of them. What is not inherent is showing them to everybody. Before
this, `GET /agent/runs` returned `registry.all()` to any authenticated caller and
the by-id endpoints resolved a run id without checking who was asking, so a
brand-new account could list another user's runs and pause, cancel or roll them
back. The onboarding isolation test is what surfaced it.

These tests pin the two halves: the registry filters, and every by-id endpoint
refuses a run that is not yours.
"""

from __future__ import annotations

import uuid

import pytest

from app.ai.agent.control import RunRegistry


def test_registry_returns_only_the_owners_runs():
    registry = RunRegistry()
    alice, bob = uuid.uuid4(), uuid.uuid4()

    registry.create("run-alice", alice)
    registry.create("run-bob", bob)

    assert {c.run_id for c in registry.all()} == {"run-alice", "run-bob"}
    assert {c.run_id for c in registry.owned_by(alice)} == {"run-alice"}
    assert {c.run_id for c in registry.owned_by(bob)} == {"run-bob"}


def test_a_stranger_sees_nothing_rather_than_everything():
    registry = RunRegistry()
    registry.create("run-alice", uuid.uuid4())

    # The empty list has to mean "no runs of yours", not "the filter failed" —
    # which is why `owned_by` returns [] and not None or all().
    assert registry.owned_by(uuid.uuid4()) == []


def test_owner_is_required_not_defaulted():
    """A default of None would let the original bug return silently."""
    from app.ai.agent.control import RunController

    with pytest.raises(TypeError):
        RunController(run_id="x")  # type: ignore[call-arg]


def test_every_by_id_endpoint_goes_through_the_ownership_check():
    """Source-level guard, because the failure is an *omission*.

    A new by-id endpoint that forgets `_owned_controller` reintroduces the leak and
    no behavioural test would notice unless it happened to exercise that path. So
    assert on the module itself: each run-scoped route must call the check.
    """
    import inspect

    from app.api.v1 import agent_runs

    source = inspect.getsource(agent_runs)

    # Routes that take a run_id and must therefore confirm ownership first.
    required = [
        "get_run_state",
        "get_run_events",
        "get_run_plan",
        "pause_run",
        "resume_run",
        "cancel_run",
        "get_run_diff",
        "rollback_run_endpoint",
    ]
    for name in required:
        fn = getattr(agent_runs, name)
        body = inspect.getsource(fn)
        assert "_owned_controller(" in body, f"{name} does not verify run ownership"

    # And the unfiltered helper must not be reachable from the API layer.
    assert "active_runs()" not in source, "the unscoped listing is back"
    assert "registry.all()" not in source

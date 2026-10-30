"""The run's terminal status, as a decision table.

§12 flow 3 of the release task book: *产物文件创建失败：不能报成功*. That rule used to
live inline in `_watch`, where the only way to exercise it was to drive a real agent —
so in practice it was exercised by whatever happened to happen.

Every rule here has a matching incident in this project's history:

* a run that declared COMPLETED and then kept writing was reported COMPLETED, and the
  deliverable was collected out of a run that had not stopped writing
* a run whose whole point was a file finished without one, and reported COMPLETED
* a five-step run that produced a correct 11-slide deck was reported FAILED because its
  final verification call failed on a mistyped path

The assertions are paired: each "must not be COMPLETED" case is checked together with
the status it *must* be, because a decision function that answered WAITING_USER to
everything would satisfy half of them.
"""

from __future__ import annotations

import pytest

from app.models.delivery import BuildStatus
from app.services.build_executor import decide_final_status


def _decide(state: str, *, unsettled: bool = False, collected: int = 0):
    return decide_final_status(state, unsettled=unsettled, collected=collected)


# --------------------------------------------------------------------------- #
# Success is the one case that requires evidence
# --------------------------------------------------------------------------- #
def test_completed_with_files_is_completed():
    status, summary = _decide("COMPLETED", collected=2)

    assert status is BuildStatus.COMPLETED
    assert "2" in summary


def test_completed_without_any_file_is_not_a_completed_delivery():
    """The run asked for a file. Finishing without one is not success."""
    status, summary = _decide("COMPLETED", collected=0)

    assert status is BuildStatus.WAITING_USER
    assert "没有检测到产出文件" in summary


def test_declared_success_that_never_settled_is_not_completed():
    """§4.3's first invariant, stated as a status.

    The agent set COMPLETED and then never stopped. Reading the verdict off that state
    is how a run was marked done while it was still writing.
    """
    status, summary = _decide("COMPLETED", unsettled=True, collected=3)

    assert status is BuildStatus.WAITING_USER
    assert "收尾没有在" in summary
    # The files it did write are not disowned — the summary says so.
    assert "不受影响" in summary


def test_unsettled_only_overrides_completion_not_failure():
    """A run that failed and also never settled is still a failure."""
    status, _ = _decide("FAILED", unsettled=True, collected=1)

    assert status is BuildStatus.PARTIAL


# --------------------------------------------------------------------------- #
# Failure with output
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("state", ["FAILED", "CANCELLED", "LIMIT_REACHED", "MAX_STEPS"])
def test_a_run_that_failed_but_wrote_files_is_partial(state: str):
    """Neither "failed" nor "completed" is true alone, so the record says the middle one."""
    status, summary = _decide(state, collected=1)

    assert status is BuildStatus.PARTIAL
    assert "没有走完的是这次运行" in summary


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED", "LIMIT_REACHED"])
def test_a_run_that_failed_with_nothing_is_that_failure(state: str):
    """The other half: PARTIAL must not swallow an empty failure."""
    status, _ = _decide(state, collected=0)

    assert status is BuildStatus[state]


def test_an_unrecognised_agent_state_fails_rather_than_succeeding():
    """Unknown must not fall through to COMPLETED."""
    status, _ = _decide("something_new", collected=0)

    assert status is BuildStatus.FAILED


# --------------------------------------------------------------------------- #
# Waiting on the user
# --------------------------------------------------------------------------- #
def test_waiting_confirmation_maps_to_waiting_user_not_partial():
    """PARTIAL would put a failure in the record that never happened."""
    status, summary = _decide("WAITING_CONFIRMATION", collected=2)

    assert status is BuildStatus.WAITING_USER
    assert "需要你自己看一眼" in summary


# --------------------------------------------------------------------------- #
# The property that matters most
# --------------------------------------------------------------------------- #
def test_nothing_is_reported_completed_without_a_file_and_a_settled_run():
    """Exhaustive: no combination may reach COMPLETED without both conditions.

    This is the invariant behind §12 flow 3, checked over the whole table rather than
    case by case — because the failure mode is a *combination* someone did not think of.
    """
    states = [
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "LIMIT_REACHED",
        "WAITING_CONFIRMATION",
        "MAX_STEPS",
        "weird",
    ]

    for state in states:
        for unsettled in (False, True):
            for collected in (0, 1, 5):
                status, _ = decide_final_status(state, unsettled=unsettled, collected=collected)
                if status is BuildStatus.COMPLETED:
                    assert state == "COMPLETED", (state, unsettled, collected)
                    assert not unsettled, (state, unsettled, collected)
                    assert collected > 0, (state, unsettled, collected)

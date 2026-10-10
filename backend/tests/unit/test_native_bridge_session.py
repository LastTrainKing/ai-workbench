"""Every service the native bridge builds must get a session from the shared scope.

`loadDashboard` was written as `StatsService()` — no session, no scope. The home screen
failed on its first call with *"missing 1 required positional argument: 'session'"*, and
because the page itself still rendered, every panel sat on "读取中…" and it read as a broken
**page** rather than a missing session. That is the same shape as the earlier migration bug
where a page rendered and its bridge call had no context.

This drives the slot the way QML does — `loadDashboard(request_id)`, work on the runner's
own loop — and asserts the service was constructed with the session the scope handed out.
Against the old code the spy's `__init__` is called with no argument and the test fails.
"""

from __future__ import annotations

import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DESKTOP = REPO / "desktop-native"
if str(DESKTOP) not in sys.path:
    sys.path.insert(0, str(DESKTOP))

from awbnative import resources

# `create=False`: this test never touches the filesystem, and a verification run should not
# create a data directory to find out whether a constructor was called correctly.
resources.configure(create=False)

from awbnative.bridge import ServiceBridge


@pytest.fixture
def bridge() -> ServiceBridge:
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication([])
    instance = ServiceBridge(owner_id=None)
    yield instance
    instance._runner.shutdown()


def _wait_until_idle(bridge: ServiceBridge, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bridge._runner.inflight == 0:
            return True
        time.sleep(0.05)
    return False


def test_the_dashboard_slot_hands_stats_service_a_session(
    bridge: ServiceBridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = object()
    seen: dict[str, object] = {}

    @asynccontextmanager
    async def fake_scope():
        seen["entered"] = True
        yield sentinel

    class Spy:
        def __init__(self, session: object) -> None:
            seen["session"] = session

        async def dashboard(self, *, owner_id: uuid.UUID) -> dict[str, object]:
            seen["owner"] = owner_id
            return {"tasks": {}}

    monkeypatch.setattr("app.infrastructure.database.session_scope", fake_scope)
    monkeypatch.setattr("app.services.stats_service.StatsService", Spy)

    owner = uuid.uuid4()

    async def fake_owner(self: ServiceBridge) -> uuid.UUID:
        return owner

    monkeypatch.setattr(ServiceBridge, "_owner", fake_owner)

    bridge.loadDashboard("workbench-session-test")
    assert _wait_until_idle(bridge), "the slot never finished"

    assert seen.get("entered") is True, "the slot ran outside a session scope"
    assert seen.get("session") is sentinel, (
        "StatsService was not given the session the scope handed out — this is the bug that "
        "made the home screen fail with 'missing 1 required positional argument: session'"
    )
    assert seen.get("owner") == owner, "the service was not asked about this account"

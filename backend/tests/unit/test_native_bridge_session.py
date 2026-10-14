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
    # Settled, because this test is about what a slot does, not about when it is allowed to
    # run: an unsettled bridge makes every call wait for an environment that no fixture
    # here starts.
    instance.record_startup({}, {"ready": True})
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


def test_a_call_waits_for_the_environment_before_it_runs(bridge: ServiceBridge) -> None:
    """The window is on screen before the stack is up, and the home page asks immediately.

    `main.py` shows the window and *then* calls `startEnvironment`, so the dashboard's first
    query reaches PostgreSQL while PostgreSQL is still starting. It failed with
    `ConnectionRefusedError [WinError 1225]`, the page showed that and stopped asking, and
    the screen sat on "读取中…" over a database that came up seconds later.

    The engines were ready 3.7 seconds after the window appeared, so the wait here is
    asserted with a state change rather than a duration: the call must not run while the
    environment is starting, and must run once it is ready.
    """
    ran: list[float] = []
    answered: dict[str, str] = {}

    async def factory() -> str:
        ran.append(time.monotonic())
        return "payload"

    bridge._set_startup("starting", "正在准备本地环境…")
    bridge.completed.connect(lambda rid, text: answered.__setitem__(rid, text))
    bridge._submit("waits-for-environment", factory)

    time.sleep(0.6)
    assert ran == [], "the call ran before the environment was ready"

    bridge._set_startup("ready", "已就绪，可以开始使用")
    assert _wait_until_idle(bridge), "the call never ran once the environment was ready"
    assert len(ran) == 1, "the call ran more than once"

    # `completed` is emitted on the runner's thread and delivered to this one through Qt's
    # queue, so it needs the event loop to turn before it arrives.
    from PySide6.QtCore import QCoreApplication

    deadline = time.monotonic() + 5.0
    while "waits-for-environment" not in answered and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    assert answered.get("waits-for-environment") == "payload", "the result never came back"


def test_a_failed_environment_does_not_leave_a_call_waiting_forever(
    bridge: ServiceBridge,
) -> None:
    """An environment that never comes up must produce an error, not a hung request."""
    bridge._set_startup("failed", "本地环境没能启动：数据库")
    bridge._submit("after-failure", lambda: _immediately())
    assert _wait_until_idle(bridge, timeout=10.0), (
        "a settled-but-failed environment must let calls through, so the user sees the "
        "error the failure describes instead of a request that never answers"
    )


async def _immediately() -> str:
    return "ran"


def test_submit_hands_the_runner_a_callable(
    bridge: ServiceBridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`AsyncRunner.submit` calls what it is given.

    The wait was first written as `self._runner.submit(id, self._after_environment(factory))`
    — a coroutine *object*, built on the UI thread's loop. Every slot then failed with
    `TypeError: 'coroutine' object is not callable`, which is the mistake `submit`'s own
    docstring warns about.
    """
    captured: dict[str, object] = {}

    def fake_submit(request_id: str, factory: object, *, timeout: float = 120.0) -> None:
        captured["request_id"] = request_id
        captured["factory"] = factory
        captured["timeout"] = timeout

    monkeypatch.setattr(bridge._runner, "submit", fake_submit)

    async def work() -> str:
        return "done"

    bridge._submit("callable-check", work)

    assert callable(captured["factory"]), (
        "the runner calls the factory it is handed; a coroutine object fails at call time"
    )
    assert float(captured["timeout"]) > 120.0, (
        "the timeout must cover the wait for the environment as well as the call itself"
    )


def test_the_reverify_slot_calls_verify_deliverable_by_keyword(
    bridge: ServiceBridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 「重新验证」 button was the one call site that passed it positionally, and it always failed.

    `DeliveryService.verify_deliverable` is declared `(*, owner_id, deliverable_id)` —
    keyword-only. The bridge passed the deliverable object positionally, so every press
    raised `TypeError: verify_deliverable() takes 1 positional argument but 2 were given`,
    the toast said only 「操作没有完成」, and the verdict on screen never moved.

    It mattered more than a dead button. `RELEASE_NOTES.md` known issue 10 says a
    deliverable's verification is a snapshot with no timestamp, and the documented
    workaround was *this button*. With it broken, a verdict was frozen permanently.

    The spy accepts anything, so what this catches is not an exception but the **call
    convention** — which is the defect. Against the old code the first assertion fails.
    """
    seen: dict[str, object] = {}

    @asynccontextmanager
    async def fake_scope():
        yield object()

    class Item:
        id = uuid.uuid4()

        def as_dict(self) -> dict[str, object]:
            return {"id": str(self.id)}

    item = Item()

    class SpyDeliveryService:
        def __init__(self, session: object) -> None:
            seen["session"] = session

        async def get_deliverable(self, *, owner_id: uuid.UUID, deliverable_id: uuid.UUID):
            return item

        async def verify_deliverable(self, *args: object, **kwargs: object) -> dict[str, object]:
            seen["args"] = args
            seen["kwargs"] = kwargs
            return {"checks": []}

    monkeypatch.setattr("app.infrastructure.database.session_scope", fake_scope)
    monkeypatch.setattr("app.services.delivery_service.DeliveryService", SpyDeliveryService)

    owner = uuid.uuid4()

    async def fake_owner(self: ServiceBridge) -> uuid.UUID:
        return owner

    monkeypatch.setattr(ServiceBridge, "_owner", fake_owner)

    # Capture the failure the slot would report, so a broken call says *why* rather than
    # only "the slot never finished".
    failures: list[tuple[str, str, str]] = []
    bridge.failed.connect(lambda *args: failures.append(args))

    bridge.verifyDeliverable("reverify-test", str(item.id))
    assert _wait_until_idle(bridge), f"the slot never finished; failures={failures}"

    assert seen.get("args") == (), (
        "verify_deliverable was called with a positional argument; its signature is "
        f"keyword-only, so this raises TypeError and the button does nothing. failures={failures}"
    )
    kwargs = seen.get("kwargs") or {}
    assert kwargs.get("owner_id") == owner, "the account was not passed"
    assert kwargs.get("deliverable_id") == item.id, "the wrong deliverable was passed"

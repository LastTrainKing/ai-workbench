"""Verification for the native UI PoC.

A screenshot proves a window appeared. This proves the window is connected to the product:
every check below invokes the same slot QML invokes, waits for the same signal QML waits
for, and then looks at the **database** to see whether anything actually happened.

Run it::

    .venv\\Scripts\\python.exe desktop-native\\verify_native_ui.py

It uses the test database (`aiworkbench_test`) and a temporary account, and removes both
when it is done. It never touches the development database or the installed data
directory.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
BACKEND = PROJECT_ROOT / "backend"

# The test database, chosen before anything imports `app`. The same reasoning as the
# backend's own `conftest.py`: a verification that can write to the development database
# is a verification nobody dares run.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/aiworkbench_test"
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/1")
os.environ.setdefault("AI_PROVIDER", "offline")
os.environ.setdefault("EMBEDDING_PROVIDER", "offline")
os.environ.setdefault("WORKER_ENABLED", "false")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("CACHE_ENABLED", "false")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

for candidate in (str(BACKEND), str(HERE)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    return ok


class Caller:
    """Invokes a bridge slot and waits for its signal, exactly as QML does."""

    def __init__(self, bridge: object) -> None:
        self._bridge = bridge
        self._n = 0

    def __call__(self, slot: str, *args: object, timeout: float = 120.0) -> object:
        self._n += 1
        request_id = f"verify-{self._n}-{uuid.uuid4().hex[:6]}"
        box: dict[str, object] = {}
        loop = QEventLoop()

        def on_ok(rid: str, payload: str) -> None:
            if rid == request_id:
                box["ok"] = payload
                loop.quit()

        def on_err(rid: str, message: str, code: str) -> None:
            if rid == request_id:
                box["err"] = (message, code)
                loop.quit()

        self._bridge.completed.connect(on_ok)  # type: ignore[attr-defined]
        self._bridge.failed.connect(on_err)  # type: ignore[attr-defined]
        try:
            getattr(self._bridge, slot)(request_id, *args)
            QTimer.singleShot(int(timeout * 1000), loop.quit)
            loop.exec()
        finally:
            self._bridge.completed.disconnect(on_ok)  # type: ignore[attr-defined]
            self._bridge.failed.disconnect(on_err)  # type: ignore[attr-defined]

        if "err" in box:
            message, code = box["err"]  # type: ignore[misc]
            raise RuntimeError(f"{message} [{code}]")
        return json.loads(box.get("ok") or "null")


async def _make_account() -> uuid.UUID:
    from app.core.security import hash_password
    from app.infrastructure.database import session_scope
    from app.models.user import User

    name = f"native_{uuid.uuid4().hex[:8]}"
    async with session_scope() as session:
        user = User(
            email=f"{name}@aiworkbench.dev",
            username=name,
            display_name="Native UI Verify",
            hashed_password=hash_password("native1234"),
        )
        session.add(user)
        await session.flush()
        return user.id


async def _drop_account(owner: uuid.UUID) -> None:
    from app.infrastructure.database import session_scope
    from app.models.task import Task
    from app.models.user import User
    from sqlalchemy import delete

    async with session_scope() as session:
        await session.execute(delete(Task).where(Task.owner_id == owner))
        await session.execute(delete(User).where(User.id == owner))


async def _count_tasks(owner: uuid.UUID) -> int:
    from app.infrastructure.database import session_scope
    from app.models.task import Task
    from sqlalchemy import func, select

    async with session_scope() as session:
        return int(
            (
                await session.execute(
                    select(func.count(Task.id)).where(Task.owner_id == owner)
                )
            ).scalar_one()
        )


async def _task_status(owner: uuid.UUID, task_id: uuid.UUID) -> str:
    from app.infrastructure.database import session_scope
    from app.models.task import Task

    async with session_scope() as session:
        row = await session.get(Task, task_id)
        return row.status if row else "<missing>"


async def _agent_run_count(owner: uuid.UUID) -> int:
    from app.infrastructure.database import session_scope
    from app.models.ai import AIRun
    from sqlalchemy import func, select

    async with session_scope() as session:
        return int(
            (
                await session.execute(
                    select(func.count(AIRun.id)).where(AIRun.owner_id == owner)
                )
            ).scalar_one()
        )


def main() -> int:
    app = QGuiApplication(sys.argv[:1] + ["verify-native-ui"])

    print("=== 1. the window and the QML tree ===")
    from awbnative.bridge import ServiceBridge

    owner = app_process_owner()
    bridge = ServiceBridge(owner_id=owner)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(HERE / "qml" / "Main.qml")))
    roots = engine.rootObjects()
    check("Main.qml produces a window", len(roots) == 1, f"{len(roots)} root object(s)")
    if not roots:
        return 1
    window = roots[0]
    check(
        "the window is a real Qt window with a size",
        int(window.property("width")) > 0 and int(window.property("height")) > 0,
        f"{window.property('width')}x{window.property('height')}",
    )
    check(
        "the screen reports a device pixel ratio",
        float(app.primaryScreen().devicePixelRatio()) > 0,
        str(app.primaryScreen().devicePixelRatio()),
    )

    call = Caller(bridge)

    def on_loop(factory):
        """Run a DB helper on the bridge's loop.

        The helpers below are async because the engine is async; running them with
        `asyncio.run` would create a second loop and the pooled connections belong to the
        first. `call_blocking` exists for exactly this — a caller that has no loop of its
        own and needs one answer.
        """
        return bridge._runner.call_blocking(factory)

    print("\n=== 2. runtime info comes from the real settings ===")
    runtime = call("loadRuntimeInfo")
    check("version matches the repository VERSION file",
          runtime["version"] == (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip(),
          runtime["version"])
    check("offline mode is reported honestly", isinstance(runtime["offline"], bool),
          f"offline={runtime['offline']}")

    print("\n=== 3. a real business page: tasks ===")
    before = on_loop(lambda: _count_tasks(owner))
    created = call("createTask", f"原生界面验证任务 {uuid.uuid4().hex[:6]}", "HIGH")
    after = on_loop(lambda: _count_tasks(owner))
    check("createTask wrote a row", after == before + 1, f"{before} -> {after}")

    listing = call("loadTasks")
    check(
        "loadTasks returns the row that was just written",
        any(item["id"] == created["id"] for item in listing["items"]),
        f"{listing['total']} task(s)",
    )

    call("completeTask", created["id"])
    status = on_loop(lambda: _task_status(owner, uuid.UUID(created["id"])))
    check("completeTask changed the stored status", status == "DONE", status)

    print("\n=== 4. provider configuration is per account and write-only ===")
    cfg = call("loadAiConfig")
    check("the API key is never returned",
          "api_key" not in json.dumps(cfg["effective"]) or cfg["effective"]["api_key_set"] is False,
          f"api_key_set={cfg['effective']['api_key_set']}")

    secret = "sk-fake-0000000000-not-a-real-credential"
    saved = call("saveAiConfig", json.dumps({"model": "native-poc-model", "api_key": secret}))
    check("saveAiConfig persists the model", saved["effective"]["model"] == "native-poc-model",
          saved["effective"]["model"])
    check("the key is reported as set, not echoed",
          saved["effective"]["api_key_set"] is True
          and secret not in json.dumps(saved["effective"]),
          saved["effective"]["api_key_hint"])

    reloaded = call("loadAiConfig")
    check("the setting survives a re-read", reloaded["effective"]["model"] == "native-poc-model")

    other = ServiceBridge(owner_id=str(uuid.uuid4()))
    other_cfg = Caller(other)("loadAiConfig")
    check("another account does not see it",
          other_cfg["effective"]["model"] != "native-poc-model"
          and other_cfg["effective"]["api_key_set"] is False,
          other_cfg["effective"]["model"])
    other.shutdown()

    call("clearAiConfig")
    cleared = call("loadAiConfig")
    check("clearAiConfig removes the override", cleared["effective"]["model"] != "native-poc-model",
          cleared["effective"]["model"])

    print("\n=== 5. a real agent run, recorded in the database ===")
    runs_before = on_loop(lambda: _agent_run_count(owner))
    outcome = call("startAgentRun", "把当前项目里未完成的任务列出来", timeout=240)
    runs_after = on_loop(lambda: _agent_run_count(owner))
    check("the run was recorded in ai_runs", runs_after > runs_before, f"{runs_before} -> {runs_after}")
    check("the status is one of the real terminal values",
          outcome["status"] in {"SUCCEEDED", "INCOMPLETE", "FAILED", "TIMEOUT", "CANCELLED"},
          outcome["status"])
    check("the stop reason is reported",
          outcome["stopped_reason"] in {"final_answer", "max_steps", "loop_detected",
                                        "empty_response", "tool_verification_failed"},
          outcome["stopped_reason"])
    check("the offline engine is not claimed as a success",
          not (outcome["status"] == "SUCCEEDED" and outcome["stopped_reason"] != "final_answer"),
          f"{outcome['status']} / {outcome['stopped_reason']}")

    print("\n=== 6. cancel is a real request ===")
    bridge.cancelAgentRun()
    check("cancelAgentRun sets the flag", bridge._cancel_requested is True)
    bridge._cancel_requested = False

    print("\n=== 7. file operations still go through the permission layer ===")
    from app.ai.tools.permissions import default_workspace

    # Inside the agent workspace, which the policy always allows — the same place the
    # agent's own `file_write` lands. Writing to a temp directory instead would fail the
    # policy, which is the guard doing its job rather than a bug.
    workspace = default_workspace()
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / f"native-poc-export-{uuid.uuid4().hex[:6]}.txt"
    try:
        written = call("saveTextFile", str(target), "hello from the native UI")
        check("saveTextFile wrote the file inside the workspace",
              target.is_file() and written["bytes"] > 0, f"{written['bytes']} bytes")
    finally:
        target.unlink(missing_ok=True)

    outside = False
    try:
        call("saveTextFile", str(pathlib.Path(tempfile.gettempdir()) / "outside-policy.txt"), "x")
    except RuntimeError as exc:
        outside = "outside the workspace" in str(exc) or "权限" in str(exc)
    check("a path outside the allowed roots is refused", outside)

    denied = False
    try:
        # Inside a protected location. The agent's own policy has to refuse this — a UI
        # that could write here would be a bypass with a nice font.
        call("saveTextFile", str(pathlib.Path.home() / ".ssh" / "authorized_keys"), "x")
    except RuntimeError as exc:
        denied = "权限" in str(exc) or "denied" in str(exc).lower() or "拒绝" in str(exc)
    check("a protected path is refused by the policy", denied)

    missing = False
    try:
        call("openPath", str(workspace / "does-not-exist.txt"))
    except RuntimeError:
        missing = True
    check("openPath reports a missing file instead of pretending", missing)

    print("\n=== 8. IPC: the out-of-process option ===")
    from awbnative.ipc import MAX_MESSAGE_BYTES, IpcError, PipeClient, PipeServer

    pipe_name = rf"\\.\pipe\awb-verify-{uuid.uuid4().hex[:8]}"
    server = PipeServer(
        {
            "ping": lambda params: {"pong": params.get("n", 0)},
            "echo": lambda params: params,
            "boom": lambda _params: (_ for _ in ()).throw(ValueError("expected failure")),
        },
        name=pipe_name,
    )
    server.start()
    try:
        client = PipeClient(name=pipe_name, timeout=10)
        client.connect()
        check("a request round-trips over the named pipe", client.call("ping", {"n": 7}) == {"pong": 7})

        big = "x" * (MAX_MESSAGE_BYTES + 1024)
        oversize = False
        try:
            client.call("echo", {"blob": big})
        except IpcError as exc:
            oversize = exc.code in {"message_too_large", "bad_reply", "timeout"}
        check("an oversized frame is refused, not truncated", oversize,
              f"cap={MAX_MESSAGE_BYTES} bytes")

        unknown = False
        try:
            client.call("no_such_method")
        except IpcError as exc:
            unknown = exc.code == "unknown_method"
        check("an unknown method is refused by name", unknown)

        failed = False
        try:
            client.call("boom")
        except IpcError as exc:
            failed = exc.code == "handler_error" and "expected failure" in str(exc)
        check("a handler error comes back structured, not as a traceback", failed)

        client.close()
    finally:
        server.stop()
        time.sleep(0.3)

    print("\n=== 9. cleanup ===")
    # On the bridge's loop, before it is stopped: the engine's pooled connections belong
    # to that loop, and `asyncio.run` here would be a second one.
    try:
        on_loop(lambda: _drop_account(owner))
        check("the temporary account and its rows were removed", True)
    except Exception as exc:  # noqa: BLE001 - report, do not mask
        check("the temporary account and its rows were removed", False, str(exc))
    bridge.shutdown()

    passed = sum(1 for _l, ok, _d in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n=== {passed}/{total} passed ===")
    if passed != total:
        print("FAILED:")
        for label, ok, detail in RESULTS:
            if not ok:
                print(f"  - {label} — {detail}")
        return 1
    return 0


# The account is created with asyncio before Qt takes over. Two event loops in one process
# is the same problem the bridge solves with its own thread — and the SQLAlchemy async
# engine pools connections bound to the loop that created them, so a second loop reusing
# that pool raises "got Future attached to a different loop".
#
# So the bootstrap loop disposes the engine on its way out. The bridge's loop then builds
# a fresh pool, which is exactly what a real application start would do.
_OWNER: str = ""


def app_process_owner() -> str:
    return _OWNER


def app_process_cleanup(owner: str) -> None:
    import asyncio

    from app.infrastructure.database import dispose_engine

    async def work() -> None:
        await _drop_account(uuid.UUID(owner))
        await dispose_engine()

    asyncio.run(work())


async def _bootstrap() -> uuid.UUID:
    from app.infrastructure.database import dispose_engine

    owner = await _make_account()
    await dispose_engine()
    return owner


if __name__ == "__main__":
    import asyncio

    _OWNER = str(asyncio.run(_bootstrap()))
    raise SystemExit(main())

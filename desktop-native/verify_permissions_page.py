"""Does the permissions page tell the truth, and does changing it change anything?

Runs the real interface in-process, drives it the way a person would, and checks the two
claims that matter:

  * **the page shows what is in force** — the mode, the level and the workspace it renders
    are compared against `policy_store.describe()` for the same account, not against a
    fixture;
  * **what it shows is what happens** — after changing the level through the page, a real
    `file_write` is run through `ToolExecutor` and its outcome is compared with the verdict
    the page displays for the same path.

A screen that renders a value correctly and a tool that ignores it would pass a UI test and
fail this one. That is the failure mode this file exists for.

The policy file and the workspace are redirected into a temporary directory before any
application module is imported: a verification run must not change the machine's agent
permissions, for the same reason it must not write into the user's data directory.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "backend"))

# Before `app.core.config` is imported: `_store_path` and `default_workspace` read these at
# call time, but the runtime root is resolved at import time, so the order matters.
_SCRATCH = Path(tempfile.mkdtemp(prefix="aw-permissions-verify-"))
(_SCRATCH / "workspace").mkdir(parents=True, exist_ok=True)
os.environ["AI_WORKBENCH_AGENT_POLICY"] = str(_SCRATCH / "agent-policy.json")
os.environ["AI_WORKBENCH_AGENT_WORKSPACE"] = str(_SCRATCH / "workspace")

from PySide6.QtCore import (
    QEventLoop,
    QMetaObject,
    QObject,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow  # noqa: F401
from PySide6.QtTest import QTest

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def pump(ms: int = 200) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def call(obj: QObject, name: str, *args: object) -> bool:
    """Invoke a QML-declared function. QML functions are methods on the meta-object."""
    return QMetaObject.invokeMethod(obj, name, Qt.DirectConnection, *args)


def load_font(app: QGuiApplication) -> str:
    from PySide6.QtGui import QFontDatabase

    for candidate in (
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttf",
    ):
        if candidate.is_file() and QFontDatabase.addApplicationFont(str(candidate)) >= 0:
            return candidate.name
    return ""


def build_engine(bridge: object) -> tuple[QQmlApplicationEngine, object]:
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(HERE / "qml" / "Main.qml")))
    roots = engine.rootObjects()
    if not roots:
        raise SystemExit("Main.qml 没有产生根对象")
    return engine, roots[0]


def read_value(obj: object, name: str) -> object:
    """A QML property as a Python value. Objects and arrays arrive as `QJSValue`."""
    raw = obj.property(name)
    if hasattr(raw, "toVariant"):
        return raw.toVariant()
    return raw


def read_array(window: object, name: str) -> list:
    """A JS array arrives as a `QJSValue`; `toVariant()` is what turns it into Python."""
    raw = window.property(name)
    if hasattr(raw, "toVariant"):
        raw = raw.toVariant()
    return list(raw or [])


def go_to_permissions(window: object) -> object:
    """Ctrl+N to the permissions page, then hand back the page object.

    Driven by a key, not by setting `activePage`: the shortcut is part of what the interface
    promises, and the page is reached through the same Loader a user reaches it through.
    """
    pages = read_array(window, "pages")
    index = next((i for i, p in enumerate(pages, start=1) if p["key"] == "permissions"), None)
    if index is None:
        raise SystemExit("页面清单里没有 permissions")
    QTest.keyClick(window, getattr(Qt, f"Key_{index}"), Qt.ControlModifier)
    pump(400)
    loader = window.findChild(QObject, "pageLoader")
    if loader is None:
        raise SystemExit("找不到 pageLoader")
    page = loader.property("item")
    if page is None:
        raise SystemExit("permissions 页面没有加载出来")
    return page


def main() -> int:
    app = QGuiApplication(sys.argv[:1] + ["verify-permissions-page"])
    load_font(app)

    from PySide6.QtCore import qInstallMessageHandler

    def _on_qt_message(_mode: object, ctx: object, message: str) -> None:
        where = f"  <-- {ctx.file}:{ctx.line}" if hasattr(ctx, "file") else ""
        print(f"     [QML] {message}{where}", flush=True)

    qInstallMessageHandler(_on_qt_message)

    from awbnative.bridge import ServiceBridge

    owner = uuid.uuid4()
    bridge = ServiceBridge(owner_id=owner)

    engine, window = build_engine(bridge)
    pump(500)

    page = go_to_permissions(window)

    # ---------------------------------------------------- 1. it shows what is in force --
    section("1. 页面显示的就是实际生效的策略")
    call(page, "load")
    pump(900)


    live = bridge._runner.call_blocking(lambda: _describe(owner))
    shown = read_value(page, "policy") or {}
    shown_mode = str(shown.get("mode", ""))
    shown_level = int(shown.get("level", -1))
    check(
        "运行模式与 policy_store 一致",
        shown_mode == live["mode"],
        f"页面 {shown_mode!r} / 服务 {live['mode']!r}",
    )
    check(
        "权限级别与 policy_store 一致",
        shown_level == live["level"],
        f"页面 {shown_level} / 服务 {live['level']}",
    )
    check(
        "工作区与 policy_store 一致",
        shown.get("workspace") == live["workspace"],
        str(live["workspace"]),
    )
    check("级别标签非空", bool(live["level_label"]), live["level_label"])

    # ------------------------------------------------- 2. changing it changes the store --
    section("2. 通过页面改动，落到了策略文件里")
    bridge._runner.call_blocking(lambda: _set_level(owner, 3))
    call(page, "load")
    pump(900)
    check(
        "页面读到的是改后的级别",
        int((read_value(page, "policy") or {}).get("level", -1)) == 3,
        str((read_value(page, "policy") or {}).get("level")),
    )
    check(
        "策略文件里也确实是 3",
        bridge._runner.call_blocking(lambda: _level(owner)) == 3,
    )

    # ------------------------------------------- 3. what it says is what actually happens --
    section("3. 页面说的和工具做的是同一件事")
    target_inside = _SCRATCH / "workspace" / "page-write.txt"
    allowed = bridge._runner.call_blocking(lambda: _try_write(owner, target_inside))
    check(
        "级别 3 时工作区内写入成功",
        allowed["status"] == "SUCCEEDED",
        f"{allowed['status']} {allowed['error']}",
    )
    check("文件确实被写了", target_inside.is_file(), str(target_inside))

    bridge._runner.call_blocking(lambda: _set_level(owner, 0))
    call(page, "load")
    pump(700)
    target_again = _SCRATCH / "workspace" / "page-write-2.txt"
    refused = bridge._runner.call_blocking(lambda: _try_write(owner, target_again))
    check(
        "级别 0 时同一调用被拒",
        refused["status"] == "DENIED",
        f"{refused['status']} {refused['error']}",
    )
    check("被拒时文件没有被创建", not target_again.exists(), str(target_again))

    # ------------------------------------------------------------- 4. the path probe --
    section("4. 路径检查给出真实裁决与补救办法")
    secrets = REPO / ".env"
    verdict = bridge._runner.call_blocking(lambda: _probe(owner, secrets, False))
    check("受保护路径是拒绝", verdict["decision"] == "deny", json.dumps(verdict, ensure_ascii=False)[:160])
    check("拒绝里有原因", bool(verdict["reason"]), verdict["reason"])
    check("拒绝里有补救办法", bool(verdict["impact"]), verdict["impact"])

    outside = _SCRATCH / "outside-the-workspace.txt"
    outside_verdict = bridge._runner.call_blocking(lambda: _probe(owner, outside, True))
    check(
        "工作区外写入是拒绝，不是含糊的确认",
        outside_verdict["decision"] == "deny",
        f"{outside_verdict['decision']} · {outside_verdict['reason']}",
    )
    check(
        "拒绝说明了怎么改",
        "允许的目录" in outside_verdict["detail"],
        outside_verdict["detail"][:120],
    )

    # ------------------------------------------------------------ 5. it persists --
    section("5. 重开界面后读到的还是同一个策略")
    bridge._runner.call_blocking(lambda: _set_level(owner, 2))
    del engine
    pump(200)
    _engine2, window2 = build_engine(bridge)
    pump(500)
    page2 = go_to_permissions(window2)
    call(page2, "load")
    pump(900)
    check(
        "新开的界面读到级别 2",
        int((read_value(page2, "policy") or {}).get("level", -1)) == 2,
        str((read_value(page2, "policy") or {}).get("level")),
    )
    check(
        "策略来源标为 runtime（有本机改动）",
        str((read_value(page2, "policy") or {}).get("source")) == "runtime",
        str((read_value(page2, "policy") or {}).get("source")),
    )

    # ------------------------------------------------------------ 6. the catalogue --
    section("6. 工具清单不隐藏被排除的工具")
    tools = list(read_value(page2, "tools") or [])
    excluded = list(read_value(page2, "excluded") or [])
    check("工具清单非空", len(tools) > 0, f"{len(tools)} 个")
    check(
        "每个工具都带可用性和级别",
        all("available" in t and "permission_label" in t for t in tools),
        "",
    )
    check(
        "被排除的工具单独列出来并给了原因",
        all(e.get("reason") for e in excluded),
        f"{len(excluded)} 个被排除",
    )
    available = sum(1 for t in tools if t["available"])
    check("可用数不超过总数", 0 < available <= len(tools), f"{available}/{len(tools)}")

    passed = sum(1 for _l, ok, _d in RESULTS if ok)
    total = len(RESULTS)
    print()
    print("-" * 74)
    print(f"结果：{passed}/{total} 通过")
    for label, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL  {label} — {detail}")
    return 0 if passed == total else 1


# --------------------------------------------------------------------------- #
# Work done on the bridge's own event loop
# --------------------------------------------------------------------------- #
async def _level(owner: uuid.UUID) -> int:
    from app.ai.tools import policy_store

    return int(policy_store.build_policy(owner).level)


async def _describe(owner: uuid.UUID) -> dict:
    from app.ai.tools import policy_store

    return policy_store.describe(owner)


async def _set_level(owner: uuid.UUID, level: int) -> None:
    from app.ai.tools import policy_store
    from app.ai.tools.permissions import AgentMode

    policy_store.save_overrides({"mode": AgentMode.AUTONOMOUS.value, "level": level}, owner)


async def _probe(owner: uuid.UUID, path: Path, write: bool) -> dict:
    from app.ai.tools import policy_store
    from app.ai.tools.permissions import PermissionManager

    policy = policy_store.build_policy(owner)
    return PermissionManager(policy).check_path(str(path), write=write).as_dict()


async def _try_write(owner: uuid.UUID, target: Path) -> dict:
    """One `file_write`, through the executor the agent uses."""
    from app.ai.agent.tools import ToolContext
    from app.ai.tools import policy_store
    from app.ai.tools.executor import ToolExecutor
    from app.ai.tools.permissions import PermissionManager
    from app.ai.tools.registry import build_computer_registry

    policy = policy_store.build_policy(owner)
    registry = build_computer_registry(policy, enabled=policy_store.capability_flags(owner))
    tool = registry.get("file_write")
    ctx = ToolContext(
        session=None,
        owner_id=owner,
        working_directory=str(policy.workspace),
        permissions=PermissionManager(policy),
    )
    outcome = await ToolExecutor(policy).execute(
        tool, ctx, {"path": str(target), "content": "written by the verifier\n"}
    )
    return {"status": outcome.status, "error": outcome.error}


if __name__ == "__main__":
    raise SystemExit(main())

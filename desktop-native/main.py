"""Native desktop entry point.

Run it from a checkout::

    .venv\\Scripts\\python.exe desktop-native\\main.py

Or headless, to check it builds and wires up without a display::

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe desktop-native\\main.py --self-check

What this file is responsible for, and nothing else: resolving where its files and the
user's data live, creating the Qt application, registering the bridge under the name QML
expects, and loading `Main.qml`.

It is deliberately thin. Everything interesting is in `awbnative/bridge.py` (the backend
boundary), `awbnative/bootstrap.py` (first-run initialisation) and `qml/` (the interface),
because a UI entry point that also holds business logic is how a rewrite turns into a
second implementation of the product.

**The order of the first two statements below is load-bearing.** `resources.configure()`
must run before anything from `app.*` is imported, because `app.core.config` resolves the
data directory once, at import time. Setting it afterwards changes nothing — and the
symptom is not an error, it is a program quietly writing the user's database into its own
program directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# `resources` imports nothing from the backend, so this is safe at module level. It is also
# the only thing here that may run before the backend is on `sys.path`.
from awbnative import resources

#: Resolved once. From a checkout this points at `desktop-native/`; frozen, at the bundle.
PATHS = resources.configure()

from awbnative.bridge import ServiceBridge
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

# Imported for its side effect as much as for the name. PySide6 only hands back the
# `QQuickWindow` wrapper for `engine.rootObjects()[0]` if that type has been registered
# with the binding system; without this import the same object arrives as a plain
# `QWindow`, which has no `grabWindow`. The symptom is an AttributeError inside a timer
# callback — a screenshot that silently does not appear, not a crash that points here.
from PySide6.QtQuick import QQuickWindow  # noqa: F401


def build_engine(bridge: ServiceBridge) -> QQmlApplicationEngine:
    engine = QQmlApplicationEngine()
    # The name QML uses. `bridge` is the single object the interface talks to; there is
    # no second channel and no implicit global.
    engine.rootContext().setContextProperty("bridge", bridge)
    main_qml = resources.qml_dir() / "Main.qml"
    if not main_qml.is_file():
        raise SystemExit(f"找不到界面文件：{main_qml}")
    engine.load(QUrl.fromLocalFile(str(main_qml)))
    if not engine.rootObjects():
        raise SystemExit("QML 加载失败：Main.qml 没有产生根对象")
    return engine


#: Every QML warning or error the engine emitted while loading, in order.
#:
#: **Why this is collected rather than left on stderr.** A QML error does not stop the
#: window from appearing. The component loads, the broken binding evaluates to `undefined`,
#: and the page renders — it just does not work. Two defects found this way were invisible
#: in a screenshot and in any check that only asked whether a window appeared:
#:
#:   * `property bool busy: bool = false` in DocumentsPage — C++ initialiser syntax, which
#:     QML reads as a write to a global called `bool`. The property was never initialised.
#:   * `ProjectsPage { shell: shell }` in Main.qml — a self-referential binding, because an
#:     unqualified name resolves to the object's own property first. Every page was built
#:     with `shell = null`, so no page could load or write anything.
#:
#: In a packaged build there is no console to read, so a warning that only goes to stderr is
#: a warning nobody sees. Recording them makes the self-check able to fail on them.
QML_MESSAGES: list[str] = []


#: Messages Qt emits that are about this machine, not about the interface.
#:
#: An explicit allowlist rather than a heuristic. "Ignore warnings that look harmless" is
#: how a real warning gets ignored; naming the two that are known and explaining why they
#: are safe means anything new fails the check and has to be looked at.
_BENIGN_QML_MESSAGES = (
    # The offscreen platform and a bare venv ship no font directory. The interface loads
    # its font from disk (see `configure_font`), so this notice does not affect rendering.
    "Cannot find font directory",
    "Note that Qt no longer ships fonts",
)


def _is_benign(message: str) -> bool:
    return any(marker in message for marker in _BENIGN_QML_MESSAGES)


def _watch_qml_messages() -> None:
    """Route Qt's messages into `QML_MESSAGES` as well as to wherever they were going."""
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    def handler(mode: object, ctx: object, message: str) -> None:
        text = str(message)
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            # `ctx` is a QMessageLogContext; the file/line attributes are only present on
            # some PySide6 builds, and a diagnostic must never raise.
            where = ""
            if hasattr(ctx, "file"):
                where = f"{ctx.file}:{ctx.line}"
            if not _is_benign(text):
                QML_MESSAGES.append(f"{text}  <-- {where}" if where else text)
        print(f"[QML] {text}", flush=True)

    qInstallMessageHandler(handler)


def configure_font(app: QGuiApplication) -> str:
    """Pick a font that actually has the glyphs this interface renders.

    The interface is in Chinese. Qt's default font on Windows is a reasonable choice for
    Latin text, and on a machine where the CJK faces are not what Qt picks first, every
    character comes out as a tofu box: the layout is right and nothing is readable.

    Three steps, because three environments differ:

    * **Ask the font database** for a family that exists. The normal path on a real desktop.
    * **Load a font bundled with the build**, when there is one. A packaged build should not
      depend on the host having the font, which is the difference between "works on my
      machine" and "works on a machine with a different language pack".
    * **Load a font file from the system**, as a last resort. The `offscreen` platform used
      for headless verification has no font database at all — it reports zero families — so
      a screenshot taken there is a wall of boxes unless the font comes from disk.
    """
    from PySide6.QtGui import QFont, QFontDatabase

    preferred = (
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "SimHei",
        "Segoe UI",
    )
    families = set(QFontDatabase.families())
    for name in preferred:
        if name in families:
            app.setFont(QFont(name, 10))
            return name

    bundled = (
        sorted((resources.bundle_root() / "fonts").glob("*"))
        if (resources.bundle_root() / "fonts").is_dir()
        else []
    )
    for candidate in [
        *bundled,
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/msyh.ttf"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]:
        if not candidate.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(candidate))
        if font_id < 0:
            continue
        loaded = QFontDatabase.applicationFontFamilies(font_id)
        if loaded:
            app.setFont(QFont(loaded[0], 10))
            return f"{loaded[0]} (loaded from {candidate.name})"

    return app.font().family()


def _write_report(path: str, payload: dict) -> None:
    """Write the verification report where a caller can read it.

    A packaged build is a GUI-subsystem executable, so it has no console: stdout goes
    nowhere and a verification run would have nothing to read. The report is written to a
    file for that reason, and printed as well so a checkout run still shows it.
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"无法写入报告 {path}：{exc}")
    print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-workbench-native")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="build the window, verify it, and exit without waiting for a user",
    )
    parser.add_argument(
        "--owner", default="", help="account id to act as (verification only)"
    )
    parser.add_argument(
        "--screenshot", default="", help="save a PNG of the window and exit"
    )
    parser.add_argument(
        "--report", default="", help="write the self-check result as JSON here"
    )
    args = parser.parse_args(argv)

    if args.self_check or args.screenshot:
        # A display is not needed to prove the QML loads and the bindings resolve.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    # First-run initialisation, before the window exists. Doing it after would show an
    # interface whose every panel reports an error, which reads as a broken application
    # rather than as a missing service.
    import asyncio

    from awbnative import bootstrap

    readiness = asyncio.run(bootstrap.ensure_ready())

    app = QGuiApplication(sys.argv[:1] + ["ai-workbench-native"])
    app.setApplicationName("AI Workbench")
    app.setApplicationVersion(resources.version())
    # Before the engine exists, so nothing is missed.
    _watch_qml_messages()
    font = configure_font(app)

    bridge = ServiceBridge(owner_id=args.owner or None)
    engine = build_engine(bridge)
    window = engine.rootObjects()[0]

    if args.self_check:
        return _run_self_check(window, bridge, readiness, font, args.report)

    for line in bootstrap.summarise(readiness):
        print(line)
    if readiness["failed"]:
        # Present but broken. Starting anyway would put the failure somewhere the user
        # cannot act on it.
        print("启动中止：环境检查未通过。", file=sys.stderr)
        bridge.shutdown()
        return 2

    # A real session: quit when the window closes, and stop the worker thread so the
    # process does not outlive its window.
    app.aboutToQuit.connect(bridge.shutdown)
    return app.exec()


def _run_self_check(
    window: object, bridge: ServiceBridge, readiness: dict, font: str, report: str
) -> int:
    checks = _self_check(window, bridge)
    checks.append(("界面字体已显式选择", bool(font), font))
    # The interface loaded without the engine complaining. A QML error does not stop the
    # window from appearing — it makes a binding evaluate to `undefined` and leaves a
    # control that looks right and does nothing — so "a window appeared" is not the
    # question. This is.
    checks.append(
        (
            "QML 加载无警告或错误",
            not QML_MESSAGES,
            "；".join(QML_MESSAGES[:3]) if QML_MESSAGES else "无",
        )
    )
    bridge.shutdown()
    # The environment checks are reported with the same three states the bootstrap uses.
    # `blocked` is not a failure of this build, and calling it one would make the
    # acceptance result depend on which services happen to be running on the machine
    # rather than on whether the package works.
    environment = []
    for name in ("database", "redis", "qdrant"):
        entry = readiness.get(name) or {}
        # Every field the check produced, not just the three this knows about. A diagnostic
        # that runs and is then dropped on the way into the report is a diagnostic nobody
        # will ever read.
        environment.append({"name": name, **entry})

    payload = {
        "version": resources.version(),
        "frozen": resources.is_frozen(),
        "executable": sys.executable,
        "bundle_root": str(resources.bundle_root()),
        "data_dir": str(resources.data_dir()),
        "runtime": readiness.get("runtime", {}),
        "font": font,
        "checks": [
            {"label": label, "ok": ok, "detail": detail} for label, ok, detail in checks
        ],
        "environment": environment,
        "blocked": readiness.get("blocked", []),
        "failed": readiness.get("failed", []),
        "passed": all(ok for _l, ok, _d in checks) and not readiness.get("failed"),
    }

    for label, ok, detail in checks:
        print(
            f"  [{'PASS' if ok else 'FAIL'}] {label}"
            + (f" — {detail}" if detail else "")
        )
    for name in readiness.get("blocked", []):
        print(f"  [BLOCKED] {name} 在本机不可用")
    for name in readiness.get("failed", []):
        print(f"  [FAIL] {name} 存在但不可用")

    if report:
        _write_report(report, payload)
    return 0 if payload["passed"] else 1


def _self_check(window: object, bridge: ServiceBridge) -> list[tuple[str, bool, str]]:
    """Assert the things a screenshot cannot: that the window is real and wired."""
    checks: list[tuple[str, bool, str]] = []

    title = str(window.property("title") or "")  # type: ignore[attr-defined]
    checks.append(("窗口有标题", bool(title), title))

    width = int(window.property("width") or 0)  # type: ignore[attr-defined]
    height = int(window.property("height") or 0)  # type: ignore[attr-defined]
    checks.append(("窗口有尺寸", width > 0 and height > 0, f"{width}x{height}"))

    visible = bool(window.property("visible"))  # type: ignore[attr-defined]
    checks.append(("窗口可见", visible, str(visible)))

    # The bridge must be reachable from QML under the expected name, with the slots the
    # interface calls. A missing slot is a runtime error the user would see as a dead
    # button, not as a crash.
    required = (
        "loadDashboard",
        "loadTasks",
        "loadProjects",
        "loadAiConfig",
        "saveAiConfig",
        "createTask",
        "completeTask",
        "startAgentRun",
        "cancelAgentRun",
        "saveTextFile",
        "openPath",
        "loadRuntimeInfo",
    )
    missing = [name for name in required if not hasattr(bridge, name)]
    checks.append(
        (
            "bridge 暴露了全部槽",
            not missing,
            ", ".join(missing) or f"{len(required)} 个",
        )
    )

    children = window.findChildren(object)  # type: ignore[attr-defined]
    checks.append(("QML 对象树非空", len(children) > 5, f"{len(children)} 个对象"))

    return checks


def _screenshot(app: QGuiApplication, window: object, target: Path) -> int:
    from PySide6.QtCore import QTimer

    def grab() -> None:
        # `grabWindow` is on QQuickWindow, which is what an ApplicationWindow's root
        # object is. Calling `grab()` instead raises AttributeError inside a timer
        # callback — where it does not stop the app, it just leaves no file behind, which
        # is a worse failure than a crash.
        try:
            image = window.grabWindow()  # type: ignore[attr-defined]
        except AttributeError as exc:
            print(f"screenshot failed: {exc}")
            app.quit()
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        saved = image.save(str(target))
        print(
            f"screenshot -> {target} "
            f"({'ok' if saved else 'FAILED'}, {image.width()}x{image.height()})"
        )
        app.quit()

    QTimer.singleShot(1500, grab)
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

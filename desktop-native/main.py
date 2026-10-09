"""Native desktop entry point.

Run it::

    .venv\\Scripts\\python.exe desktop-native\\main.py

Or headless, to check it builds and wires up without a display::

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe desktop-native\\main.py --self-check

What this file is responsible for, and nothing else: creating the Qt application, putting
the backend directory on `sys.path` so the service layer is importable, registering the
bridge under the name QML expects, and loading `Main.qml`.

It is deliberately thin. Everything interesting is in `awbnative/bridge.py` (the backend
boundary) and `qml/` (the interface), because a UI entry point that also holds business
logic is how a rewrite turns into a second implementation of the product.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
BACKEND = PROJECT_ROOT / "backend"

# The service layer lives in the backend package. It is imported, not called over HTTP —
# that is the whole point of this shell, so the path goes on `sys.path` rather than an
# address going in a config file.
for candidate in (str(BACKEND), str(HERE)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from PySide6.QtCore import QUrl, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

# Imported for its side effect as much as for the name. PySide6 only hands back the
# `QQuickWindow` wrapper for `engine.rootObjects()[0]` if that type has been registered
# with the binding system; without this import the same object arrives as a plain
# `QWindow`, which has no `grabWindow`. The symptom is an AttributeError inside a timer
# callback — a screenshot that silently does not appear, not a crash that points here.
from PySide6.QtQuick import QQuickWindow  # noqa: E402, F401

from awbnative.bridge import ServiceBridge  # noqa: E402


def build_engine(bridge: ServiceBridge) -> QQmlApplicationEngine:
    engine = QQmlApplicationEngine()
    # The name QML uses. `bridge` is the single object the interface talks to; there is
    # no second channel and no implicit global.
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(HERE / "qml" / "Main.qml")))
    if not engine.rootObjects():
        raise SystemExit("QML 加载失败：Main.qml 没有产生根对象")
    return engine


def configure_font(app: QGuiApplication) -> str:
    """Pick a font that actually has the glyphs this interface renders.

    The interface is in Chinese. Qt's default font on Windows is a reasonable choice for
    Latin text, and on a machine where the CJK faces are not what Qt picks first, every
    character comes out as a tofu box: the layout is right and nothing is readable.

    Two steps, because the two environments differ:

    * **Ask the font database** for a family that exists. This is the normal path on a
      real desktop.
    * **Load a font file directly** when the database comes back empty. The `offscreen`
      platform used for headless verification has no font database at all — it reports
      zero families — so a screenshot taken there is a wall of boxes unless the font is
      loaded from disk. Doing it this way also means a packaged build does not depend on
      the host having the font, which is the difference between "works on my machine" and
      "works on a machine with a different language pack".
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

    for candidate in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/msyh.ttf"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ):
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-workbench-native")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="build the window, verify it, and exit without waiting for a user",
    )
    parser.add_argument("--owner", default="", help="account id to act as (verification only)")
    parser.add_argument("--screenshot", default="", help="save a PNG of the window and exit")
    args = parser.parse_args(argv)

    if args.self_check or args.screenshot:
        # A display is not needed to prove the QML loads and the bindings resolve.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    app = QGuiApplication(sys.argv[:1] + ["ai-workbench-native"])
    app.setApplicationName("AI Workbench")
    app.setApplicationVersion(_version())
    font = configure_font(app)

    bridge = ServiceBridge(owner_id=args.owner or None)
    engine = build_engine(bridge)

    window = engine.rootObjects()[0]

    if args.self_check:
        checks = _self_check(window, bridge)
        checks.append(("界面字体已显式选择", bool(font), font))
        for label, ok, detail in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
        bridge.shutdown()
        return 0 if all(ok for _l, ok, _d in checks) else 1

    if args.screenshot:
        return _screenshot(app, window, Path(args.screenshot))

    # A real session: quit when the window closes, and stop the worker thread so the
    # process does not outlive its window.
    app.aboutToQuit.connect(bridge.shutdown)
    return app.exec()


def _version() -> str:
    try:
        return (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0-unknown"


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
    checks.append(("bridge 暴露了全部槽", not missing, ", ".join(missing) or f"{len(required)} 个"))

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

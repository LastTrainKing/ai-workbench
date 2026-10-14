"""Real-display screenshots of every page — the check that `offscreen` cannot make.

**Why this exists, and why it is not `shot_pages.py`.** That one runs on the offscreen
platform, which on this machine reports *zero font families*. It renders the same QML and
proves the layout, but it cannot show a glyph — and a missing font fallback is exactly the
defect it therefore misses. This project has already shipped one: an employee's avatar
initial was changed to an emoji, the font chain had no pictogram font, and the offscreen
harness reported a clean render while the real window drew a tofu block.

So this tool does the opposite: it uses the real screen, the real font stack and the real
window, and it saves PNGs a person can look at.

It loads `Main.qml` the way the application does, navigates to each page, waits for the
bridge to stop having requests in flight, and grabs the window. Nothing here imports the
product's entry point — `main.py` is untouched, which matters because it is inside the
installer payload and a change to it invalidates a built installer.

    python tools/shot_real_pages.py                     # the default page set
    python tools/shot_real_pages.py employees memory    # only these
    python tools/shot_real_pages.py --out build/shots   # where to write

Requires a real display. It will take over the screen for a few seconds per page.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# A real screen is the entire point, so `QT_QPA_PLATFORM` is *removed* rather than set.
# Anything that leaves it at `offscreen` turns this into the tool it exists to replace.
os.environ.pop("QT_QPA_PLATFORM", None)
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/aiworkbench"
)

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "desktop-native"))

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

#: Pages worth a picture, in the order a person would meet them. The keys are the ones in
#: `theme.js`'s `navGroups`.
DEFAULT_PAGES = [
    "dashboard",
    "employees",
    "deliverables",
    "agent",
    "memory",
    "knowledge",
    "settings",
]

app = QGuiApplication([*sys.argv[:1], "shot-real-pages"])

from awbnative import resources  # noqa: E402

resources.configure()

#: The engine is the window's owner. Created locally it is collected at the end of the
#: function, the window goes with it, and every later `setProperty` raises
#: "Internal C++ object already deleted". Same trap `verify_projection.py` documents.
_KEEP_ALIVE: list = []


def _load_shell():
    import importlib.util

    from awbnative.bridge import ServiceBridge

    spec = importlib.util.spec_from_file_location("awbmain", REPO / "desktop-native" / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.configure_font(app)

    bridge = ServiceBridge(owner_id=None)
    bridge.record_startup({}, {"ready": True})
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(REPO / "desktop-native" / "qml" / "Main.qml")))
    if not engine.rootObjects():
        raise SystemExit("Main.qml did not load — nothing to photograph")
    window = engine.rootObjects()[0]
    _KEEP_ALIVE.extend([engine, bridge, window])
    return bridge, window


def _pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def _pump_until(predicate, timeout_ms: int) -> bool:
    """Pump until the condition holds. A fixed wait is a guess about query latency."""
    end = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.1)
    app.processEvents()
    return bool(predicate())


def main() -> int:
    args = sys.argv[1:]
    out = REPO / "build" / "rc" / "real-pages"
    if "--out" in args:
        i = args.index("--out")
        out = Path(args[i + 1])
        del args[i : i + 2]
    pages = [a for a in args if not a.startswith("-")] or DEFAULT_PAGES
    out.mkdir(parents=True, exist_ok=True)

    bridge, window = _load_shell()
    window.setProperty("width", 1280)
    window.setProperty("height", 860)
    _pump(1200)

    written: list[Path] = []
    for page in pages:
        window.navigate(page, None)
        # Watch the bridge's in-flight counter rather than sleeping: the panels fill in
        # when the queries return, and that is not a moment anyone can guess.
        _pump_until(lambda: bridge._runner.inflight == 0, timeout_ms=25000)
        # One more settle so the last frame is painted before it is photographed.
        _pump(700)
        image = window.grabWindow()
        target = out / f"{page}.png"
        ok = image.save(str(target))
        print(
            f"  {'ok ' if ok else 'FAIL'} {page:12s} {image.width()}x{image.height()} -> {target}",
            flush=True,
        )
        if ok:
            written.append(target)

    bridge.shutdown()
    print(f"\n{len(written)}/{len(pages)} 张 -> {out}", flush=True)
    return 0 if len(written) == len(pages) else 1


if __name__ == "__main__":
    raise SystemExit(main())

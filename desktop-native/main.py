"""Native desktop entry point.

Run it from a checkout::

    .venv\\Scripts\\python.exe desktop-native\\main.py

Or headless, to check it builds and wires up without a display::

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe desktop-native\\main.py --self-check

What this file is responsible for, and nothing else: resolving where its files and the
user's data live, creating the Qt application, registering the bridge under the name QML
expects, loading `Main.qml`, and starting and stopping the local services.

It is deliberately thin. Everything interesting is in `awbnative/bridge.py` (the backend
boundary), `awbnative/bootstrap.py` (first-run initialisation), `awbnative/runtime.py` (the
local database stack) and `qml/` (the interface), because a UI entry point that also holds
business logic is how a rewrite turns into a second implementation of the product.

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
import time
import traceback
from pathlib import Path


#: Where a fatal startup error is written when nothing else can report it.
#:
#: **A packaged build is a GUI-subsystem executable: it has no console.** An exception
#: raised while the modules are being imported therefore produces a message box that waits
#: for a click, and nothing at all on disk — which is what happened: the installed
#: application hung for ten minutes in the installer acceptance and left no trace, because
#: there was nowhere for the trace to go. The acceptance had to be run under `timeout` and
#: then reasoned about from the absence of a file.
#:
#: So the crash log is installed **before the first import that can fail**, and it writes
#: to a path it can work out without any of the code below it having run.
def _crash_log_path() -> Path:
    explicit = os.environ.get("AI_WORKBENCH_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit) / "logs" / "native-crash.log"
    base = os.environ.get("LOCALAPPDATA", "").strip()
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "AI Workbench Data" / ".runtime" / "logs" / "native-crash.log"


def _record_fatal(stage: str) -> Path | None:
    """Write the traceback somewhere a person can find it. Best effort, never raises."""
    text = traceback.format_exc()
    for candidate in (
        _crash_log_path(),
        Path(sys.executable).resolve().parent / "native-crash.log",
        Path.cwd() / "native-crash.log",
    ):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with candidate.open("a", encoding="utf-8") as handle:
                handle.write(f"\n===== {stage} =====\n{text}\n")
            return candidate
        except OSError:
            continue
    return None


try:
    # `resources` imports nothing from the backend, so this is safe at module level. It is
    # also the only thing here that may run before the backend is on `sys.path`.
    from awbnative import resources, window_frame

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
except BaseException:  # noqa: BLE001 - this is the last line of defence
    # A failure here is fatal and there is no window to show it in. Exit code 70 is
    # deliberately not 1 or 2: a caller that launched the application can tell "it refused
    # to start" from "it started and the user closed it", and the traceback is in
    # `native-crash.log` under the data directory.
    _record_fatal("import")
    raise SystemExit(70) from None


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


def _say(line: str) -> None:
    """Print, if there is anywhere to print to.

    A packaged Windows build is a GUI-subsystem executable and has **no console**:
    `sys.stdout` and `sys.stderr` are `None`, and `print` to them raises
    `RuntimeError: lost sys.stdout`. That matters because the Qt message handler below
    prints every message — and Qt emits one on every start (the missing font directory),
    from inside a callback the engine invokes. An exception raised there is reported as a
    QML problem rather than as what it is.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.write(line + "\n")
            stream.flush()
        except (OSError, ValueError):
            pass
        return


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
        _say(f"[QML] {text}")

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
    from PySide6.QtGui import QFontDatabase

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
            _apply_interface_font(app, name)
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
            _apply_interface_font(app, loaded[0])
            return f"{loaded[0]} (loaded from {candidate.name})"

    return app.font().family()


#: Families tried *after* the text face, per character.
#:
#: Qt's font engine walks the list when a glyph is missing from the first entry, which is
#: the only way to express "use this face, and for anything it does not have, use that one".
#: A single family name cannot say that.
#:
#: **This is the same defect `configure_font` exists to prevent, one character class over.**
#: Microsoft YaHei has no pictograms, so a role mark came out as a tofu box: the layout was
#: right and nothing was readable. Rather than avoid emoji in the interface, give the font
#: somewhere to find them — this fixes every pictogram in the product, not the one that was
#: noticed.
EMOJI_FALLBACKS: tuple[str, ...] = (
    "Segoe UI Emoji",  # Windows
    "Apple Color Emoji",  # macOS
    "Noto Color Emoji",  # Linux
    "Noto Emoji",
    "Segoe UI Symbol",
)

#: The same faces, by file.
#:
#: Loaded from disk for the same reason the CJK faces are: the `offscreen` platform used by
#: headless verification reports **zero** font families, so a lookup by name finds nothing
#: and a screenshot shows boxes on a machine that has the font. A packaged build should not
#: depend on the host's language packs either.
EMOJI_FONT_FILES: tuple[str, ...] = (
    "C:/Windows/Fonts/seguiemj.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
)


def _emoji_families() -> list[str]:
    """Emoji families this machine can offer, by name or by file."""
    from PySide6.QtGui import QFontDatabase

    available = set(QFontDatabase.families())
    found = [name for name in EMOJI_FALLBACKS if name in available]
    for path in EMOJI_FONT_FILES:
        candidate = Path(path)
        if not candidate.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(candidate))
        if font_id < 0:
            continue
        for family in QFontDatabase.applicationFontFamilies(font_id):
            if family not in found:
                found.append(family)
    return found


def _apply_interface_font(app: QGuiApplication, family: str) -> None:
    """Set the interface font, with emoji fallbacks behind it."""
    from PySide6.QtGui import QFont

    font = QFont(family, 10)
    fallbacks = _emoji_families()
    if hasattr(font, "setFamilies"):
        font.setFamilies([family, *fallbacks])
    app.setFont(font)


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
        _say(f"无法写入报告 {path}：{exc}")
    _say(text)


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
    parser.add_argument(
        "--windowed",
        action="store_true",
        help="with --self-check, use a real window so the DWM frame can be verified",
    )
    parser.add_argument(
        "--stop-services",
        action="store_true",
        help="stop the local services this copy started, then exit",
    )
    args = parser.parse_args(argv)

    if (args.self_check or args.screenshot) and not args.windowed:
        # A display is not needed to prove the QML loads and the bindings resolve.
        #
        # `--windowed` opts out, because two of the things worth checking are properties of a
        # real window: the DWM frame and the taskbar icon. On the offscreen platform there is
        # no window handle, so those calls have nothing to talk to and the checks would be
        # reporting on the measuring code instead of on the product.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


    from awbnative import bootstrap, runtime

    if args.stop_services:
        # Only the scoped sweep: pids recorded under this data directory whose executable
        # really lives under this product. It is not a service manager and it is not a
        # `taskkill /IM`.
        result = runtime.stop()
        _say(f"停止本地服务：{result['state']} — {result['detail']}")
        return 0 if result["state"] == "ok" else 1

    # A headless run waits **before** the window, because its whole purpose is to report a
    # complete environment and a report written while the stack is still starting would be
    # a report about nothing. A real launch waits **after**, so the user is not staring at
    # an empty desktop while a database initialises.
    headless = bool(args.self_check or args.screenshot)
    runtime_state = runtime.ensure() if headless else None

    app = QGuiApplication(sys.argv[:1] + ["ai-workbench-native"])
    app.setApplicationName("AI Workbench")
    app.setApplicationVersion(resources.version())

    # **The window had no icon at all.** The executable's resource has carried
    # `assets/ai-workbench.ico` for a while and Explorer, the shortcuts and the installer all
    # show it, but the window showed the Windows placeholder — because Qt sends the window
    # icon from `QGuiApplication::windowIcon()`, nothing called `setWindowIcon`, and an
    # empty icon is not the same as no icon: Windows draws its own default instead of
    # falling back to the executable's.
    #
    # Set here, before `build_engine`, because the icon is taken when the window is created.
    icon = resources.icon_path()
    if icon is not None:
        from PySide6.QtGui import QIcon

        app.setWindowIcon(QIcon(str(icon)))
    # Before the engine exists, so nothing is missed.
    _watch_qml_messages()
    font = configure_font(app)

    bridge = ServiceBridge(owner_id=args.owner or None)

    readiness = None
    if headless:
        # **On the runner's loop, not through `asyncio.run`.** The readiness probe queries
        # the database, which opens pooled connections; `asyncio.run` closes its loop on the
        # way out, and those connections then belong to a loop that no longer exists. Every
        # page call afterwards fails with
        # `AttributeError: 'NoneType' object has no attribute 'send'` — a screenshot of a
        # failure that has nothing to do with what was being photographed. A real launch
        # never had this because it probes inside `_bring_up`, on this same loop.
        readiness = bridge._runner.call_blocking(bootstrap.ensure_ready)

    engine = build_engine(bridge)
    window = engine.rootObjects()[0]

    # The system frame, in the application's colours. Not a custom title bar: the native one
    # keeps dragging, snapping, double-click-to-maximise, the system menu, per-monitor DPI
    # and the resize borders, and this only changes how it is painted.
    # The return value is not kept: what matters is what the frame ends up set to, and
    # `_self_check` reads that back from the window rather than trusting this call.
    window_frame.apply_dark_frame(window)

    # Both headless paths stop what they started. The GUI gets this from `aboutToQuit`; a
    # run that never shows a window has no such moment, and without it `--screenshot` starts
    # PostgreSQL, Redis and Qdrant and walks away. The next launch then reuses that orphan
    # stack, correctly declines to stop it — "this process did not start it" — and three
    # engines are still holding their ports after the window closes.
    if args.self_check:
        assert runtime_state is not None and readiness is not None
        try:
            return _run_self_check(window, bridge, runtime_state, readiness, font, args.report)
        finally:
            bridge.stopEnvironment()

    if args.screenshot:
        assert runtime_state is not None and readiness is not None
        # The stack was brought up *before* the window here, so the shell has to be told —
        # otherwise every page's first call waits for a startup that already happened and
        # the screenshot is of empty panels.
        bridge.record_startup(runtime_state.as_dict(), readiness)
        try:
            return _screenshot(app, window, Path(args.screenshot), bridge)
        finally:
            bridge.stopEnvironment()

    for line in runtime.summarise(runtime_state) if runtime_state else []:
        _say(line)
    for line in bootstrap.summarise(readiness) if readiness else []:
        _say(line)

    # The window is up. Bring the stack up behind it and report into the shell's banner.
    bridge.startEnvironment()
    app.aboutToQuit.connect(bridge.shutdown)
    try:
        return app.exec()
    finally:
        # In a `finally` as well as on `aboutToQuit`, because a GUI application can be
        # closed in more than one way and only one of them is the close button. A stack
        # that outlives the window holds port 5432 and the next launch fails against a
        # cluster whose owner is a program the user already closed.
        bridge.stopEnvironment()


def _run_self_check(
    window: object,
    bridge: ServiceBridge,
    runtime_state: object,
    readiness: dict,
    font: str,
    report: str,
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
    runtime_dict = runtime_state.as_dict() if runtime_state is not None else {}
    # Give the shell the same thing a real launch would have shown it. The banner is part
    # of the interface, and a check that reads it must read what the user would see.
    bridge.record_startup(runtime_dict, readiness or {})
    checks.append(
        (
            "本地服务可用",
            runtime_dict.get("state") == "ok",
            str(runtime_dict.get("detail") or ""),
        )
    )
    checks.append(
        (
            "启动状态可在界面上读到",
            bridge.startupState in {"ready", "degraded", "failed"},
            f"startupState={bridge.startupState}",
        )
    )
    # **放在 `record_startup` 之后。** 放在 `_self_check` 里会踩到一个真实的顺序问题：
    # 自检期间 `startup_state` 还是 `idle`，而 `_after_environment` 会为一个没说就绪的环境
    # 空等 `READY_WAIT_SECONDS`（240 秒）—— 那不是"槽坏了"，是"环境还没说就绪"。
    # 放在这里，跑的就是用户按按钮时那个状态。
    checks.append(_reverify_check(bridge))

    bridge.shutdown()
    # The environment checks are reported with the same three states the bootstrap uses.
    # `blocked` is not a failure of this build, and calling it one would make the
    # acceptance result depend on which services happen to be running on the machine
    # rather than on whether the package works.
    environment = []
    for name in ("database", "redis", "qdrant"):
        entry = (readiness or {}).get(name) or {}
        # Every field the check produced, not just the three this knows about. A diagnostic
        # that runs and is then dropped on the way into the report is a diagnostic nobody
        # will ever read.
        environment.append({"name": name, **entry})

    payload = {
        "version": resources.version(),
        "frozen": resources.is_frozen(),
        "executable": sys.executable,
        "bundle_root": str(resources.bundle_root()),
        "app_root": str(resources.app_root()),
        "data_dir": str(resources.data_dir()),
        "runtime": (readiness or {}).get("runtime", {}),
        "services": runtime_dict,
        "font": font,
        "checks": [
            {"label": label, "ok": ok, "detail": detail} for label, ok, detail in checks
        ],
        "environment": environment,
        "blocked": (readiness or {}).get("blocked", []),
        "failed": (readiness or {}).get("failed", []),
        "passed": all(ok for _l, ok, _d in checks) and not (readiness or {}).get("failed"),
    }

    for label, ok, detail in checks:
        _say(
            f"  [{'PASS' if ok else 'FAIL'}] {label}"
            + (f" — {detail}" if detail else "")
        )
    for name in (readiness or {}).get("blocked", []):
        _say(f"  [BLOCKED] {name} 在本机不可用")
    for name in (readiness or {}).get("failed", []):
        _say(f"  [FAIL] {name} 存在但不可用")

    if report:
        _write_report(report, payload)
    return 0 if payload["passed"] else 1


async def _seed_deliverable(bridge: ServiceBridge) -> dict[str, str]:
    """A real file and a real row, owned by this install's account.

    Seeded rather than produced by a model run: this check is about the button, not about
    the model, and a check that needs a paid key is a check that stops running on the
    machines where it matters.
    """
    from app.core.config import settings
    from app.infrastructure.database import session_scope
    from app.models.delivery import BuildRun, BuildStatus, Deliverable

    owner = await bridge._owner()
    storage = Path(settings.storage_dir) / "self-check-reverify"
    storage.mkdir(parents=True, exist_ok=True)
    target = storage / "self-check.md"
    target.write_text("# 自检\n\n这一行是为了让「重新验证」有东西可读。\n", encoding="utf-8")

    async with session_scope() as session:
        run = BuildRun(
            user_id=owner,
            goal="[self-check] 重新验证",
            title="self-check",
            deliverable_kind="md",
            status=BuildStatus.COMPLETED.value,
        )
        session.add(run)
        await session.flush()
        item = Deliverable(
            user_id=owner,
            build_run_id=run.id,
            path=str(target),
            kind="md",
            title="self-check",
            size_bytes=target.stat().st_size,
        )
        session.add(item)
        await session.flush()
        return {"deliverable_id": str(item.id), "path": str(target)}


def _reverify_check(bridge: ServiceBridge) -> tuple[str, bool, str]:
    """Drive the 「重新验证」 path exactly as the button does, inside the packaged app.

    **Why this is here and not only in the test suite.** The button was broken by a
    positional call to a keyword-only service method, and every other call site in the
    repository passed keywords — so the suite stayed green while the one path a user can
    reach raised `TypeError` on every press. A test that drives the slot covers the
    convention; this covers the **shipped binary**, which is the thing that was wrong.

    It runs the real bridge slot against a real file and a real row, and it reads back what
    the slot produced. On the broken code the failure path fires with the `TypeError` in it.
    """
    from PySide6.QtCore import QCoreApplication

    label = "「重新验证」这条路径能跑通"
    try:
        seeded = bridge._runner.call_blocking(lambda: _seed_deliverable(bridge), timeout=60.0)
    except Exception as exc:  # noqa: BLE001
        return (label, False, f"造交付物失败 {type(exc).__name__}: {exc}")

    request_id = "self-check-reverify"
    outcome: dict[str, str] = {}

    def on_completed(rid: str, payload: str) -> None:
        if rid == request_id:
            outcome["payload"] = payload

    def on_failed(rid: str, message: str, code: str) -> None:
        if rid == request_id:
            outcome["failure"] = f"{code}: {message}"

    bridge.completed.connect(on_completed)
    bridge.failed.connect(on_failed)
    try:
        bridge.verifyDeliverable(request_id, seeded["deliverable_id"])
        deadline = time.monotonic() + 60.0
        while not outcome and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.05)
    finally:
        bridge.completed.disconnect(on_completed)
        bridge.failed.disconnect(on_failed)

    if not outcome:
        return (label, False, "60 秒内既没有成功也没有失败 —— 这个槽没有回话")
    if "failure" in outcome:
        # **这一行就是缺陷的样子。** 按位置传参时它是
        # `TypeError: verify_deliverable() takes 1 positional argument but 2 were given`。
        return (label, False, outcome["failure"])

    import json as _json

    try:
        parsed = _json.loads(outcome["payload"])
    except ValueError as exc:
        return (label, False, f"回话不是 JSON：{exc}")

    verification = (parsed or {}).get("verification") or {}
    checks = verification.get("checks") or []
    if not checks:
        return (label, False, f"拿到了回话，但里面没有验证证据：{outcome['payload'][:160]}")
    outcomes = [str(c.get("outcome")) for c in checks]
    return (
        label,
        True,
        f"{len(checks)} 项检查（{', '.join(outcomes)}），passed={verification.get('passed')}",
    )


def _self_check(window: object, bridge: ServiceBridge) -> list[tuple[str, bool, str]]:
    """Assert the things a screenshot cannot: that the window is real and wired."""
    checks: list[tuple[str, bool, str]] = []

    title = str(window.property("title") or "")  # type: ignore[attr-defined]
    checks.append(("窗口有标题", bool(title), title))

    # Read back from the window, not from the call that was supposed to set it. The icon was
    # missing for the whole life of this product while the code that should have set it was
    # one line away from being written.
    window_icon = QGuiApplication.windowIcon()
    sizes = window_icon.availableSizes()
    checks.append(
        (
            "窗口有应用图标",
            not window_icon.isNull(),
            "，" .join(f"{size.width()}x{size.height()}" for size in sizes[:4])
            if sizes
            else "图标为空",
        )
    )

    # **Only when there is a real window.** On the offscreen platform there is no handle for
    # DWM to act on, so the item is left out rather than reported as a pass — a check that
    # cannot fail is a check that says nothing.
    #
    # `dark_mode` is read back because DWM will tell us. The caption colour is *write-only*
    # on this build: `DwmGetWindowAttribute(DWMWA_CAPTION_COLOR)` returns E_INVALIDARG even
    # after the set succeeds, so the colour is verified by looking at the window, not here.
    if QGuiApplication.platformName() != "offscreen":
        frame = window_frame.read_frame(window)
        dark = frame.get("dark_mode")
        checks.append(
            (
                "系统标题栏是深色",
                dark == 1,
                f"dark_mode={dark}（1=深色），标题栏色已设为 {window_frame.CAPTION}",
            )
        )

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
        "loadProjectDetail",
        "loadAiConfig",
        "saveAiConfig",
        "testAiConnection",
        "createTask",
        "completeTask",
        "startAgentRun",
        "cancelAgentRun",
        "saveTextFile",
        "openPath",
        "loadRuntimeInfo",
        "startEnvironment",
        "stopEnvironment",
        "environmentReport",
        "loadRuns",
        "loadRunDetail",
        "reconcileRun",
        "streamChat",
        "loadPermissions",
        "savePermissions",
        "resetPermissions",
        "loadCapabilities",
        "saveCapabilities",
        "loadTools",
        "checkPermission",
        "search",
        "loadGraph",
        "loadTaskTree",
        "updateTask",
        "deleteTask",
        "loadEmbeddingStatus",
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


def _screenshot(app: QGuiApplication, window: object, target: Path, bridge: ServiceBridge) -> int:
    """Grab the window once the page has finished asking for its data.

    A fixed delay was the first version and it photographed whatever happened to be there:
    the panels fill in when the queries come back, which is after the window is up and not
    at a time that can be guessed. The bridge knows how many calls are still in flight, so
    this waits for that count to reach zero — which is what "the page has loaded" means
    here, and it is the same number a stuck page would leave above zero.
    """
    from PySide6.QtCore import QTimer

    deadline = time.monotonic() + 30.0

    def grab() -> None:
        # `grabWindow` is on QQuickWindow, which is what an ApplicationWindow's root
        # object is. Calling `grab()` instead raises AttributeError inside a timer
        # callback — where it does not stop the app, it just leaves no file behind, which
        # is a worse failure than a crash.
        try:
            image = window.grabWindow()  # type: ignore[attr-defined]
        except AttributeError as exc:
            _say(f"screenshot failed: {exc}")
            app.quit()
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        saved = image.save(str(target))
        _say(
            f"screenshot -> {target} "
            f"({'ok' if saved else 'FAILED'}, {image.width()}x{image.height()})"
        )
        app.quit()

    def wait_then_grab() -> None:
        if bridge._runner.inflight and time.monotonic() < deadline:
            QTimer.singleShot(200, wait_then_grab)
            return
        grab()

    QTimer.singleShot(1500, wait_then_grab)
    app.exec()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - a windowed build has nowhere else to report
        _record_fatal("main")
        raise SystemExit(71) from None

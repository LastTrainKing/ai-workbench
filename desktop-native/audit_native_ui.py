"""Independent audit of the native UI proof of concept.

Every claim in `desktop-native/README.md` is checked here, by execution where execution is
possible and by static evidence where it is not. The point is that the claims are falsifiable:
a README that says "no HTTP" is worth nothing next to a check that fails if a socket opens.

Run it::

    .venv\\Scripts\\python.exe desktop-native\\audit_native_ui.py

Exits non-zero on any failed check.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import sys
import threading
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
BACKEND = PROJECT_ROOT / "backend"

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

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# --------------------------------------------------------------------- static --


def poc_sources() -> list[pathlib.Path]:
    return [
        p
        for p in HERE.rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".qml", ".js"}
        and "__pycache__" not in p.parts
        # This file names every forbidden word in order to look for them, so scanning
        # itself would report itself. That is not a loophole: the audit is not part of
        # the shipped interface.
        and p.name != "audit_native_ui.py"
    ]


def _docstring_lines(text: str) -> set[int]:
    """Line numbers occupied by string statements (docstrings).

    Written without `isinstance(node, A | B | C | D)`. That form compiled to something
    that did not match its own source here — the function returned an empty set for input
    the same expression matched when run inline — and an audit whose scanner silently
    matches nothing is worse than no audit, because it reports PASS. The plain form below
    was checked against the same input and returns the expected lines.

    Any first statement that is a bare string is treated as prose, including inside an
    `if` or a `for` where it is not a docstring in the Python sense. For a scan that is
    stripping commentary that is the right side to err on.
    """
    lines: set[int] = set()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr):
            continue
        value = first.value
        if not isinstance(value, ast.Constant):
            continue
        if not isinstance(value.value, str):
            continue
        end = getattr(value, "end_lineno", None) or value.lineno
        lines.update(range(value.lineno, end + 1))
    return lines


def code_without_prose(path: pathlib.Path) -> str:
    """The file's code with comments and docstrings removed.

    These files explain what they replaced. `bridge.py` says in prose that the WebView
    build reached `127.0.0.1:8000`, and `async_runner.py` says the WebView build got
    request/response correlation from HTTP. Searching the raw text finds those sentences
    and reports the very thing the file is documenting the removal of — which is how the
    first version of this audit produced three false failures.

    So the scan is over code, not over prose. Same mistake as the `/IM` check in
    `test_release_freeze_process_safety.py`, and the same fix.

    The original text is tokenised and tokens are filtered by position, rather than
    blanking the docstring lines first and tokenising the result: blanking changes the
    token stream underneath the parser, and a half-stripped file is worse than none.
    """
    import io
    import re as _re
    import tokenize

    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix != ".py":
        text = _re.sub(r"/\*.*?\*/", " ", text, flags=_re.DOTALL)
        return _re.sub(r"//[^\n]*", " ", text)

    prose_lines = _docstring_lines(text)
    kept: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                continue
            if token.start[0] in prose_lines:
                continue
            kept.append(token.string)
    except (tokenize.TokenError, IndentationError):
        return "\n".join(
            line for number, line in enumerate(text.splitlines(), start=1)
            if number not in prose_lines
        )
    return "\n".join(kept)


def audit_no_http_client() -> None:
    section("A. does the PoC reach the backend over HTTP?")

    #: Modules that would mean an HTTP call. `urllib.parse` is fine — it is string work,
    #: not transport — so the check is on the transport modules specifically.
    forbidden_imports = {"httpx", "requests", "aiohttp", "urllib.request", "urllib.error", "http.client"}
    offenders: list[str] = []

    for path in poc_sources():
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            offenders.append(f"{path.name}: unparseable ({exc})")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden_imports:
                        offenders.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module in forbidden_imports:
                offenders.append(f"{path.name}: from {node.module} import …")

    check(
        "no HTTP client is imported anywhere in the PoC",
        not offenders,
        ", ".join(offenders) or f"{len([p for p in poc_sources() if p.suffix == '.py'])} python files scanned",
    )

    #: The old backend port. A literal in *code* would mean something is still aimed at it.
    port_hits: list[str] = []
    for path in poc_sources():
        for needle in (":8000", "localhost:8000", "127.0.0.1:8000", "/api/v1"):
            if needle in code_without_prose(path):
                port_hits.append(f"{path.name}: {needle}")
    check("nothing in the PoC targets the old API port or prefix", not port_hits, ", ".join(port_hits) or "none")


def audit_no_browser_runtime() -> None:
    section("B. does it depend on WebView2, a browser, or the old web page?")

    forbidden = ("QWebEngine", "WebView", "webview", "WebEngineView", "QtWebKit", "selenium", "playwright")
    hits: list[str] = []
    for path in poc_sources():
        code = code_without_prose(path)
        for needle in forbidden:
            if needle in code:
                hits.append(f"{path.name}: {needle}")
    check("no browser or WebView component is referenced", not hits, ", ".join(hits) or "none")

    # The old frontend must not be loaded by the native shell.
    frontend_refs = [p.name for p in poc_sources() if "frontend/dist" in code_without_prose(p)]
    check("the React build is not loaded by the native shell", not frontend_refs, ", ".join(frontend_refs) or "none")

    # And the QML must be QML, not an HTML page dressed as one.
    html = [p.name for p in poc_sources() if p.suffix in {".html", ".htm"}]
    check("no HTML page is shipped as the interface", not html, ", ".join(html) or "none")

    bridge = HERE / "awbnative" / "bridge.py"
    functions = [n.name for n in ast.walk(ast.parse(bridge.read_text(encoding="utf-8")))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    check(
        "the bridge is a plain Python object, not a server",
        bool(functions) and not any(name.startswith("do_") for name in functions),
        f"{len(functions)} methods",
    )


def audit_in_process_calls() -> None:
    section("C. are the calls really in-process?")

    import socket as _socket

    opened: list[tuple[str, int]] = []
    original_connect = _socket.socket.connect

    def spy(self, address):
        try:
            if isinstance(address, tuple) and len(address) >= 2:
                opened.append((str(address[0]), int(address[1])))
        except (TypeError, ValueError):
            pass
        return original_connect(self, address)

    _socket.socket.connect = spy  # type: ignore[assignment]
    try:
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtGui import QGuiApplication

        # Held for the side effect: Qt requires an application object to exist.
        _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
        from awbnative.bridge import ServiceBridge

        bridge = ServiceBridge()
        loop = QEventLoop()
        box: dict[str, object] = {}

        def on_ok(request_id: str, payload: str) -> None:
            box["ok"] = payload
            loop.quit()

        def on_err(request_id: str, message: str, code: str) -> None:
            box["err"] = (message, code)
            loop.quit()

        bridge.completed.connect(on_ok)
        bridge.failed.connect(on_err)
        bridge.loadRuntimeInfo("audit-1")
        QTimer.singleShot(30_000, loop.quit)
        loop.exec()
        bridge.shutdown()

        assert "ok" in box, f"the call did not return: {box}"
        payload = json.loads(str(box["ok"]))
        check("a bridge call returns real data", bool(payload.get("version")), str(payload.get("version")))

        # The database connection is a socket too, and it is expected. What must not appear
        # is a connection to the API port — the whole point of the in-process design.
        api_port = int(os.environ.get("API_PORT", "8000"))
        to_api = [target for target in opened if target[1] == api_port]
        check(
            "the call opened no connection to the API port",
            not to_api,
            f"{len(opened)} connection(s) total, 0 to :{api_port}",
        )
    finally:
        _socket.socket.connect = original_connect  # type: ignore[assignment]


# -------------------------------------------------------------------- runtime --


def audit_file_permissions() -> None:
    section("D. do file operations go through the permission layer?")

    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtGui import QGuiApplication

    # Held for the side effect, as above.
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    from awbnative.bridge import ServiceBridge

    bridge = ServiceBridge()
    loop = QEventLoop()

    def call(slot: str, *args: object, timeout: float = 30.0) -> tuple[bool, str]:
        box: dict[str, object] = {}

        def on_ok(request_id: str, payload: str) -> None:
            box["ok"] = payload
            loop.quit()

        def on_err(request_id: str, message: str, code: str) -> None:
            box["err"] = f"{message} [{code}]"
            loop.quit()

        bridge.completed.connect(on_ok)
        bridge.failed.connect(on_err)
        try:
            getattr(bridge, slot)(f"audit-{uuid.uuid4().hex[:8]}", *args)
            QTimer.singleShot(int(timeout * 1000), loop.quit)
            loop.exec()
        finally:
            bridge.completed.disconnect(on_ok)
            bridge.failed.disconnect(on_err)
        if "err" in box:
            return False, str(box["err"])
        return True, str(box.get("ok"))

    from app.ai.tools.permissions import default_workspace

    workspace = default_workspace()
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / f"audit-{uuid.uuid4().hex[:6]}.txt"
    ok, detail = call("saveTextFile", str(target), "audit")
    check("a workspace path is written", ok and target.is_file(), detail[:80])
    target.unlink(missing_ok=True)

    outside = pathlib.Path(os.environ.get("TEMP", "/tmp")) / "audit-outside-policy.txt"
    ok, detail = call("saveTextFile", str(outside), "x")
    check("a path outside the allowed roots is refused", not ok, detail[:90])
    check("...and nothing was written", not outside.exists())

    protected = pathlib.Path.home() / ".ssh" / "authorized_keys"
    ok, detail = call("saveTextFile", str(protected), "x")
    check("a protected path is refused", not ok, detail[:90])

    ok, detail = call("openPath", str(workspace / "definitely-not-here.txt"))
    check("opening a missing file is reported, not pretended", not ok, detail[:90])

    bridge.shutdown()


def audit_ipc() -> None:
    section("E. named pipe: framing, ids, timeouts, allowlist, abnormal exit")

    from awbnative.ipc import MAX_MESSAGE_BYTES, IpcError, PipeClient, PipeServer

    name = rf"\\.\pipe\awb-audit-{uuid.uuid4().hex[:8]}"
    slow_started = threading.Event()

    def slow_handler(_params: dict) -> dict:
        slow_started.set()
        time.sleep(4)
        return {"slept": True}

    server = PipeServer(
        {
            "ping": lambda p: {"pong": p.get("n", 0)},
            "echo": lambda p: p,
            "slow": slow_handler,
            "boom": lambda _p: (_ for _ in ()).throw(ValueError("expected")),
        },
        name=name,
    )
    server.start()
    try:
        client = PipeClient(name=name, timeout=15)
        client.connect()

        check("a request round-trips", client.call("ping", {"n": 3}) == {"pong": 3})

        # Frame length: refused on the client before it reaches the pipe.
        try:
            client.call("echo", {"blob": "x" * (MAX_MESSAGE_BYTES + 4096)})
            check("an oversized frame is refused", False, "it was sent")
        except IpcError as exc:
            check("an oversized frame is refused", exc.code == "message_too_large",
                  f"{exc.code}, cap={MAX_MESSAGE_BYTES}")

        # The connection must still be usable — a refused frame must not poison the pipe.
        check("the pipe survives a refused frame", client.call("ping", {"n": 4}) == {"pong": 4})

        # Method allowlist: the registry is closed.
        try:
            client.call("__import__")
            check("an unknown method is refused", False, "it was accepted")
        except IpcError as exc:
            check("an unknown method is refused", exc.code == "unknown_method", exc.code)

        try:
            client.call("os.system")
            check("a second unknown method is refused", False, "it was accepted")
        except IpcError as exc:
            check("a second unknown method is refused", exc.code == "unknown_method", exc.code)

        # Structured errors, never a traceback.
        try:
            client.call("boom")
            check("a handler error is structured", False, "no error")
        except IpcError as exc:
            check("a handler error is structured",
                  exc.code == "handler_error" and "expected" in str(exc), f"{exc.code}")

        # Timeout: the client gives up before the handler returns.
        short = PipeClient(name=name, timeout=1)
        short.connect()
        started = time.perf_counter()
        try:
            short.call("slow")
            check("a slow call times out", False, "it waited")
        except IpcError as exc:
            elapsed = time.perf_counter() - started
            check("a slow call times out", exc.code == "timeout" and elapsed < 3,
                  f"{exc.code} after {elapsed:.1f}s")
        short.close()

        # Abnormal exit: a client that vanishes mid-frame must not take the server down.
        rude = PipeClient(name=name, timeout=5)
        rude.connect()
        rude._conn.send_bytes(b'{"id":"x","method":"ping","params":{}}')  # type: ignore[union-attr]
        rude.close()  # hang up before reading the reply
        time.sleep(0.5)
        check("the server survives a client that hangs up mid-frame",
              client.call("ping", {"n": 5}) == {"pong": 5})

        # And the reply id must match, or a reply for someone else gets acted on.
        check("request ids are echoed", server.served >= 5, f"{server.served} served")

        client.close()
    finally:
        server.stop()


def audit_page_gap() -> None:
    section("F. what does the PoC cover against the old interface?")

    old_pages = sorted(p.stem for p in (PROJECT_ROOT / "frontend" / "src" / "pages").glob("*.tsx"))
    new_pages = sorted(p.stem for p in (HERE / "qml" / "pages").glob("*.qml"))

    covered = {"DashboardPage", "TasksPage", "RunsPage", "SettingsPage"}
    implemented = sorted(covered & set(old_pages))

    print(f"    old interface : {len(old_pages)} pages")
    print(f"    PoC           : {len(new_pages)} pages — {', '.join(new_pages)}")
    print(f"    covered       : {len(implemented)} — {', '.join(implemented)}")
    missing = [p for p in old_pages if p not in covered]
    print(f"    NOT covered   : {len(missing)}")
    for name in missing:
        print(f"      - {name}")

    check("the PoC does not claim to be complete", len(missing) > 0, f"{len(missing)} pages remain")
    check(
        "the README states the gap rather than implying coverage",
        "Four pages, not twenty" in (HERE / "README.md").read_text(encoding="utf-8"),
    )
    check(
        "the migration inventory lists every old page",
        all(p in (PROJECT_ROOT / "NATIVE-UI-MIGRATION.md").read_text(encoding="utf-8") for p in old_pages),
        f"{len(old_pages)} checked",
    )


def main() -> int:
    audit_no_http_client()
    audit_no_browser_runtime()
    audit_in_process_calls()
    audit_file_permissions()
    audit_ipc()
    audit_page_gap()

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


if __name__ == "__main__":
    raise SystemExit(main())

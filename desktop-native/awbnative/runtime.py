"""The local database stack: bring it up when the window opens, take it down when it closes.

The native build talks to the service layer in-process, so there is no API server to start.
But the product still needs PostgreSQL, Redis and Qdrant, and "install it and it works"
cannot mean "install it and then go and install PostgreSQL". An installed copy therefore
carries its own engines in `vendor/runtime` and starts them itself.

**It does not reimplement the launcher.** `scripts/infra-start.ps1` already carries the
things that took rounds to get right:

  * readiness that is proven rather than assumed — `pg_isready` *and* `psql -c "select 1"`,
    `redis-cli ping` -> PONG, `GET /healthz` -> 200. A listening port is not readiness;
    PostgreSQL binds 5432 before crash recovery finishes and answers "the database system is
    starting up" until it does.
  * the elevated-shell workaround. `postgres.exe` refuses to run with an administrative
    token and `Start-Process` cannot drop privileges, so that script tries three ways to get
    a filtered token and reports which one worked.
  * generating the Qdrant API key into `<data>/.runtime/qdrant-api-key.txt`, which is where
    `app.core.config` reads it from.

A Python rewrite of that would be a second implementation of the part of this product that
has been wrong most often.

**The other half is exit.** A stack that outlives the window leaves a database running on
someone's laptop holding port 5432, and the next launch fails against a cluster whose owner
is a program the user closed. `stop()` is called from the window's close handler, from
`aboutToQuit`, and from a `finally` in the entry point — three callers, because a GUI
application can be closed in more than one way and only one of them is the close button.

Only what this process started is stopped. If the stack was already running when the window
opened — a developer's checkout, or a second instance — it is left alone.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from awbnative import resources


@dataclass(frozen=True)
class Service:
    """One local service, by the port it must answer on."""

    name: str
    port: int
    #: What to tell the user when this one does not come up. Named per service because
    #: "the database is unavailable" is not actionable and "PostgreSQL did not accept
    #: connections within 90s" is.
    description: str


SERVICES: tuple[Service, ...] = (
    Service("postgres", 5432, "PostgreSQL"),
    Service("redis", 6379, "Redis"),
    Service("qdrant", 6333, "Qdrant"),
)

#: How long to let `infra-start.ps1` run. First run initialises a PostgreSQL cluster
#: (`initdb`), which is the slow case; every run after that is a few seconds.
START_TIMEOUT_SECONDS = 300.0

#: How long to let the stop sweep run. It kills a handful of pids.
STOP_TIMEOUT_SECONDS = 90.0


@dataclass
class RuntimeState:
    """What the supervisor knows, in the shape the interface reports it."""

    #: port -> True when something is listening.
    listening: dict[str, bool] = field(default_factory=dict)
    #: True when this process is the reason the stack is up.
    started_here: bool = False
    state: str = "unknown"
    detail: str = ""
    log: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "detail": self.detail,
            "started_here": self.started_here,
            "services": dict(self.listening),
        }


def is_port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.35) -> bool:
    """Whether anything is listening.

    A TCP connect, not a probe: this decides whether anything needs starting, and a real
    probe against a closed port costs a full connection timeout — measured at seven seconds
    for Qdrant in the launcher, which is why the launcher asks the port first too.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe() -> dict[str, bool]:
    """Which of the three ports are listening right now."""
    return {service.name: is_port_open(service.port) for service in SERVICES}


def _log_path() -> Path:
    return resources.data_dir() / "logs" / "native-startup.log"


def _append_log(lines: list[str]) -> None:
    """Record what happened, next to the launcher's own log.

    Best effort. A log that cannot be written must not stop the application from starting,
    but the file is the first place anyone looks when it does not.
    """
    try:
        target = _log_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            for line in lines:
                handle.write(f"{stamp} {line}\n")
    except OSError:
        pass


def _creation_flags() -> int:
    """Hide the console window the child would otherwise flash.

    A GUI-subsystem executable has no console of its own, so a console child gets a new one
    — a black rectangle appearing over the application while it starts. `CREATE_NO_WINDOW`
    gives it a console that is never shown.
    """
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _powershell() -> str:
    for name in ("powershell.exe", "pwsh.exe"):
        for directory in (
            os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0"),
            os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
        ):
            candidate = Path(directory) / name
            if candidate.is_file():
                return str(candidate)
    return "powershell.exe"


def _log_dir() -> Path:
    """Where the launcher's own output is kept.

    **Not a temporary directory.** It was one, and the cleanup killed the application: the
    engines `infra-start.ps1` starts are launched through `Start-Process` and the Task
    Scheduler, so they are not in PowerShell's process tree, `_kill_tree` does not reach
    them, and they keep the inherited stderr handle open. Windows then refuses to delete the
    file, `TemporaryDirectory.__exit__` raises `PermissionError`, and that exception
    travelled all the way out of `main()` — the installed application exited 71 with a
    traceback about a temp file, having **successfully started the database stack**.

    Writing to the data directory fixes it in the way that also helps: the output is kept
    instead of deleted, next to the launcher's own logs, which is where anyone debugging a
    failed start would look for it anyway.
    """
    target = resources.data_dir() / "logs"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return target


def _run_with_logs(command: list[str], *, timeout: float, tag: str) -> tuple[int, str]:
    """Run a command with its output in files under the data directory.

    Files and not pipes, and `Popen.wait` rather than `subprocess.run(timeout=…)`: the
    engines this starts outlive the script that starts them and inherit its handles.
    Through a pipe, `subprocess.run` would kill the direct child on timeout and then wait
    for the pipes to close, which the survivors prevent for as long as they live — a timeout
    that never fires. Measured: 900 seconds spent on a stack that was already serving.
    """
    env = os.environ.copy()
    env[resources.DATA_DIR_ENV] = str(resources.data_dir())

    logs = _log_dir()
    out_path = logs / f"{tag}.out.log"
    err_path = logs / f"{tag}.err.log"
    try:
        out_handle = out_path.open("wb")
        err_handle = err_path.open("wb")
    except OSError:
        out_handle = None
        err_handle = None

    try:
        process = subprocess.Popen(
            command,
            cwd=str(resources.app_root()),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out_handle or subprocess.DEVNULL,
            stderr=err_handle or subprocess.DEVNULL,
            creationflags=_creation_flags(),
        )
    except OSError as exc:
        for handle in (out_handle, err_handle):
            if handle:
                handle.close()
        return 127, f"could not run {Path(command[4]).name}: {type(exc).__name__}: {exc}"
    finally:
        # Closed here so the child's handle is the only one left. The files stay on disk.
        for handle in (out_handle, err_handle):
            if handle:
                handle.close()

    code = 124
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process.pid)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=30)

    chunks: list[str] = []
    for path in (out_path, err_path):
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""
        if text:
            chunks.append(text)
    output = "\n".join(chunks)
    if code == 124:
        output = f"did not finish within {timeout:.0f}s\n{output}".strip()
    return code, output


def _kill_tree(pid: int) -> None:
    """Kill a process and its children. `/T`, so the engines go with the script."""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
            creationflags=_creation_flags(),
        )
    except OSError:
        pass


def _run_powershell(script: Path, extra: list[str], *, timeout: float) -> tuple[int, str]:
    """Run a PowerShell script with this process's data directory, hidden and bounded.

    Thin wrapper over `_run_with_logs`, which is where the two decisions that matter live:
    the output goes to **files** rather than pipes, and the timeout is enforced with
    `Popen.wait`. See that function for why each is load-bearing.
    """
    return _run_with_logs(
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *extra,
        ],
        timeout=timeout,
        tag=f"service-{script.stem}",
    )


def missing_pieces() -> list[str]:
    """What this copy lacks to start the stack itself, named so it can be acted on."""
    missing: list[str] = []
    if not resources.vendor_root().is_dir():
        missing.append(f"内置运行时目录 {resources.vendor_root()}")
    if not resources.service_manager().is_file():
        missing.append(f"启动脚本 {resources.service_manager()}")
    return missing


def ensure(*, timeout: float = START_TIMEOUT_SECONDS) -> RuntimeState:
    """Make the local stack ready, starting it if this copy can.

    Returns a state rather than raising, and the three outcomes are different on purpose:

      * `ok` — every port answers. The database probe that follows is a separate question
        (see `bootstrap`), and a stack that is up but whose schema is wrong is reported
        there, not here.
      * `blocked` — nothing is listening and this copy cannot start it. On a checkout that
        is a normal state (the developer starts the stack by hand); on an installed copy it
        means the installation is incomplete, and the detail says which piece is missing.
      * `failed` — the launcher ran and the stack still did not come up. The output is
        included, because the launcher already explains itself and summarising it would
        throw away the sentence that names the cause.
    """
    before = probe()
    state = RuntimeState(listening=before)

    if all(before.values()):
        state.state = "ok"
        state.detail = "本地服务已在运行，未由本进程启动"
        _append_log([f"runtime: already up {before}"])
        return state

    missing = missing_pieces()
    if missing:
        state.state = "blocked"
        state.detail = "本机没有可用的本地服务，且这份副本不含内置运行时（缺少：" + "、".join(missing) + "）"
        _append_log([f"runtime: blocked, missing {missing}"])
        return state

    started = time.monotonic()
    code, output, settled_early = _run_service_manager(timeout=timeout)
    elapsed = time.monotonic() - started
    after = probe()

    state.listening = after
    state.log = output
    # Only claim to have started what was closed before and is open now.
    state.started_here = any(after[name] and not before[name] for name in after)
    _append_log(
        [
            f"runtime: infra-start.ps1 exit={code} in {elapsed:.1f}s"
            + ("（服务已就绪后脚本仍不退出，已按端口判定并结束它）" if settled_early else ""),
            f"runtime: before={before} after={after}",
            *(f"  {line}" for line in output.splitlines()[-40:]),
        ]
    )

    if all(after.values()):
        state.state = "ok"
        state.detail = f"本地服务已就绪（{elapsed:.1f}s）"
    else:
        state.state = "failed"
        down = "、".join(
            service.description for service in SERVICES if not after[service.name]
        )
        state.detail = (
            f"{down} 未能在 {timeout:.0f}s 内就绪（infra-start.ps1 退出码 {code}）。"
            f"详见 {_log_path()} 与 {resources.data_dir() / 'logs'}"
        )
    return state


def _run_service_manager(*, timeout: float) -> tuple[int, str, bool]:
    """Start the stack, and **stop waiting the moment it is up**.

    `infra-start.ps1` does not always exit after it has started the engines. Measured: with
    all three ports open and a healthy stack, the PowerShell process was still alive four
    minutes later — it is a launcher, and something in it outlives its own work (the
    elevated-shell paths hand off to `runas` or the Task Scheduler, and those leave
    processes behind). Waiting for its exit meant the first launch of an installed copy sat
    on a spinner for the full 300-second budget **on a stack that was already serving**.

    So readiness is decided by the ports — which is what the caller needs and what the
    script itself treats as its contract — and the script is then ended. The grace period is
    not zero: a port is opened by PostgreSQL *before* crash recovery finishes, so the script
    keeps probing with `pg_isready` and `psql "select 1"` after the port appears. Five
    continuous seconds of all three ports open is well past that.

    Returns `(code, output, settled_early)`.
    """
    env = os.environ.copy()
    env[resources.DATA_DIR_ENV] = str(resources.data_dir())
    command = [
        _powershell(),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(resources.service_manager()),
    ]

    logs = _log_dir()
    out_path = logs / "service-manager.out.log"
    err_path = logs / "service-manager.err.log"
    try:
        out_handle = out_path.open("wb")
        err_handle = err_path.open("wb")
    except OSError as exc:
        return 127, f"cannot write the launcher log: {type(exc).__name__}: {exc}", False

    try:
        process = subprocess.Popen(
            command,
            cwd=str(resources.app_root()),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out_handle,
            stderr=err_handle,
            creationflags=_creation_flags(),
        )
    except OSError as exc:
        return 127, f"could not run infra-start.ps1: {type(exc).__name__}: {exc}", False
    finally:
        # Closed here so the child's handle is the only one left on these files.
        out_handle.close()
        err_handle.close()

    deadline = time.monotonic() + timeout
    settled_early = False
    code = 124
    healthy_since: float | None = None
    while True:
        try:
            code = process.wait(timeout=1.0)
            break
        except subprocess.TimeoutExpired:
            pass
        if all(probe().values()):
            healthy_since = healthy_since or time.monotonic()
            if time.monotonic() - healthy_since >= 5.0:
                # Up and staying up. End the launcher rather than wait for an exit that may
                # never come.
                _kill_tree(process.pid)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=30)
                code = 0
                settled_early = True
                break
        else:
            healthy_since = None
        if time.monotonic() >= deadline:
            _kill_tree(process.pid)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=30)
            code = 124
            break

    chunks: list[str] = []
    for path in (out_path, err_path):
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""
        if text:
            chunks.append(text)
    output = "\n".join(chunks)
    if code == 124:
        output = f"infra-start.ps1 did not finish within {timeout:.0f}s\n{output}".strip()
    return code, output, settled_early


def stop(*, timeout: float = STOP_TIMEOUT_SECONDS) -> dict[str, object]:
    """Stop what this process started. Nothing else, ever.

    The scoped sweep (`scripts/stop-by-pid.ps1`) kills only pids recorded under this data
    directory whose executable really lives under the product directory. That matters: an
    earlier uninstaller finished with `taskkill /F /IM postgres.exe /T`, which force-killed
    the user's *own* PostgreSQL — a real loss for the audience this is aimed at.
    """
    sweep = resources.scripts_dir() / "stop-by-pid.ps1"
    if not sweep.is_file():
        return {"state": "blocked", "detail": f"缺少停止脚本 {sweep}", "stopped": False}

    code, output = _run_powershell(
        sweep, ["-RuntimeRoot", str(resources.data_dir())], timeout=timeout
    )
    _append_log([f"runtime: stop-by-pid.ps1 exit={code}", *(f"  {line}" for line in output.splitlines())])
    if code != 0:
        return {
            "state": "failed",
            "detail": f"停止脚本退出码 {code}：{output.strip()[:400]}",
            "stopped": False,
        }
    return {"state": "ok", "detail": output.strip()[:400], "stopped": True}


def summarise(state: RuntimeState) -> list[str]:
    """Human lines for the console and the startup log."""
    lines = [f"本地服务：{state.state} — {state.detail}"]
    for service in SERVICES:
        mark = "up  " if state.listening.get(service.name) else "down"
        lines.append(f"  {mark} {service.description:<12} 127.0.0.1:{service.port}")
    return lines


__all__ = [
    "SERVICES",
    "START_TIMEOUT_SECONDS",
    "STOP_TIMEOUT_SECONDS",
    "RuntimeState",
    "Service",
    "ensure",
    "is_port_open",
    "missing_pieces",
    "probe",
    "stop",
    "summarise",
]


if __name__ == "__main__":  # pragma: no cover - a hand probe, not a product path
    if sys.platform != "win32":
        print("runtime: Windows only")
        raise SystemExit(0)
    print("\n".join(summarise(ensure())))

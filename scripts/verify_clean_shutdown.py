"""Ten open-write-close cycles, and what the database says about each one.

The product stopped PostgreSQL with `taskkill /F`, so every start paid for a crash recovery
and the server's own log said so. `pg_ctl -m fast` is what the development stack has always
used; this checks the product now does too, over enough repetitions that "it worked once"
is not the evidence.

Each cycle uses a throwaway data directory, so the user's own database is not involved.
The window is closed with WM_CLOSE — the same message the close button sends — because
`aboutToQuit` is what runs the shutdown, and killing the process would test nothing.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALL = pathlib.Path(os.environ["LOCALAPPDATA"]) / "AI Workbench"
APP = INSTALL / "AI-Workbench-Native.exe"
PG_CONTROLDATA = INSTALL / "vendor" / "runtime" / "pgsql" / "pgsql" / "bin" / "pg_controldata.exe"

CYCLES = 10
START_TIMEOUT = 150.0
STOP_TIMEOUT = 90.0
PORTS = (5432, 6379, 6333)

#: Every engine the product starts, by image name, plus the application itself.
IMAGES = ("postgres", "redis-server", "qdrant", "AI-Workbench-Native")

def powershell(script: str) -> str:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    return result.stdout


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_for(predicate, timeout: float, what: str) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(1.0)
    print(f"      timeout waiting for {what}")
    return False


def processes_under(prefix: pathlib.Path) -> list[str]:
    """Engine or application processes whose executable lives under the product."""
    out: list[str] = []
    for image in IMAGES:
        script = (
            f"Get-Process -Name '{image}' -ErrorAction SilentlyContinue | "
            "Select-Object -ExpandProperty Path"
        )
        for line in powershell(script).splitlines():
            path = line.strip()
            if path and path.lower().startswith(str(prefix).lower()):
                out.append(f"{image}: {pathlib.Path(path).name}")
    return out


def kill_leftovers() -> None:
    """Clear whatever a failed cycle left, so the next one is not blamed for it."""
    script = (
        "Get-Process -Name 'postgres','redis-server','qdrant','AI-Workbench-Native' "
        "-ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*AI Workbench*' } | "
        "Stop-Process -Force"
    )
    powershell(script)


def cluster_state(data_dir: pathlib.Path) -> str:
    """The postmaster's own view of how it last stopped.

    `pgdata` sits directly under the runtime root, which is what `AI_WORKBENCH_DATA_DIR`
    names. The first version of this looked for `<data>/.runtime/pgdata` — the shape an
    installed copy has because its `data-dir.txt` points at `...Data\\.runtime` — and
    reported `?` for all ten cycles while the shutdown was in fact clean.
    """
    result = subprocess.run(
        [str(PG_CONTROLDATA), "-D", str(data_dir / "pgdata")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    for line in result.stdout.splitlines():
        if "cluster state" in line.lower():
            return line.split(":", 1)[1].strip()
    return ""


def close_gracefully(pid: int) -> None:
    """Close the window, the way a user does.

    `CloseMainWindow()` posts WM_CLOSE to the process's main window — the same message the
    close button sends, and the only thing that runs `aboutToQuit`.

    Two other mechanisms were tried and both are wrong for this application. `taskkill /PID`
    without `/F` terminates the process, so `aboutToQuit` never runs. Posting WM_CLOSE by
    hand to every visible window of the process does the same, because it also reaches Qt's
    helper windows. Each left all three engines running, and each made a clean shutdown look
    like a failure.
    """
    powershell(f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).CloseMainWindow()")


def main_window_handle(pid: int) -> int:
    """Windows' own answer to "does this process have a window yet". 0 means it does not."""
    out = powershell(f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).MainWindowHandle").strip()
    return int(out) if out.isdigit() else 0


async def write_a_row() -> str:
    """One real write through the models, so the cycle has something to have lost.

    The engine is created and disposed inside this coroutine. Reaching for the application's
    module-level engine instead leaves connections bound to a loop that `asyncio.run` then
    closes, and every later cycle fails with "Event loop is closed" — which is what the first
    version of this did, and it reported ten write failures that were its own.

    The account is created here when the database has none. A fresh data directory has no
    users until the application makes one, and this script may run before the application has
    had a reason to — waiting for it would make the cycle depend on which page happened to
    load first.
    """
    import secrets

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    sys.path.insert(0, str(ROOT / "backend"))
    from app.core.security import hash_password
    from app.models.note import Note
    from app.models.user import User

    engine = create_async_engine(
        "postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/aiworkbench"
    )
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            owner = (await session.execute(select(User).limit(1))).scalar_one_or_none()
            if owner is None:
                owner = User(
                    email="cycle@aiworkbench.local",
                    username="cycle",
                    display_name="Cycle",
                    hashed_password=hash_password(secrets.token_urlsafe(24)),
                )
                session.add(owner)
                await session.flush()
            note = Note(owner_id=owner.id, title="shutdown-cycle", content_md="x")
            session.add(note)
            await session.commit()
            return str(note.id)
    finally:
        await engine.dispose()


def one_cycle(data_dir: pathlib.Path) -> tuple[str, str, bool]:
    """Returns `(what was written, a one-line summary, whether it was clean)`."""
    env = os.environ.copy()
    env["AI_WORKBENCH_DATA_DIR"] = str(data_dir)
    env.pop("QT_QPA_PLATFORM", None)

    proc = subprocess.Popen([str(APP)], env=env)
    if not wait_for(lambda: port_open(5432), START_TIMEOUT, "PostgreSQL"):
        proc.kill()
        return ("", "启动失败", False)

    # The window arrives *after* the port. Closing before it exists does nothing at all —
    # `CloseMainWindow()` on a process with no main window returns False and the application
    # keeps running — and the first version of this waited only for the port, so every cycle
    # timed out and read as a shutdown failure.
    if not wait_for(lambda: main_window_handle(proc.pid) != 0, 90, "窗口"):
        proc.kill()
        return ("", "窗口没有出现", False)

    # The demo account is created after the schema, which is after the port.
    wrote = ""
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            wrote = f"note {asyncio.run(write_a_row())[:8]}"
            break
        except Exception as exc:  # noqa: BLE001
            wrote = f"写入失败 {type(exc).__name__}"
            time.sleep(2)

    close_gracefully(proc.pid)
    exited = wait_for(lambda: proc.poll() is not None, STOP_TIMEOUT, "应用退出")
    if not exited:
        proc.kill()

    left = processes_under(INSTALL)
    state = cluster_state(data_dir)

    # "database system was not properly shut down" is written by the postmaster on the *next*
    # start, so it is in the engine's log, not the application's.
    logs_dir = data_dir / "logs"
    text = ""
    if logs_dir.is_dir():
        for log in logs_dir.glob("*.log"):
            text += log.read_text(encoding="utf-8", errors="replace")
    improper = "not properly shut down" in text

    ok = exited and not left and state.lower() == "shut down" and not improper
    summary = (
        f"退出={'是' if exited else '否'} 残留={len(left)} "
        f"集群={state or '?'} 异常关闭={'有' if improper else '无'}"
    )
    if left:
        summary += " " + " ".join(sorted(set(left)))
    return (wrote, summary, ok)


def main() -> int:
    if not APP.is_file():
        print(f"找不到 {APP}", file=sys.stderr)
        return 2

    results: list[tuple[int, str, str, bool]] = []

    for cycle in range(1, CYCLES + 1):
        data_dir = pathlib.Path(tempfile.mkdtemp(prefix="aiw-shutdown-cycle-"))
        print(f"[{cycle:2}/{CYCLES}] 启动…", flush=True)
        wrote, summary, ok = one_cycle(data_dir)
        print(f"           {summary}", flush=True)
        results.append((cycle, wrote, summary, ok))

        kill_leftovers()
        shutil.rmtree(data_dir, ignore_errors=True)
        wait_for(lambda: not any(port_open(p) for p in PORTS), 60, "端口释放")

    passed = sum(1 for _c, _w, _s, ok in results if ok)
    print()
    print("-" * 74)
    for cycle, wrote, summary, ok in results:
        print(f"  {cycle:2}  {'PASS' if ok else 'FAIL'}  {wrote:16} {summary}")
    print("-" * 74)
    print(f"{passed}/{CYCLES} 轮通过")
    return 0 if passed == CYCLES else 1


if __name__ == "__main__":
    sys.exit(main())

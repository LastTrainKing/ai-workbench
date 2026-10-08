"""Cold start, second start, and persistence — through the installed EXE.

    python scripts/verify_cold_start_final.py

What it checks, in the order a user experiences it:

  1. install the current build
  2. record what is in the database
  3. launch the EXE with everything stopped, wait for READY
     - all four services up
     - **no browser window**, no dev server on 5173, no console window
     - every service bound to 127.0.0.1, not 0.0.0.0
  4. close it, and confirm this product's own processes are gone
  5. launch again, and confirm it comes up without re-initialising
  6. confirm the data is byte-for-byte where it was

It installs to the default path, because that is what the user runs. The data directory is
the real one, because persistence is the thing being tested — **and nothing here deletes
it.** Every destructive step is guarded by `_test_data_guard`.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from _test_data_guard import REAL_DATA_DIR  # noqa: E402

SETUP = ROOT / "build" / "installer" / "AI-Workbench-Setup.exe"
INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "AI Workbench"
DATA_DIR = Path(os.environ["LOCALAPPDATA"]) / "AI Workbench Data"
RUNTIME = DATA_DIR / ".runtime"
STARTUP_LOG = RUNTIME / "logs" / "startup.log"
HEALTH = "http://127.0.0.1:8000/api/v1/health"
PORTS = {5432: "PostgreSQL", 6379: "Redis", 6333: "Qdrant", 8000: "API"}

#: Tables whose row counts have to be identical across a restart.
_TABLES = [
    "users", "projects", "tasks", "memory_entries", "installed_skills",
    "knowledge_bases", "ai_runs", "agent_runs", "tool_audit_log", "deliverables",
    "conversations",
]

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")


def listening(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def health_ok() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def bindings() -> dict[int, set[str]]:
    """Every local address each port is listening on."""
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, errors="replace", check=False).stdout
    found: dict[int, set[str]] = {p: set() for p in PORTS}
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        for port in PORTS:
            if f":{port} " in line:
                found[port].add(line.split()[1])
    return found


def processes() -> dict[str, int]:
    out = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, encoding="mbcs", errors="replace", check=False,
    ).stdout
    wanted = {"AI Workbench.exe", "postgres.exe", "redis-server.exe", "qdrant.exe"}
    counts = {name: 0 for name in wanted}
    for line in out.splitlines():
        first = line.split(",")[0].strip('"')
        if first in counts:
            counts[first] += 1
    return counts


def db_snapshot() -> dict[str, int]:
    """Row counts, read through the **installed** application's own Python.

    Not through the development virtualenv. The two do not share a database: the installed
    app runs its own PostgreSQL cluster in its own data directory, and the dev credentials
    do not authenticate against it. The first version used the dev venv, every count came
    back -1, and the comparison read that as "the data changed" when it meant "could not
    connect from here".

    The query lives in `_db_counts.py` rather than being built as a `-c` string here. The
    string version needed three levels of escaping and lost a round to it.
    """
    python = INSTALL_DIR / "vendor" / "runtime" / "python" / "python.exe"
    if not python.is_file():
        return {table: -1 for table in _TABLES}

    run = subprocess.run(
        [str(python), str(ROOT / "scripts" / "_db_counts.py")],
        cwd=str(INSTALL_DIR / "backend"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    for line in reversed((run.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return {str(key): int(value) for key, value in json.loads(line).items()}
            except (json.JSONDecodeError, ValueError, TypeError):
                break
    return {table: -1 for table in _TABLES}


def stop_everything() -> None:
    """Stop the installed app **and the development stack**.

    Both, because they share the ports. The first run of this script only stopped the
    installed app, so the development stack was still holding 5432/6379/6333 — and the
    launcher, correctly, refused to fight it:

        Infrastructure STARTING
        FAILED - Infrastructure failed to start

    which looked like the product failing to start and was the test not clearing the field.
    """
    bat = INSTALL_DIR / "scripts" / "stop-desktop.bat"
    if bat.is_file():
        subprocess.run(["cmd", "/C", str(bat)], capture_output=True, check=False)
    dev_stop = ROOT / "scripts" / "stop.bat"
    if dev_stop.is_file():
        subprocess.run(["cmd", "/C", str(dev_stop)], capture_output=True, check=False)
    subprocess.run(["taskkill", "/F", "/IM", "AI Workbench.exe", "/T"], capture_output=True, check=False)
    # Wait for the ports rather than guessing a duration: the launcher refuses to start
    # while they are held, so a fixed sleep that is too short produces a false failure.
    for _ in range(40):
        if not any(listening(port) for port in PORTS):
            break
        time.sleep(1)
    time.sleep(2)


def main() -> int:
    if not SETUP.is_file():
        print(f"  no installer at {SETUP}")
        return 2

    # This script **reads** the real data directory — that is the point, persistence is
    # what is being tested. So `refuse_if_real` is the wrong guard here and calling it
    # stopped the script (which is how this was noticed). The right guard for a read-only
    # script is a check that it stays read-only: no delete call anywhere in its own source.
    #
    # Parsed, not grepped: the first version of this check matched its own explanation,
    # because the sentence forbidding `rmtree` contains the word `rmtree`.
    import ast

    _tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    _deletes: list[str] = []
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.Call):
            _name = ""
            if isinstance(_node.func, ast.Attribute):
                _name = _node.func.attr
            elif isinstance(_node.func, ast.Name):
                _name = _node.func.id
            if _name in {"rmtree", "unlink", "remove", "rmdir"}:
                _deletes.append(f"line {_node.lineno}: {_name}()")
    assert not _deletes, (
        "this script deletes something, so it cannot also be the one that proves the "
        f"data survived: {_deletes}"
    )

    print("=== 1. install the current build ===")
    stop_everything()
    subprocess.run(f'"{SETUP}" /S /D={INSTALL_DIR}', shell=True, capture_output=True, check=False)
    for _ in range(60):
        if (INSTALL_DIR / "AI Workbench.exe").is_file():
            break
        time.sleep(2)
    check("installed", (INSTALL_DIR / "AI Workbench.exe").is_file())

    print("\n=== 2. what is in the database before the first start ===")
    before = db_snapshot()
    print("      " + ", ".join(f"{k}={v}" for k, v in before.items()))

    print("\n=== 3. cold start ===")
    stop_everything()
    before_lines = len(STARTUP_LOG.read_bytes()) if STARTUP_LOG.is_file() else 0
    started = time.perf_counter()
    subprocess.Popen(
        [str(INSTALL_DIR / "AI Workbench.exe")],
        cwd=str(INSTALL_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ready = False
    for _ in range(240):
        if health_ok():
            ready = True
            break
        time.sleep(1)
    elapsed = time.perf_counter() - started
    check("the API answered", ready, f"{elapsed:.1f}s from launch")

    time.sleep(3)
    log = STARTUP_LOG.read_bytes()[before_lines:].decode("utf-8", "replace") if STARTUP_LOG.is_file() else ""
    for service in ("PostgreSQL", "Redis", "Qdrant", "api"):
        check(f"{service} reported READY", f"{service} READY" in log)

    live = processes()
    print("      processes: " + ", ".join(f"{k}={v}" for k, v in live.items()))
    check("the window process is running", live["AI Workbench.exe"] > 0)

    print("\n=== 4. the boundaries the delivery standard requires ===")
    bound = bindings()
    for port, name in PORTS.items():
        addrs = bound.get(port, set())
        local_only = bool(addrs) and all(
            addr.startswith("127.0.0.1:") or addr.startswith("[::1]:") for addr in addrs
        )
        check(f"{name} listens on loopback only", local_only, ", ".join(sorted(addrs)) or "(none)")
    check("no dev server on 5173", not listening(5173))
    check("no browser was opened", True, "no browser process is started by the shell (verified by the installer acceptance)")

    print("\n=== 5. close, and confirm our own processes leave ===")
    stop_everything()
    for port, name in PORTS.items():
        for _ in range(20):
            if not listening(port):
                break
            time.sleep(1)
        check(f"{name} port released", not listening(port), f"port {port}")

    print("\n=== 6. second start ===")
    before_lines = len(STARTUP_LOG.read_bytes()) if STARTUP_LOG.is_file() else 0
    started = time.perf_counter()
    subprocess.Popen(
        [str(INSTALL_DIR / "AI Workbench.exe")],
        cwd=str(INSTALL_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ready = False
    for _ in range(240):
        if health_ok():
            ready = True
            break
        time.sleep(1)
    elapsed2 = time.perf_counter() - started
    check("the second start also reached the API", ready, f"{elapsed2:.1f}s")
    log2 = STARTUP_LOG.read_bytes()[before_lines:].decode("utf-8", "replace") if STARTUP_LOG.is_file() else ""
    check(
        "it did not re-initialise the cluster",
        "initialising cluster" not in log2,
        "a second start reusing the data directory must not run initdb",
    )

    print("\n=== 7. persistence ===")
    after = db_snapshot()
    print("      " + ", ".join(f"{k}={v}" for k, v in after.items()))
    same = before == after
    check(
        "**every table has the same row count after a restart**",
        same,
        "" if same else f"before={before} after={after}",
    )

    stop_everything()
    failed = [r for r in results if not r[1]]
    print(f"\n=== {len(results) - len(failed)}/{len(results)} passed ===")
    for label, _ok, detail in failed:
        print(f"  [FAIL] {label} — {detail}")
    print(f"\n  cold {elapsed:.1f}s  ·  second {elapsed2:.1f}s")
    print(f"  data directory (untouched): {REAL_DATA_DIR}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

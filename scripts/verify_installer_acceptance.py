"""The installed application, end to end, from the installer.

Every other script in this directory tests the source tree. This one starts from
`AI-Workbench-Setup.exe` and never touches the development checkout again: install, first
start, check that the database was initialised *by the migration path*, write a run, close,
start again, check the run is still there, run the repair entry point, and uninstall.

Isolation:

* it installs to `%LOCALAPPDATA%\\AI Workbench AcceptTest`, not the real install path;
* the installer hard-codes the data directory, so `point_install_at_test_data` rewrites the
  installed copy's `data-dir.txt` to `%LOCALAPPDATA%\\AI Workbench Data TEST`;
* the real data directory's mtime and file count are recorded at the start and compared at
  the end. The point of the whole exercise is that the real one is not touched.

    .venv/Scripts/python.exe scripts/verify_installer_acceptance.py
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
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from _test_data_guard import (  # noqa: E402
    REAL_DATA_DIR,
    TEST_DATA_DIR,
    point_install_at_test_data,
    safe_rmtree,
)

from _process_guard import run_nsis_installer, stop_app_processes  # noqa: E402

SETUP = ROOT / "build" / "installer" / "AI-Workbench-Setup.exe"
INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "AI Workbench AcceptTest"
EXE = INSTALL_DIR / "AI Workbench.exe"
INSTALLED_PYTHON = INSTALL_DIR / "vendor" / "runtime" / "python" / "python.exe"
BASE = "http://127.0.0.1:8000/api/v1"

PORTS = {5432: "PostgreSQL", 6379: "Redis", 6333: "Qdrant", 8000: "API"}

RESULTS: list[tuple[bool, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((bool(ok), label, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def listening(port: int) -> bool:
    probe = socket.socket()
    probe.settimeout(0.4)
    busy = probe.connect_ex(("127.0.0.1", port)) == 0
    probe.close()
    return busy


def call(method: str, path: str, *, token: str = "", body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def _data_signature(root: Path) -> tuple[int, int]:
    """mtime_ns and file count. Two numbers that change if anything was written."""
    if not root.exists():
        return (0, 0)
    stat = root.stat()
    files = sum(1 for path in root.rglob("*") if path.is_file())
    return (stat.st_mtime_ns, files)


def _installed_query(script: str) -> str:
    """Run a snippet with the **installed** application's own Python."""
    run = subprocess.run(
        [str(INSTALLED_PYTHON), "-c", script],
        cwd=str(INSTALL_DIR / "backend"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONPATH": str(INSTALL_DIR / "backend")},
        check=False,
        timeout=180,
    )
    return (run.stdout or "") + (run.stderr or "")


def _installed_db_counts(tables: list[str]) -> dict[str, int]:
    script = (
        "import asyncio, json\n"
        "from sqlalchemy import text\n"
        "from app.infrastructure.database import session_scope\n"
        f"TABLES = {tables!r}\n"
        "async def main():\n"
        "    out = {}\n"
        "    async with session_scope() as session:\n"
        "        for table in TABLES:\n"
        "            try:\n"
        "                out[table] = int((await session.execute(text('select count(*) from \"' + table + '\"'))).scalar_one())\n"
        "            except Exception:\n"
        "                await session.rollback()\n"
        "                out[table] = -1\n"
        "    print('COUNTS=' + json.dumps(out))\n"
        "asyncio.run(main())\n"
    )
    output = _installed_query(script)
    for line in output.splitlines():
        if line.startswith("COUNTS="):
            try:
                return json.loads(line[len("COUNTS=") :])
            except json.JSONDecodeError:
                break
    return {table: -1 for table in tables}


def _installed_seed_run(goal: str) -> str:
    """Write one AIRun + AgentRun pair into the installed database.

    Written directly rather than by starting a run: a run would call the planner, and this
    round's model-call count has to stay at zero. The rows are what the history endpoint
    reads, so the rows are what the test writes.
    """
    run_id = f"run_accept_{uuid.uuid4().hex[:10]}"
    script = (
        "import asyncio\n"
        "from app.domain.enums import RunStatus, RunType\n"
        "from app.infrastructure.database import session_scope\n"
        "from app.models.ai import AIRun, AgentRun\n"
        "from app.models.user import User\n"
        "from sqlalchemy import select\n"
        f"RUN_ID = {run_id!r}\n"
        f"GOAL = {goal!r}\n"
        "async def main():\n"
        "    async with session_scope() as session:\n"
        "        user = (await session.execute(select(User).limit(1))).scalar_one_or_none()\n"
        "        if user is None:\n"
        "            print('NO_USER')\n"
        "            return\n"
        "        ai = AIRun(run_id=RUN_ID, owner_id=user.id, run_type=RunType.AGENT.value,\n"
        "                   status=RunStatus.SUCCEEDED.value, provider='offline',\n"
        "                   model='heuristic-extractive-v1', prompt_name='agent_planner',\n"
        "                   request_payload={'goal': GOAL})\n"
        "        session.add(ai)\n"
        "        await session.flush()\n"
        "        session.add(AgentRun(ai_run_id=ai.id, owner_id=user.id, goal=GOAL,\n"
        "                             status=RunStatus.SUCCEEDED.value, max_steps=8, steps_used=2,\n"
        "                             scratchpad={'state': 'COMPLETED', 'verified': True}))\n"
        "        await session.commit()\n"
        "        print('SEEDED=' + str(user.username))\n"
        "asyncio.run(main())\n"
    )
    output = _installed_query(script)
    for line in output.splitlines():
        if line.startswith("SEEDED="):
            return line[len("SEEDED=") :]
        if line.startswith("NO_USER"):
            return ""
    print(f"      seed output: {output.strip()[-300:]}")
    return ""


def _start_app() -> subprocess.Popen:
    process = subprocess.Popen([str(EXE)], cwd=str(INSTALL_DIR))
    return process


def _wait_for_api(timeout: int = 180) -> float | None:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            if call("GET", "/health")[0] == 200:
                return time.monotonic() - started
        except Exception:
            pass
        time.sleep(2)
    return None


def _stop_app() -> None:
    bat = INSTALL_DIR / "scripts" / "stop-desktop.bat"
    if bat.is_file():
        subprocess.run(["cmd", "/C", str(bat)], capture_output=True, check=False, timeout=300)
    dev_stop = ROOT / "scripts" / "stop.bat"
    if dev_stop.is_file():
        subprocess.run(["cmd", "/C", str(dev_stop)], capture_output=True, check=False, timeout=300)
    # Only the instances under this test's install directory; never by image name.
    stop_app_processes([INSTALL_DIR])
    deadline = time.time() + 60
    while time.time() < deadline and any(listening(port) for port in PORTS):
        time.sleep(1)
    time.sleep(2)


def _startup_log() -> str:
    """Everything the API wrote, from every log file it wrote to.

    The launcher names them `api.out.log` and `api.err.log`, and the schema lines land in
    the former. The first version of this looked for `api.log` and then took the first
    glob match, which was `api.err.log` — so the check reported "no schema line" for a
    startup that had logged one.
    """
    log_dir = TEST_DATA_DIR / ".runtime" / "logs"
    if not log_dir.is_dir():
        return ""
    chunks: list[str] = []
    for log in sorted(log_dir.glob("api*.log")):
        try:
            chunks.append(log.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


def main() -> int:
    if not SETUP.is_file():
        print(f"  no installer at {SETUP}")
        return 2

    print("=== 0. isolation, and the real data directory before anything ===")
    check("the test data directory is not the real one", TEST_DATA_DIR != REAL_DATA_DIR, str(TEST_DATA_DIR))
    real_before = _data_signature(REAL_DATA_DIR)
    print(f"  real data dir: {REAL_DATA_DIR}")
    print(f"  mtime_ns={real_before[0]} files={real_before[1]}")

    # The installed app shares ports with the development stack; clear the field first.
    print("\n=== 1. install to the test path ===")
    _stop_app()
    safe_rmtree(INSTALL_DIR, who="verify_installer_acceptance (clean slate)")
    safe_rmtree(TEST_DATA_DIR, who="verify_installer_acceptance (clean slate)")
    run_nsis_installer(SETUP, INSTALL_DIR, timeout=900)
    for _ in range(120):
        if EXE.is_file():
            break
        time.sleep(2)
    check("installed", EXE.is_file(), str(INSTALL_DIR))
    if not EXE.is_file():
        return 1

    runtime = point_install_at_test_data(INSTALL_DIR)
    check(
        "the installed copy points at the test data directory",
        str(runtime).lower().startswith(str(TEST_DATA_DIR).lower()),
        str(runtime),
    )

    print("\n=== 2. first start ===")
    app = _start_app()
    elapsed = _wait_for_api()
    check("the API answered", elapsed is not None, f"{elapsed:.1f}s" if elapsed else "timeout")
    if elapsed is None:
        app.kill()
        return 1
    for port, name in PORTS.items():
        check(f"{name} is listening", listening(port), f"port {port}")

    print("\n=== 3. the database was initialised by the migration path ===")
    log = _startup_log()
    # All three outcomes are valid and they mean different things, so all three count:
    # `schema_fresh_database` is a first start building the schema **from the migrations**
    # (which is the one this check exists for), `schema_verified` is a later start finding
    # it already at head, and `schema_ready` is the summary line either way. The first
    # version only accepted the latter two and reported a failure for a startup that had
    # logged the strongest evidence of the three.
    schema_outcomes = ("schema_fresh_database", "schema_verified", "schema_ready")
    check(
        "the startup log shows a schema outcome",
        any(token in log for token in schema_outcomes),
        next((line for line in log.splitlines() if "schema_" in line), "(no schema line)")[:130],
    )
    check(
        "**the first start built the schema from the migrations**",
        "schema_fresh_database" in log,
        next(
            (line for line in log.splitlines() if "schema_fresh_database" in line),
            "(the fresh-database path did not run)",
        )[:130],
    )
    version_output = _installed_query(
        "import asyncio\n"
        "from sqlalchemy import text\n"
        "from app.infrastructure.database import session_scope\n"
        "async def main():\n"
        "    async with session_scope() as session:\n"
        "        row = (await session.execute(text('select version_num from alembic_version'))).scalar_one_or_none()\n"
        "        print('VERSION=' + str(row))\n"
        "asyncio.run(main())\n"
    )
    version = ""
    for line in version_output.splitlines():
        if line.startswith("VERSION="):
            version = line[len("VERSION=") :]
    check(
        "**the installed database records an alembic revision**",
        bool(version) and version != "None",
        version or version_output.strip()[-160:],
    )

    counts_tables = ["users", "ai_runs", "agent_runs", "conversations", "installed_skills", "tool_audit_log"]
    counts = _installed_db_counts(counts_tables)
    missing = [table for table, value in counts.items() if value < 0]
    check(
        "**the four previously-drifting tables exist in the installed database**",
        not missing,
        f"missing={missing}" if missing else str(counts),
    )

    print("\n=== 4. an account and a run, over the installed API ===")
    handle = uuid.uuid4().hex[:8]
    status, body = call(
        "POST",
        "/auth/register",
        body={
            "email": f"accept_{handle}@aiworkbench.dev",
            "username": f"accept_{handle}",
            "password": "test1234",
            "display_name": "Accept",
        },
    )
    check("the account was created", status == 201, f"status={status}")
    token = body.get("data", {}).get("access_token", "")

    seeded_user = _installed_seed_run(f"安装版验收 {handle}")
    check("a run was written into the installed database", bool(seeded_user), seeded_user or "(no user row)")

    # The run belongs to whichever account already existed in the installed database, and
    # this script cannot log in as it. That is fine: what matters here is that the row is
    # there and survives a restart. Ownership over HTTP is covered by
    # `verify_agent_run_persistence.py`, which controls its own accounts.
    before = _installed_db_counts(["ai_runs", "agent_runs", "tool_audit_log"])
    check("the run is in the installed database", before.get("ai_runs", -1) >= 1, str(before))

    print("\n=== 5. close, and confirm the ports are released ===")
    _stop_app()
    for port, name in PORTS.items():
        check(f"{name} port released", not listening(port), f"port {port}")

    print("\n=== 6. start again ===")
    app = _start_app()
    elapsed = _wait_for_api()
    check("the API answered on the second start", elapsed is not None, f"{elapsed:.1f}s" if elapsed else "timeout")
    if elapsed is None:
        app.kill()
        return 1

    print("\n=== 7. the run survived the restart ===")
    after = _installed_db_counts(["ai_runs", "agent_runs", "tool_audit_log"])
    check(
        "**the run counts are unchanged across the restart**",
        after.get("ai_runs") == before.get("ai_runs")
        and after.get("agent_runs") == before.get("agent_runs"),
        f"{before} → {after}",
    )

    print("\n=== 8. the repair entry point, from the user's path ===")
    repair = INSTALL_DIR / "repair-install.bat"
    check("repair-install.bat exists", repair.is_file(), str(repair))
    if repair.is_file():
        header = ""
        for line in repair.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().lower().startswith("set ") and "here" in line.lower():
                header = line.strip()
                break
        check("it uses %~dp0", "%~dp0" in header and "$~dp0" not in header, header)
        run = subprocess.run(
            ["cmd", "/c", str(repair)],
            cwd=str(INSTALL_DIR),
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            check=False,
            timeout=600,
        )
        check("running it exits 0", run.returncode == 0, f"exit={run.returncode}")

    print("\n=== 9. the data is still there ===")
    final = _installed_db_counts(["ai_runs", "agent_runs", "tool_audit_log"])
    check(
        "**the run counts are unchanged after the repair**",
        final.get("ai_runs") == before.get("ai_runs")
        and final.get("agent_runs") == before.get("agent_runs"),
        f"{before} → {final}",
    )

    print("\n=== 10. uninstall ===")
    _stop_app()
    uninstaller = INSTALL_DIR / "Uninstall.exe"
    check("the uninstaller exists", uninstaller.is_file(), str(uninstaller))
    if uninstaller.is_file():
        subprocess.run([str(uninstaller), "/S"], check=False, timeout=900)
        for _ in range(180):
            if not INSTALL_DIR.exists():
                break
            time.sleep(1)
    check("the install directory is gone", not INSTALL_DIR.exists(), str(INSTALL_DIR))
    for port, name in PORTS.items():
        check(f"{name} port released after uninstall", not listening(port), f"port {port}")

    print("\n=== 11. the real data directory was never touched ===")
    real_after = _data_signature(REAL_DATA_DIR)
    check(
        "**the real data directory's mtime and file count are unchanged**",
        real_after == real_before,
        f"before={real_before} after={real_after}",
    )

    passed = sum(1 for ok, _, _ in RESULTS if ok)
    print(f"\n=== {passed}/{len(RESULTS)} passed ===")
    for ok, label, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {label} — {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())

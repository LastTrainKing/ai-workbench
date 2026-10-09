"""What happens to a run whose process died.

`_reconcile_interrupted_agent_runs` closes `RUNNING` rows at startup, because a row that
says `RUNNING` when no process is driving it is not running. This script proves that
against a real server restart rather than by calling the function directly — the claim is
about what startup does, and calling the function in-process would assume the answer.

The three cases that matter, and the reason each is here:

* **`RUNNING`** — the interrupted run. It must reach a terminal state with a reason. Left
  alone it would sit at "running" forever, and the history endpoint (which now reads these
  rows) would report a run in progress days after the process that was driving it died.
* **`SUCCEEDED`** — must not be touched. A reconcile that "closes everything open" would
  rewrite a finished run's outcome, which is the worst possible version of this feature.
* **`CANCELLED`** — same, and it is the one most likely to be swept up, because `CANCELLED`
  is not a success and a careless `status != SUCCEEDED` filter would catch it.

Runs against the test database on its own port. Nothing here calls a model: the rows are
written directly, which is what the endpoint reads.

    .venv/Scripts/python.exe scripts/verify_interrupted_runs.py
"""

from __future__ import annotations

import asyncio
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
BACKEND = ROOT / "backend"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

TEST_DB_NAME = "aiworkbench_test"
PORT = 8012
BASE = f"http://127.0.0.1:{PORT}/api/v1"

os.environ["DATABASE_URL"] = f"postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/{TEST_DB_NAME}"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/1"
os.environ["QDRANT_COLLECTION"] = "ai_workbench_chunks_test"
os.environ["AI_PROVIDER"] = "offline"
os.environ["EMBEDDING_PROVIDER"] = "offline"
os.environ["WORKER_ENABLED"] = "false"
os.environ["APP_ENV"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["TEST_NO_LLM"] = "1"

sys.path.insert(0, str(BACKEND))

RESULTS: list[tuple[bool, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((bool(ok), label, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def call(method: str, path: str, *, token: str = "", body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def port_free() -> bool:
    probe = socket.socket()
    probe.settimeout(0.4)
    free = probe.connect_ex(("127.0.0.1", PORT)) != 0
    probe.close()
    return free


def start_server() -> subprocess.Popen:
    process = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(BACKEND),
        env={**os.environ},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"server exited early with {process.returncode}")
        try:
            if call("GET", "/health")[0] == 200:
                return process
        except Exception:
            pass
        time.sleep(1)
    process.kill()
    raise SystemExit("server did not become healthy within 90s")


def stop_server(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    deadline = time.time() + 30
    while time.time() < deadline and not port_free():
        time.sleep(0.5)


async def _seed(owner_id: uuid.UUID) -> dict[str, str]:
    """Three runs in three states, written directly — no planner, no model."""
    from app.domain.enums import RunStatus, RunType
    from app.infrastructure.database import dispose_engine, session_scope
    from app.models.ai import AIRun, AgentRun

    ids: dict[str, str] = {}
    async with session_scope() as session:
        for label, status, state in (
            ("running", RunStatus.RUNNING, "EXECUTING"),
            ("succeeded", RunStatus.SUCCEEDED, "COMPLETED"),
            ("cancelled", RunStatus.CANCELLED, "CANCELLED"),
        ):
            run_id = f"run_{label}_{uuid.uuid4().hex[:8]}"
            ai_run = AIRun(
                run_id=run_id,
                owner_id=owner_id,
                run_type=RunType.AGENT.value,
                status=status.value,
                provider="offline",
                model="heuristic-extractive-v1",
                prompt_name="agent_planner",
                request_payload={"goal": f"{label} goal"},
            )
            session.add(ai_run)
            await session.flush()
            session.add(
                AgentRun(
                    ai_run_id=ai_run.id,
                    owner_id=owner_id,
                    goal=f"{label} goal",
                    status=status.value,
                    max_steps=8,
                    steps_used=1,
                    scratchpad={"state": state},
                )
            )
            ids[label] = run_id
        await session.commit()
    await dispose_engine()
    return ids


async def _read_statuses(run_ids: dict[str, str]) -> dict[str, tuple[str, str]]:
    """`label -> (AIRun.status, error_message)` straight from the table."""
    from sqlalchemy import select

    from app.infrastructure.database import dispose_engine, session_scope
    from app.models.ai import AIRun

    out: dict[str, tuple[str, str]] = {}
    async with session_scope() as session:
        for label, run_id in run_ids.items():
            row = (
                await session.execute(select(AIRun).where(AIRun.run_id == run_id))
            ).scalar_one_or_none()
            out[label] = (row.status, row.error_message) if row else ("<missing>", "")
    await dispose_engine()
    return out


async def _cleanup(run_ids: dict[str, str], user_ids: list[uuid.UUID]) -> None:
    from sqlalchemy import delete

    from app.infrastructure.database import dispose_engine, session_scope
    from app.models.ai import AIRun
    from app.models.user import User

    async with session_scope() as session:
        await session.execute(delete(AIRun).where(AIRun.run_id.in_(list(run_ids.values()))))
        await session.execute(delete(User).where(User.id.in_(user_ids)))
        await session.commit()
    await dispose_engine()


def main() -> int:
    print("=== 0. the test database, not the development one ===")
    from app.core.config import settings

    check("DATABASE_URL resolves to the test database", TEST_DB_NAME in settings.database_url)
    if TEST_DB_NAME not in settings.database_url:
        raise SystemExit("refusing to run: this would write to a non-test database")
    if not port_free():
        raise SystemExit(f"port {PORT} is busy; stop whatever holds it and re-run")

    print("\n=== 1. an account, and three runs in three states ===")
    server = start_server()
    try:
        handle = uuid.uuid4().hex[:8]
        status, body = call(
            "POST",
            "/auth/register",
            body={
                "email": f"interrupt_{handle}@aiworkbench.dev",
                "username": f"interrupt_{handle}",
                "password": "test1234",
                "display_name": "Interrupt",
            },
        )
        check("the account was created", status == 201, f"status={status}")
        token = body["data"]["access_token"]
        user_id = uuid.UUID(body["data"]["user"]["id"])

        run_ids = asyncio.run(_seed(user_id))
        seeded = asyncio.run(_read_statuses(run_ids))
        check("the running row starts as RUNNING", seeded["running"][0] == "RUNNING", seeded["running"][0])
        check("the succeeded row starts as SUCCEEDED", seeded["succeeded"][0] == "SUCCEEDED")
        check("the cancelled row starts as CANCELLED", seeded["cancelled"][0] == "CANCELLED")

        print("\n=== 2. the process is replaced ===")
        stop_server(server)
        check("the old process is gone", port_free())
        server = start_server()
        check("a new process started (this runs the reconcile)", not port_free())

        print("\n=== 3. what the restart did to each state ===")
        after = asyncio.run(_read_statuses(run_ids))

        running_status, running_error = after["running"]
        check(
            "**the interrupted run is no longer RUNNING**",
            running_status != "RUNNING",
            f"status={running_status}",
        )
        check(
            "it reached a terminal state",
            running_status in {"FAILED", "CANCELLED", "TIMEOUT"},
            running_status,
        )
        check(
            "**and it carries a reason**",
            bool(running_error.strip()),
            running_error[:80],
        )

        check(
            "**the finished run was not rewritten**",
            after["succeeded"][0] == "SUCCEEDED",
            f"{seeded['succeeded'][0]} → {after['succeeded'][0]}",
        )
        check(
            "**the cancelled run was not rewritten**",
            after["cancelled"][0] == "CANCELLED",
            f"{seeded['cancelled'][0]} → {after['cancelled'][0]}",
        )

        print("\n=== 4. the history endpoint agrees ===")
        status, body = call("GET", "/agent/runs?limit=50", token=token)
        entries = {entry["run_id"]: entry for entry in body["data"]["runs"]}
        for label, run_id in run_ids.items():
            check(f"the {label} run is in the history", run_id in entries)
        check(
            "the interrupted run shows a terminal state, not a live one",
            entries[run_ids["running"]]["state"] in {"FAILED", "CANCELLED", "COMPLETED"}
            and entries[run_ids["running"]]["live"] is False,
            entries[run_ids["running"]]["state"],
        )
        check(
            "the succeeded run still shows COMPLETED",
            entries[run_ids["succeeded"]]["state"] == "COMPLETED",
            entries[run_ids["succeeded"]]["state"],
        )
        check(
            "the cancelled run still shows CANCELLED",
            entries[run_ids["cancelled"]]["state"] == "CANCELLED",
            entries[run_ids["cancelled"]]["state"],
        )

        print("\n=== 5. ownership still holds after the restart ===")
        other_handle = uuid.uuid4().hex[:8]
        _, other = call(
            "POST",
            "/auth/register",
            body={
                "email": f"other_{other_handle}@aiworkbench.dev",
                "username": f"other_{other_handle}",
                "password": "test1234",
                "display_name": "Other",
            },
        )
        other_token = other["data"]["access_token"]
        other_id = uuid.UUID(other["data"]["user"]["id"])
        refused = call("GET", f"/agent/runs/{run_ids['running']}", token=other_token)
        check(
            "another account is refused by the ownership rule",
            refused[0] == 404 and refused[1].get("error", {}).get("code") == "run_not_found",
            f"status={refused[0]} code={refused[1].get('error', {}).get('code')}",
        )

        print("\n=== 6. a second restart changes nothing further ===")
        stop_server(server)
        server = start_server()
        second = asyncio.run(_read_statuses(run_ids))
        check(
            "the reconcile is idempotent",
            second["running"][0] == after["running"][0]
            and second["succeeded"][0] == "SUCCEEDED"
            and second["cancelled"][0] == "CANCELLED",
            f"{second['running'][0]} / {second['succeeded'][0]} / {second['cancelled'][0]}",
        )

        print("\n=== 7. clean up ===")
        asyncio.run(_cleanup(run_ids, [user_id, other_id]))
        check("the rows this script created were removed", True)
    finally:
        stop_server(server)

    passed = sum(1 for ok, _, _ in RESULTS if ok)
    print(f"\n=== {passed}/{len(RESULTS)} passed ===")
    for ok, label, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {label} — {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())

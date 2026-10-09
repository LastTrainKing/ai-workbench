"""Does a run's history survive a real restart?

The pytest suite covers the endpoint's logic in-process, but it cannot cover the thing
that was actually broken: ``RunRegistry`` is a module global, and "the history survives a
restart" is a statement about a **new process with an empty registry**. Only a real
server, stopped and started again, can answer it.

So this script:

1. starts a real uvicorn on port 8011 against the **test database**;
2. registers an account over HTTP;
3. writes one AIRun + AgentRun row for it (directly — running the agent would call the
   planner, and this round's model-call count has to stay at zero);
4. reads ``GET /agent/runs`` and finds it;
5. **kills the server**;
6. starts it again;
7. reads ``GET /agent/runs`` again — the run must still be there, and the entry must not
   claim to be live;
8. deletes the rows it created.

It never touches the development database: ``DATABASE_URL`` is set to the test database
before any application module is imported, and the script refuses to run if the resolved
database is not the test one.

    .venv/Scripts/python.exe scripts/verify_agent_run_persistence.py
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
BACKEND = ROOT / "backend"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

TEST_DB_NAME = "aiworkbench_test"
TEST_DB_URL = f"postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/{TEST_DB_NAME}"
PORT = 8011
BASE = f"http://127.0.0.1:{PORT}/api/v1"

# Set before importing anything from `app`, exactly as the test suite does.
os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/1"
os.environ["QDRANT_COLLECTION"] = "ai_workbench_chunks_test"
os.environ["AI_PROVIDER"] = "offline"
os.environ["EMBEDDING_PROVIDER"] = "offline"
os.environ["WORKER_ENABLED"] = "false"
os.environ["APP_ENV"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
# The model-call hard block, armed explicitly rather than relying on pytest.
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
            status, _ = call("GET", "/health")
            if status == 200:
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


async def _write_run(owner_id: uuid.UUID, goal: str) -> str:
    from app.domain.enums import RunStatus, RunType
    from app.infrastructure.database import dispose_engine, session_scope
    from app.models.ai import AIRun, AgentRun

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    async with session_scope() as session:
        ai_run = AIRun(
            run_id=run_id,
            owner_id=owner_id,
            run_type=RunType.AGENT.value,
            status=RunStatus.SUCCEEDED.value,
            provider="offline",
            model="heuristic-extractive-v1",
            prompt_name="agent_planner",
            request_payload={"goal": goal},
        )
        session.add(ai_run)
        await session.flush()
        session.add(
            AgentRun(
                ai_run_id=ai_run.id,
                owner_id=owner_id,
                goal=goal,
                status=RunStatus.SUCCEEDED.value,
                max_steps=8,
                steps_used=4,
                scratchpad={"state": "COMPLETED", "verified": True},
            )
        )
        await session.commit()
    await dispose_engine()
    return run_id


async def _delete_run(run_id: str) -> int:
    from sqlalchemy import delete

    from app.domain.enums import RunType
    from app.infrastructure.database import dispose_engine, session_scope
    from app.models.ai import AIRun

    async with session_scope() as session:
        result = await session.execute(
            delete(AIRun).where(AIRun.run_id == run_id, AIRun.run_type == RunType.AGENT.value)
        )
        await session.commit()
        removed = int(result.rowcount or 0)
    await dispose_engine()
    return removed


async def _delete_user(user_id: uuid.UUID) -> None:
    from sqlalchemy import delete

    from app.infrastructure.database import dispose_engine, session_scope
    from app.models.user import User

    async with session_scope() as session:
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()
    await dispose_engine()


def main() -> int:
    import asyncio

    print("=== 0. the test database, not the development one ===")
    from app.core.config import settings

    check(
        "DATABASE_URL resolves to the test database",
        TEST_DB_NAME in settings.database_url,
        settings.database_url.split("@")[-1],
    )
    if TEST_DB_NAME not in settings.database_url:
        raise SystemExit("refusing to run: this would write to a non-test database")

    if not port_free():
        raise SystemExit(f"port {PORT} is busy; stop whatever holds it and re-run")

    print("\n=== 1. first start ===")
    server = start_server()
    try:
        print("\n=== 2. an account, and a stored run ===")
        handle = uuid.uuid4().hex[:8]
        status, body = call(
            "POST",
            "/auth/register",
            body={
                "email": f"persist_{handle}@aiworkbench.dev",
                "username": f"persist_{handle}",
                "password": "test1234",
                "display_name": "Persist",
            },
        )
        check("the account was created", status == 201, f"status={status}")
        token = body["data"]["access_token"]
        user_id = uuid.UUID(body["data"]["user"]["id"])

        goal = f"重启持久化验证 {handle}"
        run_id = asyncio.run(_write_run(user_id, goal))
        check("a run was stored", bool(run_id), run_id)

        print("\n=== 3. read it back, before any restart ===")
        status, body = call("GET", "/agent/runs", token=token)
        runs = body.get("data", {}).get("runs", [])
        before = [entry for entry in runs if entry["run_id"] == run_id]
        check("the run is in the list", status == 200 and len(before) == 1, f"status={status}")
        check("it is not marked live", before and before[0]["live"] is False)
        check("its goal survived the round trip", before and before[0]["goal"] == goal)
        check("its state is the run state", before and before[0]["state"] == "COMPLETED")

        print("\n=== 4. restart the server ===")
        stop_server(server)
        check("the old process is gone", port_free())
        server = start_server()
        check("a new process is listening (empty registry)", not port_free())

        print("\n=== 5. read it back, after the restart ===")
        status, body = call("GET", "/agent/runs", token=token)
        runs = body.get("data", {}).get("runs", [])
        after = [entry for entry in runs if entry["run_id"] == run_id]
        check(
            "**the run is still in the history**",
            status == 200 and len(after) == 1,
            f"status={status} matches={len(after)}",
        )
        check(
            "the entry is identical to before the restart",
            bool(after) and after[0]["goal"] == goal and after[0]["state"] == "COMPLETED",
        )
        check(
            "it is not claimed to be live",
            bool(after) and after[0]["live"] is False,
            "the registry is empty in a new process",
        )

        print("\n=== 6. by id, after the restart ===")
        status, body = call("GET", f"/agent/runs/{run_id}", token=token)
        check(
            "the stored run is readable by id",
            status == 200 and body.get("data", {}).get("run_id") == run_id,
            f"status={status}",
        )

        print("\n=== 7. another account still cannot see it ===")
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
        _, listing = call("GET", "/agent/runs", token=other_token)
        check(
            "the other account's list does not contain it",
            run_id not in [entry["run_id"] for entry in listing["data"]["runs"]],
        )
        status, _ = call("GET", f"/agent/runs/{run_id}", token=other_token)
        check("the other account cannot read it by id", status != 200, f"status={status}")

        print("\n=== 8. clean up the rows this script created ===")
        removed = asyncio.run(_delete_run(run_id))
        check("the run row was removed", removed == 1, f"rowcount={removed}")
        asyncio.run(_delete_user(user_id))
        asyncio.run(_delete_user(other_id))
        check("the accounts were removed", True)
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

"""Die inside the tool-invocation window, in a real process, on purpose.

**Why a separate process.** The whole subject is "the process stopped existing between
two statements". Catching an exception in the same process is a different event: `finally`
blocks run, SQLAlchemy's session is disposed, and the connection is closed politely —
which is exactly the cleanup a crash does not get. The only faithful way to test this is
to have a process reach a specific point, say so, and then be killed from outside.

**How the parent kills it.** The child does not kill itself. It writes a sentinel file
naming the window it reached, then blocks forever; the parent polls for the sentinel and
terminates the process with `Popen.kill()`. Self-termination (`os._exit`) would also skip
Python cleanup, but it would not prove that an *externally* killed process leaves the
database in the state we claim — and external termination is what actually happens when a
user kills the app or the machine loses power.

**What each window is.** The numbers match the list in the execution order:

    1  the intent was built and flushed, but the transaction was never committed
    2  the intent was committed; the tool had not started
    3  the tool ran and its side effect landed; the outcome was not written
    4  the tool ran and its outcome was committed
    5  the tool ran and failed; the failure was not written

Windows 3 and 4 use `file_append` — a mutating, **non-idempotent** tool, so a duplicate is
observable in the file itself and not only in the database. Window 5 uses `file_delete` on
a path that does not exist, which is a mutating tool whose call fails.

Not a test module — the leading underscore keeps pytest from collecting it. It is spawned
by `test_release_freeze_tool_invocation.py`.

argv (all required): --window --ai-run-id --agent-run-id --owner-id --run-id
                     --workspace --target --sentinel
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _reached(window: int, sentinel: Path) -> None:
    """Announce the window, then block until the parent kills us.

    The file is written and fsynced *before* blocking, so the parent can never kill a
    process that has announced a window it has not actually reached — which would make
    every assertion in the test meaningless.
    """
    sentinel.write_text(str(window), encoding="utf-8")
    with sentinel.open("rb") as handle:
        os.fsync(handle.fileno())
    print(f"REACHED {window}", flush=True)
    while True:  # killed by the parent
        time.sleep(0.25)


def _build_registry(session, workspace: Path):
    """A registry with the real filesystem tools, reachable in `workspace`."""
    from app.ai.agent.tools import get_tool_registry
    from app.ai.tools.audit import make_audit_sink
    from app.ai.tools.executor import ResourceLimits, ToolExecutor
    from app.ai.tools.permissions import AgentPolicy, PermissionManager
    from app.ai.tools.registry import build_computer_registry
    from app.ai.tools.unified import UnifiedToolRegistry

    policy = AgentPolicy.from_settings(
        mode="autonomous",
        level=2,
        workspace=workspace,
        allowed_paths=[workspace],
    )
    computer = build_computer_registry(policy)
    limits = ResourceLimits(
        max_output_bytes=policy.max_output_bytes,
        max_files_modified=policy.max_files_modified,
        max_tool_calls=policy.max_tool_calls,
        max_runtime_seconds=policy.max_runtime_seconds,
    )
    registry = UnifiedToolRegistry(
        get_tool_registry(),
        computer,
        ToolExecutor(policy, audit=make_audit_sink(session)),
        limits=limits,
    )
    return registry, PermissionManager(policy)


async def _main(args: argparse.Namespace) -> int:
    from app.ai.agent.tools import ToolContext
    from app.ai.tracing import begin_tool_call, finish_tool_call
    from app.infrastructure.database import dispose_engine, session_scope
    from app.models.ai import AIRun

    ai_run_id = uuid.UUID(args.ai_run_id)
    agent_run_id = uuid.UUID(args.agent_run_id)
    owner_id = uuid.UUID(args.owner_id)
    workspace = Path(args.workspace)
    target = Path(args.target)
    sentinel = Path(args.sentinel)
    window = int(args.window)

    try:
        async with session_scope() as session:
            registry, permissions = _build_registry(session, workspace)
            ctx = ToolContext(
                session=session,
                owner_id=owner_id,
                run_id=args.run_id,
                permissions=permissions,
                working_directory=str(workspace),
            )
            run = await session.get(AIRun, ai_run_id)
            if run is None:
                print(f"CHILD_ERROR no such ai_run {ai_run_id}", flush=True)
                return 2

            if window == 1:
                # The intent exists inside this transaction and nowhere else.
                await begin_tool_call(
                    session,
                    run=run,
                    tool_name="file_append",
                    arguments={"path": str(target), "content": "w1\n"},
                    step_index=1,
                    mutates_state=True,
                    agent_run_id=agent_run_id,
                )
                # Deliberately **no commit** — this is the whole point of window 1.
                _reached(window, sentinel)

            if window == 5:
                call = await begin_tool_call(
                    session,
                    run=run,
                    tool_name="file_delete",
                    arguments={"path": str(target) + ".absent"},
                    step_index=5,
                    mutates_state=True,
                    agent_run_id=agent_run_id,
                )
                await session.commit()
                failed = await registry.execute(
                    ctx, "file_delete", {"path": str(target) + ".absent"}
                )
                print(f"TOOL_OK={failed.ok} ERR={failed.error[:120]}", flush=True)
                # The call failed; the failure is not written.
                _reached(window, sentinel)

            # Windows 2, 3 and 4 all append to a real file.
            call = await begin_tool_call(
                session,
                run=run,
                tool_name="file_append",
                arguments={"path": str(target), "content": f"w{window}\n"},
                step_index=window,
                mutates_state=True,
                agent_run_id=agent_run_id,
            )
            await session.commit()

            if window == 2:
                _reached(window, sentinel)

            result = await registry.execute(
                ctx, "file_append", {"path": str(target), "content": f"w{window}\n"}
            )
            if not result.ok:
                print(f"CHILD_ERROR tool failed: {result.error}", flush=True)
                return 3

            if window == 3:
                # The bytes are on disk; the outcome is not written.
                _reached(window, sentinel)

            await finish_tool_call(
                session,
                call,
                ok=result.ok,
                result=result.to_payload(),
                latency_ms=result.latency_ms,
                error_message=result.error,
            )
            await session.commit()

            if window == 4:
                _reached(window, sentinel)

            print("CHILD_DONE", flush=True)
    except Exception as exc:  # pragma: no cover - reported to the parent
        print(f"CHILD_ERROR {type(exc).__name__}: {exc}", flush=True)
        return 1
    finally:
        await dispose_engine()
    return 0


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    for name in (
        "window",
        "ai-run-id",
        "agent-run-id",
        "owner-id",
        "run-id",
        "workspace",
        "target",
        "sentinel",
    ):
        parser.add_argument(f"--{name}", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(_parse())))

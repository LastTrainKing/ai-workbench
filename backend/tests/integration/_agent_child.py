"""Runs one agent run and stays alive, so the parent can kill it mid-flight.

This exists as a separate process because "the process died during a run" cannot be tested
any other way. Catching an exception inside the same process is not the same event: the
reconcile path runs at *startup* and its whole job is to deal with a row that a process
left behind when it stopped existing. In-process, there is always a process to clean up
after itself.

Not a test module — the leading underscore keeps pytest from collecting it. It is spawned by
`test_release_freeze_stub_provider.py`.

argv:  owner_id  max_steps
env:   the same DATABASE_URL / REDIS_URL the parent used, plus the stub endpoint already
       written to the account's configuration file by the parent.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.ai.agent.executor import AgentExecutor
from app.infrastructure.database import dispose_engine, session_scope


async def _main(owner_id: str, max_steps: int) -> int:
    owner = uuid.UUID(owner_id)
    try:
        async with session_scope() as session:
            await AgentExecutor(max_steps=max_steps).run(
                session, owner_id=owner, goal="把当前项目里未完成的任务列出来"
            )
            await session.commit()
    except Exception as exc:
        print(f"CHILD_ERROR {type(exc).__name__}: {exc}", flush=True)
    finally:
        await dispose_engine()
    print("CHILD_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1], int(sys.argv[2]))))

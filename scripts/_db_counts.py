"""Row counts for the tables a restart must not disturb.

Run with whichever Python owns the database you want to ask:

    <install>/vendor/runtime/python/python.exe scripts/_db_counts.py
    .venv/Scripts/python.exe scripts/_db_counts.py

Prints one JSON object on the last line. A table that cannot be read gets `-1`, which is
deliberately distinct from `0`: "could not connect" and "empty" are different answers and
collapsing them is how a persistence check passes for the wrong reason.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

TABLES = [
    "users",
    "projects",
    "tasks",
    "memory_entries",
    "installed_skills",
    "knowledge_bases",
    "ai_runs",
    "agent_runs",
    "tool_audit_log",
    "deliverables",
    "conversations",
]


async def main() -> None:
    from sqlalchemy import text

    from app.infrastructure.database import session_scope

    counts: dict[str, int] = {}
    async with session_scope() as session:
        for table in TABLES:
            try:
                result = await session.execute(text(f'select count(*) from "{table}"'))
                counts[table] = int(result.scalar_one())
            except Exception:
                await session.rollback()
                counts[table] = -1
    print(json.dumps(counts))


if __name__ == "__main__":
    asyncio.run(main())

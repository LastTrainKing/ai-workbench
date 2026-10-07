"""After the suite runs, the user's memory library must contain no test litter.

Six rows reading 「这是一条浏览器测试写入的记忆」 were found sitting in the live memory
list, and the user's complaint about it was blunt: 「这种东西怎么能让用户自己调试呢」.
They came from `scripts/verify-context-ui.mjs`, which drives the real product against
the real database and used to add one on every run.

The pytest suite was never the problem — it has its own database
(`aiworkbench_test`) and its own storage (`.runtime/test-storage`). The browser
scripts do not: they log in as `demo` and click real buttons, which is exactly why they
are worth having and exactly why they have to clean up after themselves.

So this is the assertion that closes the loop. It is deliberately a separate script
rather than a pytest case, because it checks the *development* database — the one the
browser scripts write to — and a pytest case would be looking somewhere else.

    .venv/Scripts/python.exe scripts/verify-no-test-litter.py

Exits 0 when the library is clean, 1 when it is not, and prints every offending row.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import String, cast, or_, select  # noqa: E402

from app.infrastructure.database import session_scope  # noqa: E402
from app.models.context import MemoryCandidate, MemoryEntry, UserProfile  # noqa: E402

#: Text the verification scripts are known to write. Each one is a string that exists
#: only because a test typed it, so finding it in the library means a test left it there.
SIGNATURES = ("浏览器测试", "测试写入", "测试身份", "verify-", "pytest", "collect-test")


def _matches(*columns):
    return or_(*[column.like(f"%{sig}%") for column in columns for sig in SIGNATURES])


async def main() -> int:
    problems: list[str] = []

    async with session_scope() as session:
        entries = list(
            (await session.execute(select(MemoryEntry).where(_matches(MemoryEntry.content)))).scalars()
        )
        for row in entries:
            problems.append(f"memory_entries  {row.id}  status={row.status}  「{row.content[:60]}」")

        candidates = list(
            (
                await session.execute(
                    select(MemoryCandidate).where(_matches(MemoryCandidate.content))
                )
            ).scalars()
        )
        for row in candidates:
            problems.append(
                f"memory_candidates  {row.id}  status={row.status}  「{row.content[:60]}」"
            )

        profiles = list(
            (
                await session.execute(
                    select(UserProfile).where(
                        _matches(
                            UserProfile.identity,
                            # `current_goals` is JSONB; cast so LIKE can read it.
                            cast(UserProfile.current_goals, String),
                        )
                    )
                )
            ).scalars()
        )
        for row in profiles:
            problems.append(
                f"user_profile  {row.user_id}  identity=「{(row.identity or '')[:60]}」 "
                f"goals=「{row.current_goals}」"
            )

    if problems:
        print(f"[FAIL] 记忆库里有 {len(problems)} 条测试留下的文本：")
        for problem in problems:
            print(f"       {problem}")
        print()
        print("       测试写进系统的状态必须自己清掉。见 verify-context-ui.mjs 的成对清理。")
        return 1

    print("[PASS] 记忆库里没有测试特征文本")
    print("       已检查 memory_entries / memory_candidates / user_profile")
    print(f"       特征串：{'、'.join(SIGNATURES)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

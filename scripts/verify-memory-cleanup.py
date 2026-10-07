"""Show — and optionally execute — the one-time memory cleanup.

The user's library, before this round:

    13 条 active
      ├─ 7 条  「这是一条浏览器测试写入的记忆」        测试垃圾
      ├─ 4 条  「用户偏好图表充足、页数精简…」         同一件事的四种说法
      └─ 2 条  封面信息 / 报告完整性                  真正不同的两件事

Fixing the mechanism that produced them does not remove the ones already there. This is
the removal, and it runs in two steps on purpose: `--plan` writes nothing and prints
what it would do, and `--apply` executes exactly that plan. Nothing is deleted that the
user has not been shown.

    .venv/Scripts/python.exe scripts/verify-memory-cleanup.py --plan
    .venv/Scripts/python.exe scripts/verify-memory-cleanup.py --apply

Exits 0 when the plan is coherent (or the apply succeeded), 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.ai.memory.cleanup import apply_cleanup, plan_cleanup  # noqa: E402
from app.ai.providers.factory import build_llm_provider  # noqa: E402
from app.infrastructure.database import session_scope  # noqa: E402
from app.models.context import MemoryEntry  # noqa: E402
from app.models.user import User  # noqa: E402


async def demo_user_id() -> str:
    async with session_scope() as session:
        user = (
            await session.execute(select(User).where(User.username == "demo"))
        ).scalar_one_or_none()
        if user is None:
            raise RuntimeError("找不到 demo 账号")
        return user.id


async def show_library(user_id) -> list[MemoryEntry]:
    async with session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(MemoryEntry)
                    .where(MemoryEntry.user_id == user_id, MemoryEntry.status == "active")
                    .order_by(MemoryEntry.use_count.desc())
                )
            ).scalars()
        )
        return rows


def render(rows: list[MemoryEntry]) -> None:
    print(f"— 清理前：{len(rows)} 条已生效记忆 —")
    for row in rows:
        print(f"  {row.use_count:>4} 次  [{row.kind}] {row.content[:58]}")
    print()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true", help="只看计划，不写入")
    parser.add_argument("--apply", action="store_true", help="执行计划")
    args = parser.parse_args()
    if not args.plan and not args.apply:
        args.plan = True

    user_id = await demo_user_id()
    before = await show_library(user_id)
    render(before)

    llm, info = build_llm_provider()
    print(f"判定用模型：{info.kind} · {info.model}  degraded={info.degraded}")
    if info.degraded:
        print("[NOT VERIFIED] 模型不可用，清理计划无法生成（会走离线启发式）。")
        return 2

    async with session_scope() as session:
        plan = await plan_cleanup(session, user_id=user_id, llm=llm)

    print(f"计划：{plan.note}")
    print()

    if plan.duplicates:
        print(f"① 重复组 {len(plan.duplicates)} 组：")
        for group in plan.duplicates:
            print(f"   保留：「{group.keep_content[:50]}」")
            print(f"         现在用过 {group.keep_use_count} 次 → 合并后 {group.merged_use_count} 次")
            for row in group.merged:
                print(f"   并入：「{str(row['content'])[:50]}」（{row['use_count']} 次）")
            print(f"   依据：{group.why}")
    else:
        print("① 没有发现重复组")

    print()
    if plan.litter:
        print(f"② 测试垃圾 {len(plan.litter)} 条（将被删除）：")
        for row in plan.litter:
            print(f"   「{str(row['content'])[:50]}」 —— {row['why']}")
    else:
        print("② 没有发现测试垃圾")

    print()
    print(f"清理后预计剩 {plan.total_after} 条（现在 {plan.total_before} 条）")
    print()

    if not plan.duplicates and not plan.litter:
        print("[PASS] 没有需要清理的东西")
        return 0

    if args.plan:
        print("[PLAN] 以上只是计划，没有写入任何东西。加 --apply 执行。")
        return 0

    async with session_scope() as session:
        result = await apply_cleanup(session, user_id=user_id, plan=plan)

    print(f"执行结果：合并 {result.merged} 条，删除 {result.deleted} 条，保留 {result.kept} 组")
    for line in result.details:
        print(f"  {line}")
    print()

    after = await show_library(user_id)
    print(f"— 清理后：{len(after)} 条已生效记忆 —")
    for row in after:
        print(f"  {row.use_count:>4} 次  [{row.kind}] {row.content[:58]}")
    print()

    problems: list[str] = []
    if len(after) != plan.total_after:
        problems.append(f"条数对不上：预期 {plan.total_after}，实际 {len(after)}")
    # The accumulation the prompt asks for by name: 「合并后的那条要保留：最完整的
    # 那份措辞 + 累加的 use_count」.
    for group in plan.duplicates:
        row = next((r for r in after if str(r.id) == group.keep_id), None)
        if row is None:
            problems.append(f"保留的那条不见了：{group.keep_content[:30]}")
        elif row.use_count != group.merged_use_count:
            problems.append(
                f"use_count 没有累加：预期 {group.merged_use_count}，实际 {row.use_count}"
            )
    for row in plan.litter:
        if any(str(r.id) == str(row["id"]) for r in after):
            problems.append(f"垃圾没被删掉：{row['content'][:30]}")

    if problems:
        print("[FAIL]")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("[PASS] 清理完成，use_count 是累加的，垃圾已删除")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

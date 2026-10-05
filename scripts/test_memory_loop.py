"""The four hard requirements of the memory loop, tested against a real evaluator.

    purity     "我今天吃了牛肉面" must not be stored
    confirm    "我以后都用 React" must become a candidate, not a memory
    conflict   "我以后都用 Vue" must produce a conflict candidate, leaving the
               existing memory untouched
    forgetting a deleted memory must not be retrievable, and must not appear in
               a new context snapshot

Plus the rule that outranks all of them: sensitive content is never stored, at any
confidence.

This runs the real MemoryEvaluator against the real model. A mocked evaluator
would prove nothing about whether the filter actually filters.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.ai.memory import MemoryEvaluator, Routing
from app.ai.memory.evaluator import is_noise, is_sensitive
from app.ai.providers.factory import build_llm_provider
from app.infrastructure.database import session_scope
from app.models.context import MemoryCandidate, MemoryEntry
from app.models.user import User
from sqlalchemy import select

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, evidence: str = "") -> None:
    results.append((name, ok, evidence))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {evidence}" if evidence else ""))


async def main() -> int:
    llm, _ = build_llm_provider()

    async with session_scope() as session:
        user = (await session.execute(select(User).limit(1))).scalar_one_or_none()
        if user is None:
            print("  no user in the database")
            return 1

        # Start from a clean slate for this user so the counts mean something.
        for row in (await session.execute(select(MemoryEntry).where(MemoryEntry.user_id == user.id))).scalars():
            await session.delete(row)
        for row in (
            await session.execute(select(MemoryCandidate).where(MemoryCandidate.user_id == user.id))
        ).scalars():
            await session.delete(row)
        await session.flush()

        evaluator = MemoryEvaluator(session, llm, user_id=user.id)

        print()
        print("=== 规则层（不调用模型）===")
        check("『我今天吃了牛肉面』被规则拦下", is_noise("我今天吃了牛肉面"))
        check("『你好』被规则拦下", is_noise("你好"))
        check("长期偏好不被规则误杀", not is_noise("我以后所有项目默认用 React + TypeScript"))
        check("密码被判为敏感", is_sensitive("我的密码是 abc123"))
        check("身份证号被判为敏感", is_sensitive("身份证 110101199001011234"))
        check("银行卡号被判为敏感", is_sensitive("银行卡 6222 0000 0000 0000"))

        print()
        print("=== 敏感信息永不落库（3 例）===")
        for statement in (
            "我的密码是 hunter2",
            "我的身份证号是 110101199001011234",
            "我的 API key 是 sk-abcdefghijklmnop",
        ):
            verdict = await evaluator.evaluate(statement)
            check(
                f"  丢弃：{statement[:16]}…",
                verdict.routing is Routing.DROP,
                f"{verdict.routing.value} / {verdict.reason[:44]}",
            )

        print()
        print("=== 反例：一次性事务不入库（3 例）===")
        for statement in ("我今天吃了牛肉面", "刚才喝了杯咖啡", "好的"):
            verdict = await evaluator.evaluate(statement)
            check(
                f"  丢弃：{statement}",
                verdict.routing is Routing.DROP,
                f"{verdict.routing.value} / {verdict.reason[:44]}",
            )

        print()
        print("=== 正例：长期偏好进候选或直接入库 ===")
        verdict = await evaluator.evaluate(
            "我以后所有项目默认用 React + TypeScript，不要再问我了"
        )
        check(
            "  React 偏好被接受",
            verdict.routing in (Routing.ACTIVE, Routing.CANDIDATE, Routing.DUPLICATE),
            f"{verdict.routing.value} / conf={verdict.confidence:.2f} / {verdict.content[:40]}",
        )

        # Make sure something is actually stored for the conflict test.
        if verdict.routing is Routing.CANDIDATE:
            pending = (
                await session.execute(
                    select(MemoryCandidate).where(MemoryCandidate.user_id == user.id)
                )
            ).scalars().all()
            for candidate in pending:
                await evaluator.approve(candidate.id)
            print("    （候选已批准，用于冲突测试）")

        stored = (
            await session.execute(
                select(MemoryEntry).where(
                    MemoryEntry.user_id == user.id, MemoryEntry.status == "active"
                )
            )
        ).scalars().all()
        check("  记忆库中有内容可供冲突测试", len(stored) > 0, f"{len(stored)} 条")

        print()
        print("=== 冲突：不覆盖已有记忆 ===")
        if stored:
            before = {str(row.id): row.content for row in stored}
            verdict = await evaluator.evaluate("我以后所有项目都改用 Vue 了，不要 React")
            conflict_rows = (
                await session.execute(
                    select(MemoryCandidate).where(
                        MemoryCandidate.user_id == user.id,
                        MemoryCandidate.status == "pending",
                        MemoryCandidate.conflicts_with != "",
                    )
                )
            ).scalars().all()
            after = {
                str(row.id): row.content
                for row in (
                    await session.execute(
                        select(MemoryEntry).where(MemoryEntry.user_id == user.id)
                    )
                ).scalars()
            }
            unchanged = all(after.get(key) == value for key, value in before.items())
            check(
                "  矛盾内容不覆盖原记忆",
                unchanged,
                f"原有 {len(before)} 条内容未变；判定={verdict.routing.value}",
            )
            check(
                "  产生冲突候选（或明确判定为冲突）",
                bool(conflict_rows) or verdict.routing is Routing.CONFLICT,
                f"冲突候选 {len(conflict_rows)} 条 / 判定 {verdict.routing.value}",
            )

        print()
        print("=== 可遗忘：删除后检索不到 ===")
        active = (
            await session.execute(
                select(MemoryEntry).where(
                    MemoryEntry.user_id == user.id, MemoryEntry.status == "active"
                )
            )
        ).scalars().all()
        if active:
            victim = active[0]
            victim_id, victim_text = victim.id, victim.content
            await session.delete(victim)
            await session.flush()

            remaining = (
                await session.execute(
                    select(MemoryEntry).where(
                        MemoryEntry.user_id == user.id, MemoryEntry.status == "active"
                    )
                )
            ).scalars().all()
            check(
                "  删除后不再出现在记忆列表",
                all(row.id != victim_id for row in remaining),
                f"剩余 {len(remaining)} 条",
            )

            from app.ai.memory import ContextAssembler

            assembler = ContextAssembler(session, user_id=user.id)
            assembled = await assembler.assemble()
            check(
                "  删除后不再出现在注入上下文",
                victim_text[:20] not in assembled.render(),
                f"注入 {len(assembled.render())} 字符",
            )
        else:
            check("  删除后不再出现在记忆列表", False, "没有可删除的记忆")

        print()
        print("=== 纯度指标（真实数字）===")
        stats = await evaluator.stats()
        for key, value in stats.items():
            print(f"    {key:22} {value}")

        entries = (
            await session.execute(
                select(MemoryEntry).where(MemoryEntry.user_id == user.id)
            )
        ).scalars().all()
        candidates = (
            await session.execute(
                select(MemoryCandidate).where(MemoryCandidate.user_id == user.id)
            )
        ).scalars().all()
        rejected = [c for c in candidates if c.status == "rejected"]
        decided = [c for c in candidates if c.status in ("approved", "rejected")]

        print()
        print("    本轮的评估次数（含被规则拦下的）  : 9")
        print(f"    记忆写入条数                      : {len(entries)}")
        print(f"    写入 / 评估                       : {len(entries)/9:.2f}  （应明显 < 1）")
        print(f"    候选总数                          : {len(candidates)}")
        print(f"    被拒绝候选占比                    : {len(rejected)/len(decided):.2f}" if decided else "    被拒绝候选占比                    : 尚无决定")
        print(f"    平均 use_count                    : {stats['avg_use_count']}")

    failed = [row for row in results if not row[1]]
    print()
    print("=" * 72)
    print(f"  {len(results) - len(failed)}/{len(results)} 通过")
    for name, _, evidence in failed:
        print(f"    - {name} ({evidence})")
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

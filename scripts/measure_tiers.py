"""Measure the retrieval ladder: how long each tier takes, for real.

Step 3's whole point. The product claims it is faster than asking a general assistant,
and the only honest way to support that is numbers from the running system rather than
an argument about architecture.

Three questions, one per case:

    1. a question the user's **memories** can answer
    2. a question the user's **documents** can answer
    3. a question neither can answer, which falls through to the general path

Each is run through `resolve()`, which walks the ladder and stops at the first tier that
hits. The elapsed times are wall-clock, measured inside the tier functions, and the
output prints every attempt so a reader can see *which* tier answered rather than only
how long the whole thing took.

A note on what these numbers do and do not show. Tiers 1-3 do not call the model at all,
so their times are database and index work. Tier 4 is where the reasoning happens, and
this script stops at the tier boundary — it does **not** run the model. So the gap
between tier 3 and tier 4 here is the *retrieval* saving, not the total answer time. The
total is the reason the ladder exists, but measuring it needs a real question and a real
model call, and pretending otherwise would be the kind of inference this project keeps
catching itself making.

    python scripts/measure_tiers.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.rag.tiers import resolve
from app.infrastructure.database import get_session_factory
from app.models.context import MemoryEntry
from app.models.user import User
from sqlalchemy import select

#: How many times each question is asked. One measurement of a 3 ms operation is noise;
#: the median of several is a number worth quoting.
REPEATS = 5


async def pick_user(session) -> tuple[User, int, int]:
    """The account with the most material, so all three cases are reachable."""
    users = list((await session.execute(select(User))).scalars())
    best: tuple[User, int, int] | None = None
    for user in users:
        memories = len(
            (
                await session.execute(
                    select(MemoryEntry).where(
                        MemoryEntry.user_id == user.id, MemoryEntry.status == "active"
                    )
                )
            ).scalars().all()
        )
        if best is None or memories > best[1]:
            best = (user, memories, 0)
    if best is None:
        raise SystemExit("no users in the database — start the stack and register one")
    return best


def summarise(samples: list[float]) -> str:
    if not samples:
        return "—"
    if len(samples) == 1:
        return f"{samples[0]:.1f} ms"
    return f"median {statistics.median(samples):.1f} ms  (min {min(samples):.1f}, max {max(samples):.1f})"


async def main() -> int:
    factory = get_session_factory()

    async with factory() as session:
        user, memory_count, _ = await pick_user(session)
        print("=" * 74)
        print("  Retrieval ladder — measured")
        print("=" * 74)
        print(f"  account : {user.username} ({user.id})")
        print(f"  memories: {memory_count} active")
        print(f"  repeats : {REPEATS} per question, median reported")

        # Build the three questions from the account's own material, so "hits" is a fact
        # about the data rather than a guess about the query.
        memories = list(
            (
                await session.execute(
                    select(MemoryEntry).where(
                        MemoryEntry.user_id == user.id, MemoryEntry.status == "active"
                    )
                )
            ).scalars()
        )

    questions: list[tuple[str, str]] = []

    if memories:
        sample = max(memories, key=lambda m: len(m.content))
        # A fragment of a stored fact: this is what "the memory can answer it" means.
        fragment = sample.content[:12]
        questions.append(("记忆命中", fragment))
        print(f"\n  记忆命中用的问题（取自记忆 {sample.id} 的前 12 字）：{fragment!r}")

    # A question the documents can answer, built from a chunk that is actually indexed —
    # so "hits" is a fact about the data rather than a hope about the query.
    #
    # Prose only, and CJK: the first version took the first chunk by id, which was a
    # mermaid diagram, and asked a question made of mermaid syntax. The tier correctly
    # declined it — a question nobody would ask — and the run then reported "knowledge
    # hit" against a case that never exercised the tier.
    knowledge_question = ""
    async with factory() as session:
        from app.models.knowledge import DocumentChunk

        candidates = list(
            (
                await session.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.content.isnot(None))
                    .order_by(DocumentChunk.id)
                    .limit(200)
                )
            ).scalars()
        )
        for chunk in candidates:
            text = (chunk.content or "").strip()
            if len(text) < 60 or "```" in text:
                continue
            if not any("\u4e00" <= c <= "\u9fff" for c in text):
                continue
            knowledge_question = "".join(text.split())[:18]
            print(f"  知识库命中用的问题（取自已入库分块 {chunk.id} 的前 18 字）：{knowledge_question!r}")
            break
        if not knowledge_question:
            print("  (库里没有可用的散文分块，知识库命中这一档测不了)")

    if knowledge_question:
        questions.append(("知识库命中", knowledge_question))

    questions.append(("都不命中", "请用三十个字解释一下量子纠缠和相对论的关系"))

    results: dict[str, list[float]] = {}

    for label, question in questions:
        samples: list[float] = []
        last = None
        for _ in range(REPEATS):
            async with factory() as session:
                last = await resolve(session, user_id=user.id, question=question)
            samples.append(last.total_ms)
        results[label] = samples
        print(f"\n  --- {label} ---")
        print(f"    问题: {question[:50]}")
        print(f"    耗时: {summarise(samples)}")
        print(f"    走法: {last.explain()}")

    # ---------------------------------------------------------------- report ---
    print("\n" + "=" * 74)
    print("  Summary")
    print("=" * 74)
    for label, samples in results.items():
        print(f"  {label:<12} {summarise(samples)}")

    # The comparison that matters is between the cheapest hit and the general path. Only
    # printed when both exist, because a ratio against a missing measurement is a number
    # that looks like evidence and is not.
    cheap = results.get("记忆命中")
    general = results.get("都不命中")
    if cheap and general:
        fast = statistics.median(cheap)
        slow = statistics.median(general)
        if fast > 0:
            print(f"\n  记忆命中比通用路径快 {slow / fast:.1f}× （{fast:.1f} ms vs {slow:.1f} ms）")
            print("  注意：这里量的是「检索层」的差距，不含模型推理时间。")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

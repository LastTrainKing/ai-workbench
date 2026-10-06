"""Tiered retrieval: answer from the cheapest place that can answer.

The product claims it is *faster than asking a general assistant*, and the way it earns
that is not a faster model. It is not starting from zero. A question that a stored fact,
a known procedure, or a document in the user's own library can answer never reaches the
reasoning path at all.

    1. memory     one indexed lookup over facts about this user
    2. skill      a known way of doing this kind of work
    3. knowledge  one hybrid retrieval over the user's documents
    4. general    reasoning + tools + possibly the network  <- slowest, and the only
                  tier that is not personalised at all

**Every tier is timed, and whether it hit is recorded**, because "faster" without a
number is a claim rather than a result. The numbers this produces are the only evidence
that the ranking above is real — and the reason step 4 can say whether the skill layer
made anything faster.

Two rules the tiers enforce rather than merely document:

* **A miss is reported as a miss.** Tier 4 is not a fallback that pretends the first
  three were never asked; the answer carries which tiers were tried and what each said.
* **Tier 2 is present before it can hit.** The skill layer does not exist yet, so the
  tier returns a miss with a reason instead of being absent — an absent tier is
  indistinguishable from a tier that was never consulted.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.context import MemoryEntry

logger = get_logger(__name__)

#: Tier names, cheapest first. Order is the whole design.
TIERS = ("memory", "skill", "knowledge", "general")

#: Below this many matching terms, a memory is not considered relevant to the question.
#: Deliberately low: memories are short, and a single distinctive term ("毕设", "部长")
#: is often the entire signal. Raising it would make tier 1 miss on exactly the
#: questions it exists to answer.
MIN_TERM_OVERLAP = 1


@dataclass
class TierAttempt:
    """One tier's turn: whether it could answer, how long it took, and why."""

    tier: str
    hit: bool
    elapsed_ms: float
    detail: str = ""


@dataclass
class TieredResult:
    """The winning tier, every attempt, and the material to answer with."""

    tier: str
    attempts: list[TierAttempt] = field(default_factory=list)
    total_ms: float = 0.0
    memory_text: str = ""
    memory_ids: list[str] = field(default_factory=list)
    knowledge_chunks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def hit_tier(self) -> str:
        return self.tier

    def explain(self) -> str:
        """One line a person can read: which tier answered, and what the others said."""
        parts = []
        for attempt in self.attempts:
            mark = "命中" if attempt.hit else "未命中"
            parts.append(f"{attempt.tier}={mark}({attempt.elapsed_ms:.0f}ms)")
        return f"由「{self.tier}」回答 · " + " · ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "total_ms": round(self.total_ms, 1),
            "explanation": self.explain(),
            "attempts": [
                {"tier": a.tier, "hit": a.hit, "elapsed_ms": round(a.elapsed_ms, 1), "detail": a.detail}
                for a in self.attempts
            ],
        }


def _terms(text: str) -> set[str]:
    """Crude but predictable tokenisation for CJK and latin alike.

    Deliberately not a tokeniser dependency: this decides *whether to look closer*, not
    how to score. Every character bigram for CJK, every word for latin. A false positive
    costs one extra retrieval; a false negative sends a question that the user's own
    memory could answer down the expensive path, which is the failure this whole module
    exists to prevent.
    """
    lowered = text.lower()
    words = {w for w in "".join(c if c.isalnum() else " " for c in lowered).split() if len(w) > 1}
    cjk = [c for c in lowered if "\u4e00" <= c <= "\u9fff"]
    words |= {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    return words


async def _try_memory(session: AsyncSession, user_id: uuid.UUID, question: str) -> tuple[TierAttempt, str, list[str]]:
    """Tier 1: is there a stored fact about this user that bears on the question?

    Scored by term overlap rather than vector distance on purpose. The memory table is
    the *structured* store — the design says so — and an indexed scan over a user's few
    hundred memories is faster than any embedding call, which matters because this tier
    is the one that is supposed to be fastest.
    """
    started = time.perf_counter()
    rows = list(
        (
            await session.execute(
                select(MemoryEntry)
                .where(MemoryEntry.user_id == user_id, MemoryEntry.status == "active")
                .limit(200)
            )
        ).scalars()
    )
    if not rows:
        return (
            TierAttempt("memory", False, (time.perf_counter() - started) * 1000, "没有记忆"),
            "",
            [],
        )

    wanted = _terms(question)
    scored: list[tuple[int, MemoryEntry]] = []
    for row in rows:
        overlap = len(wanted & _terms(row.content))
        if overlap >= MIN_TERM_OVERLAP:
            scored.append((overlap, row))

    if not scored:
        return (
            TierAttempt("memory", False, (time.perf_counter() - started) * 1000, f"{len(rows)} 条记忆都不相关"),
            "",
            [],
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    picked = [row for _, row in scored[:5]]
    text = "\n".join(f"- {row.content}" for row in picked)
    detail = f"命中 {len(picked)} 条（共 {len(rows)} 条）"
    return (
        TierAttempt("memory", True, (time.perf_counter() - started) * 1000, detail),
        text,
        [str(row.id) for row in picked],
    )


async def _try_skill(question: str) -> TierAttempt:
    """Tier 2: is there a known way of doing this kind of work?

    The skill layer is step 4. Until it exists this tier reports a miss *with a reason*,
    rather than being left out of the list — a tier that is absent and a tier that was
    never consulted look identical in a report, and only one of them is true.
    """
    started = time.perf_counter()
    # Imported here rather than at module scope: the skill layer imports the retrieval
    # ladder back, and a top-level import would be a cycle.
    from app.ai.skills.registry import SKILL_LAYER_BUILT, find_matching_skills

    if not SKILL_LAYER_BUILT:
        # Say which of the two it is. An empty list here means "the layer is not built",
        # not "nothing matched", and reporting the second would be a lie told by a list
        # that is empty for a different reason.
        return TierAttempt("skill", False, (time.perf_counter() - started) * 1000, "skill 层尚未建立（第 4 步）")

    matches = await find_matching_skills(question)
    elapsed = (time.perf_counter() - started) * 1000
    if not matches:
        return TierAttempt("skill", False, elapsed, "没有匹配的 skill")
    names = "、".join(m.name for m in matches[:3])
    return TierAttempt("skill", True, elapsed, f"匹配 {len(matches)} 个：{names}")


async def _try_knowledge(
    session: AsyncSession, question: str, knowledge_base_ids: list[uuid.UUID] | None
) -> tuple[TierAttempt, list[dict[str, Any]]]:
    """Tier 3: does the user's own library hold the answer?"""
    from app.ai.rag.retriever import Retriever

    started = time.perf_counter()
    try:
        result = await Retriever().retrieve(
            session, query=question, knowledge_base_ids=knowledge_base_ids, top_k=5
        )
    except Exception as exc:
        # A retrieval that failed is not a retrieval that found nothing. Saying so is the
        # difference between "I have no material on this" and "I could not look".
        logger.warning("tier_knowledge_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        return (
            TierAttempt("knowledge", False, (time.perf_counter() - started) * 1000, f"检索失败：{exc}"),
            [],
        )

    chunks = list(result.chunks or [])
    elapsed = (time.perf_counter() - started) * 1000
    if not chunks:
        return TierAttempt("knowledge", False, elapsed, "知识库里没有相关资料"), []
    return TierAttempt("knowledge", True, elapsed, f"命中 {len(chunks)} 段"), [
        {"id": str(c.id), "document_id": str(c.document_id), "text": c.text, "score": c.score}
        for c in chunks
    ]


async def resolve(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    question: str,
    knowledge_base_ids: list[uuid.UUID] | None = None,
) -> TieredResult:
    """Walk the tiers in order and stop at the first that can answer.

    Stopping early *is* the optimisation. Asking all four every time would make the
    measurement useless — the point is that a question answerable from memory never pays
    for a knowledge retrieval, and one answerable from either never pays for reasoning.
    """
    started = time.perf_counter()
    result = TieredResult(tier="general")

    attempt, text, ids = await _try_memory(session, user_id, question)
    result.attempts.append(attempt)
    if attempt.hit:
        result.tier = "memory"
        result.memory_text = text
        result.memory_ids = ids
        result.total_ms = (time.perf_counter() - started) * 1000
        return result

    skill_attempt = await _try_skill(question)
    result.attempts.append(skill_attempt)
    if skill_attempt.hit:
        result.tier = "skill"
        result.total_ms = (time.perf_counter() - started) * 1000
        return result

    knowledge_attempt, chunks = await _try_knowledge(session, question, knowledge_base_ids)
    result.attempts.append(knowledge_attempt)
    if knowledge_attempt.hit:
        result.tier = "knowledge"
        result.knowledge_chunks = chunks
        result.total_ms = (time.perf_counter() - started) * 1000
        return result

    # Nothing personal answered it. Tier 4 is the general path, and the result says so
    # rather than presenting a general answer as though it came from the user's material.
    result.attempts.append(
        TierAttempt("general", True, (time.perf_counter() - started) * 1000, "前三层都没有，走通用路径")
    )
    result.total_ms = (time.perf_counter() - started) * 1000
    return result


__all__ = ["TIERS", "TierAttempt", "TieredResult", "resolve"]

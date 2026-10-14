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
* **Tier 2 distinguishes "not built" from "nothing matched".** The skill layer is live
  (`SKILL_LAYER_BUILT`), but the guard stays: an empty result list means "nothing
  matched", and "the layer is not built" has to be said in words. A list that is empty
  for a different reason is a lie told by a list.
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

#: How each tier is named to a person. The interface never shows the internal word.
#:
#: `general` is the one that matters most: it is the only tier that is not personalised,
#: and saying "通用推理" is the difference between "your library answered this" and "this
#: came from nowhere in particular" — a distinction the user has to be able to see.
TIER_LABELS: dict[str, str] = {
    "memory": "记忆",
    "skill": "Skill",
    "knowledge": "知识库",
    "general": "通用推理",
}

#: How relevant a retrieved chunk has to be before the library counts as having answered.
#:
#: **Measured, not guessed** — twice, which is the point. Both earlier attempts picked a
#: number first and looked at the data afterwards:
#:
#: 1. the fused score with a 0.35 cut-off. An unrelated question scored **0.70**, because
#:    the hybrid score is normalised *within the returned set* — its top value is high
#:    whether or not anything matched. Measured overlap between the two populations: 0.337.
#: 2. term overlap, the signal tier 1 uses. The same unrelated question matched on
#:    ordinary-word bigrams ("关系", "一下"). Overlap: 1.000.
#:
#: So `scripts/sample_relevance.py` was written to sample first: eight questions the
#: library genuinely covers, six plausible questions it does not, every candidate signal
#: printed for both groups. Results:
#:
#:     fused_top     covered 0.294-0.977   unrelated 0.099-0.632   overlap 0.337
#:     vector_top    covered 0.117-0.557   unrelated 0.097-0.124   overlap 0.007
#:     keyword_top   covered 0.000-14.565  unrelated 0.000-3.246   overlap 3.246
#:     overlap_top   covered 1.000-16.000  unrelated 0.000-2.000   overlap 1.000
#:     combined      covered 1.763-7.283   unrelated 0.647-1.623   overlap -0.140  <- separable
#:
#: The absolute vector similarity nearly separates on its own (0.007 of overlap against
#: the fused score's 0.337) because it is not normalised against the returned set — which
#: is exactly the property the two failures lacked. It fails in one place: the offline
#: hash embedding places an exact phrase poorly, so a question quoting the library can
#: score low. The sparse score fails in the opposite place: zero for a paraphrase sharing
#: no surface form. Taking the larger of the two covers both, and the populations separate.
#:
#: The margin is narrow (0.14) on fourteen samples, and that is stated rather than
#: smoothed over: this is a cut-off that works, not one with room to spare. It should be
#: re-measured if the corpus or the embedding changes — `sample_relevance.py` prints the
#: two distributions and the gap, so the check is one command.
RELEVANCE_FLOOR = 1.693

#: Divisors that put the two absolute signals on a comparable scale before comparing
#: them. They come from the same measurement: a vector score around 0.15 and a sparse
#: score around 2.0 are both "clearly about this", and dividing by those makes the larger
#: of the two readable as a single number.
VECTOR_SCALE = 0.15
KEYWORD_SCALE = 2.0


@dataclass
class TierAttempt:
    """One tier's turn: whether it could answer, how long it took, and why."""

    tier: str
    hit: bool
    elapsed_ms: float
    detail: str = ""
    #: The same fact as `detail`, said to a person instead of to whoever tunes this.
    #:
    #: Two fields rather than one because the audiences are genuinely different. The
    #: user's rule: 「用户看得懂的留，看不懂的删。毫秒数和 token 数是给你调优用的」.
    #: `detail` keeps the milliseconds and the thresholds — that is what
    #: `sample_relevance.py` and the startup logs are read for — and `summary` is what
    #: appears on screen. Collapsing them into one string is how the screen ended up
    #: showing `knowledge=命中(44ms)`.
    summary: str = ""


@dataclass
class TieredResult:
    """The winning tier, every attempt, and the material to answer with."""

    tier: str
    attempts: list[TierAttempt] = field(default_factory=list)
    total_ms: float = 0.0
    memory_text: str = ""
    memory_ids: list[str] = field(default_factory=list)
    knowledge_chunks: list[dict[str, Any]] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)

    @property
    def hit_tier(self) -> str:
        return self.tier

    def explain(self) -> str:
        """One line for a log or a tuning session: which tier, and what the others said.

        The detail is included, not just hit-or-miss. "knowledge=未命中(24ms)" says a tier
        was consulted and declined; *why* it declined — no material, or material that
        scored below the threshold — is the part that tells a reader whether the
        threshold is set correctly.

        **Not for the interface.** It carries milliseconds and thresholds, which answer a
        question the user did not ask. `summary()` is the sentence that goes on screen.
        """
        parts = []
        for attempt in self.attempts:
            mark = "命中" if attempt.hit else "未命中"
            reason = f"：{attempt.detail}" if attempt.detail else ""
            parts.append(f"{attempt.tier}={mark}({attempt.elapsed_ms:.0f}ms){reason}")
        return f"由「{self.tier}」回答 · " + " · ".join(parts)

    def summary(self) -> str:
        """The sentence a person reads: what answered this, in their words.

        Answers the question 「它凭什么这么说」 and nothing else. What did *not* answer is
        left out on purpose — a list of misses reads as a performance report, and the
        only thing that changes a person's behaviour is knowing whether the answer came
        from their own material.
        """
        label = TIER_LABELS.get(self.tier, self.tier)
        winner = next((a for a in self.attempts if a.tier == self.tier and a.hit), None)
        if self.tier == "general":
            return "这次没用到你的东西，走的是通用推理"
        detail = winner.summary if winner and winner.summary else ""
        return f"由「{label}」回答，{detail}" if detail else f"由「{label}」回答"

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "tier_label": TIER_LABELS.get(self.tier, self.tier),
            "total_ms": round(self.total_ms, 1),
            # What the interface shows by default.
            "summary": self.summary(),
            # Kept in the payload but not rendered: it is what the startup log and the
            # tuning scripts read, and dropping it would make this endpoint useless for
            # the one job it is genuinely good at.
            "explanation": self.explain(),
            "attempts": [
                {
                    "tier": a.tier,
                    "hit": a.hit,
                    "elapsed_ms": round(a.elapsed_ms, 1),
                    "detail": a.detail,
                    "summary": a.summary,
                }
                for a in self.attempts
            ],
        }


async def _try_memory(session: AsyncSession, user_id: uuid.UUID, question: str) -> tuple[TierAttempt, str, list[str]]:
    """Tier 1: is there a stored fact about this user that bears on the question?

    Scored by term rarity rather than vector distance on purpose. The memory table is
    the *structured* store — the design says so — and an indexed scan over a user's few
    hundred memories is faster than any embedding call, which matters because this tier
    is the one that is supposed to be fastest.

    The relevance rule lives in `app.ai.memory.relevance`, shared with the context
    assembler. It used to be a local `overlap >= 1` here, which is not a rule at all:
    Chinese bigrams like 「内容」 appear in several memories, so one shared term said
    nothing about *which* memory was about the request. See that module for the measured
    comparison.
    """
    from app.ai.memory.relevance import rank

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
            TierAttempt(
                "memory",
                False,
                (time.perf_counter() - started) * 1000,
                "没有记忆",
                "还没有关于你的记忆",
            ),
            "",
            [],
        )

    scored = rank(question, rows, limit=5)
    if not scored:
        return (
            TierAttempt(
                "memory",
                False,
                (time.perf_counter() - started) * 1000,
                f"{len(rows)} 条记忆都不相关",
                "没有和这件事相关的记忆",
            ),
            "",
            [],
        )

    picked = [row for _, row in scored]
    text = "\n".join(f"- {row.content}" for row in picked)
    detail = f"命中 {len(picked)} 条（共 {len(rows)} 条）"
    summary = f"用了 {len(picked)} 条关于你的记忆"
    return (
        TierAttempt("memory", True, (time.perf_counter() - started) * 1000, detail, summary),
        text,
        [str(row.id) for row in picked],
    )


async def _try_skill(
    session: AsyncSession, user_id: uuid.UUID, question: str
) -> tuple[TierAttempt, list[dict[str, Any]]]:
    """Tier 2: is there a known way of doing this kind of work?"""
    started = time.perf_counter()
    # Imported here rather than at module scope: the skill layer imports the retrieval
    # ladder back, and a top-level import would be a cycle.
    from app.ai.skills.registry import SKILL_LAYER_BUILT, find_matching_skills

    if not SKILL_LAYER_BUILT:
        # Say which of the two it is. An empty list here means "the layer is not built",
        # not "nothing matched", and reporting the second would be a lie told by a list
        # that is empty for a different reason.
        return (
            TierAttempt(
                "skill",
                False,
                (time.perf_counter() - started) * 1000,
                "skill 层尚未建立",
                "还没有可用的 Skill",
            ),
            [],
        )

    matches = await find_matching_skills(session, user_id=user_id, question=question)
    elapsed = (time.perf_counter() - started) * 1000
    if not matches:
        return (
            TierAttempt("skill", False, elapsed, "没有匹配的 skill", "没有匹配的 Skill"),
            [],
        )

    names = "、".join(m.name for m in matches[:3])
    return (
        TierAttempt(
            "skill",
            True,
            elapsed,
            f"匹配 {len(matches)} 个：{names}",
            f"用了 Skill「{names}」",
        ),
        # `body` must travel with the match. Passing only id/name/score is how the
        # agent ended up being told 「【他的做法：X】」 with nothing after it.
        [
            {"id": m.id, "name": m.name, "score": m.score, "body": m.body}
            for m in matches
        ],
    )


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
            TierAttempt(
                "knowledge",
                False,
                (time.perf_counter() - started) * 1000,
                f"检索失败：{exc}",
                "没能查到你的资料（检索出错了）",
            ),
            [],
        )

    chunks = list(result.chunks or [])
    elapsed = (time.perf_counter() - started) * 1000
    if not chunks:
        return (
            TierAttempt(
                "knowledge",
                False,
                elapsed,
                "知识库里没有相关资料",
                "你的资料里没有相关的",
            ),
            [],
        )

    def relevance(chunk: Any) -> float:
        """The combined absolute signal, on one scale. See RELEVANCE_FLOOR."""
        vector = getattr(chunk, "vector_score", 0.0) or 0.0
        keyword = getattr(chunk, "keyword_score", 0.0) or 0.0
        return max(vector / VECTOR_SCALE, keyword / KEYWORD_SCALE)

    scored = sorted(((relevance(c), c) for c in chunks), key=lambda pair: pair[0], reverse=True)
    top_score, _ = scored[0]

    if top_score < RELEVANCE_FLOOR:
        # The retriever found *something*, because it always does. Below the floor that is
        # not an answer, and saying so is the difference between "your library covers
        # this" and "here are five loosely related passages" — the second of which the
        # user would read as sourced when it is not.
        return (
            TierAttempt(
                "knowledge",
                False,
                elapsed,
                f"检索到 {len(chunks)} 段，最相关的一段相关度 {top_score:.2f}，低于 {RELEVANCE_FLOOR} —— 视为没有相关资料",
                "你的资料里没有相关的",
            ),
            [],
        )

    kept = [c for score, c in scored if score >= RELEVANCE_FLOOR]
    return (
        TierAttempt(
            "knowledge",
            True,
            elapsed,
            f"命中 {len(kept)} 段（相关度 {top_score:.2f}）",
            f"用了 {len(kept)} 段你的资料",
        ),
        [
            {
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "document_title": c.document_title,
                "text": c.content,
                "score": c.score,
                "relevance": round(relevance(c), 3),
            }
            for c in kept
        ],
    )


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

    skill_attempt, matched = await _try_skill(session, user_id, question)
    result.attempts.append(skill_attempt)
    if skill_attempt.hit:
        result.tier = "skill"
        result.skills = matched
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
        TierAttempt(
            "general",
            True,
            (time.perf_counter() - started) * 1000,
            "前三层都没有，走通用路径",
            "没有用到你的东西",
        )
    )
    result.total_ms = (time.perf_counter() - started) * 1000
    return result


__all__ = ["TIERS", "TIER_LABELS", "TierAttempt", "TieredResult", "resolve"]

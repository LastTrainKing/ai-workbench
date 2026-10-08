"""Memory lifecycle: merge, decay, conflict, and the report that says what happened.

The user's words: 「宜精不宜多，保证记忆库的精与纯净。」 The library has a table, a write
path and a confirmation step, but nothing that ever *removes* anything — so it can only
grow, and a memory store that only grows stops being about the person and starts being a
transcript.

Four operations, and one rule that governs all of them:

* **merge** — near-identical memories become one, with `use_count` summed. The others are
  marked `merged`, not deleted, and point at the survivor: the fact that two phrasings
  existed is itself information about how the person says things.
* **decay** — long-unused, low-confidence memories leave retrieval but stay in the table.
  "Forget" in this system means "stop using", not "destroy" — a decayed memory can come
  back the moment it is mentioned again, which a deleted one cannot.
* **conflict** — two memories that contradict each other become a *candidate for the user
  to settle*. Neither existing row changes. The system proposes; the person decides. That
  is the same rule the memory confirmation step already follows, applied to contradictions.
* **review** — using a memory updates `use_count` and `last_used_at`, which is what makes
  the decay decision evidence-based rather than a guess about what mattered.

**The one rule: nothing here deletes.** Only an explicit user action deletes, and when it
does, the memory must genuinely disappear from retrieval — `purge` exists to make that
testable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.memory.similarity import (
    MERGE_THRESHOLD,
    containment,
    named_tokens,
    same_thing,
    similarity,
)
from app.ai.memory.similarity import (
    terms as _terms,
)
from app.core.logging import get_logger
from app.models.context import MemoryCandidate, MemoryEntry

logger = get_logger(__name__)

#: `MERGE_THRESHOLD`, `similarity` and `_terms` now live in `similarity.py` and are
#: re-exported here because callers and tests already import them from this module.
#: The *decision* they feed is `same_thing`, which is what `find_merge_groups` uses —
#: a bare threshold could not refuse 「用户喜欢用 Python」 / 「用户喜欢用 Java」, and the
#: measurement in that module is why.

#: A memory nobody has used in this long, and that was never very confident, leaves
#: retrieval. 90 days is long enough that a seasonal fact (a semester, a project) is not
#: discarded mid-use, and short enough that a stale one stops being injected.
DECAY_AFTER_DAYS = 90

#: Below this confidence, decay applies. A high-confidence memory is one the user
#: confirmed; it does not decay on a timer, because being sure is not the same as being
#: recent.
DECAY_CONFIDENCE_BELOW = 0.75

#: Negation markers. Used to spot two memories that are about the same thing but disagree.
_NEGATIONS = ("不", "别", "不要", "不用", "no ", "not ", "never ", "don't", "dont")


def has_negation(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _NEGATIONS)


@dataclass
class MergeGroup:
    survivor_id: str
    survivor_content: str
    merged_ids: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class Conflict:
    left_id: str
    right_id: str
    left: str
    right: str
    reason: str = ""


@dataclass
class ConsolidationReport:
    """Real counts, so the report can be checked rather than believed."""

    total: int = 0
    active_before: int = 0
    merged: int = 0
    decayed: int = 0
    conflicts: int = 0
    candidates_created: int = 0
    average_use_count: float = 0.0
    never_used: int = 0
    merge_groups: list[MergeGroup] = field(default_factory=list)
    conflict_list: list[Conflict] = field(default_factory=list)

    def summarise(self) -> str:
        if not self.total:
            return "记忆库是空的。"
        return (
            f"共 {self.total} 条记忆（{self.active_before} 条活跃）："
            f"合并 {self.merged} 条，衰减 {self.decayed} 条，发现 {self.conflicts} 处冲突，"
            f"平均被用过 {self.average_use_count:.1f} 次，{self.never_used} 条从没被用过。"
        )


# ------------------------------------------------------------------------- merging --


def find_merge_groups(memories: list[MemoryEntry]) -> list[MergeGroup]:
    """Memories that are the same fact said twice.

    Greedy and single-pass: each memory joins the first group it is similar enough to, and
    the survivor is the one with the highest use count — the phrasing that has actually
    been used is the one worth keeping.

    The test is `same_thing`, not a bare threshold, and the difference is measurable:
    「用户喜欢用 Python 写脚本」 and 「用户喜欢用 Java 写脚本」 score 0.778 — higher than
    any genuine rephrasing in `scripts/sample_memory_similarity.py` — so a threshold
    merges them and loses the fact that the user switched languages. `same_thing` also
    requires the two to name the same things, which refuses that pair and leaves every
    rephrasing untouched.

    Claimed rows are tracked by **object identity**, not by database id. The first version
    used `id`, which is `None` on a row that has not been flushed yet — so every memory in
    a caller's list shared the key `"None"` and the claiming logic collapsed. Rows loaded
    from the database always have an id, which is exactly why the bug would have survived
    to production and only shown up in a caller that built its list in memory.
    """
    groups: list[MergeGroup] = []
    claimed: set[int] = set()

    ordered = sorted(memories, key=lambda m: m.use_count, reverse=True)
    for memory in ordered:
        if id(memory) in claimed:
            continue
        group = MergeGroup(survivor_id=str(memory.id), survivor_content=memory.content)
        for other in ordered:
            if other is memory or id(other) in claimed:
                continue
            # Same kind only. A `preference` and a `fact` that share wording are not the
            # same thing, and merging them would lose the distinction the kind carries.
            if other.kind != memory.kind:
                continue
            if same_thing(memory.content, other.content):
                group.merged_ids.append(str(other.id))
                claimed.add(id(other))

        if group.merged_ids:
            group.reason = (
                f"{len(group.merged_ids)} 条与它说的是同一件事"
                f"（用词重合 ≥ {MERGE_THRESHOLD}，且没有指向不同的事物），保留被用过最多的那条"
            )
            claimed.add(id(memory))
            groups.append(group)

    return groups


async def apply_merge(session: AsyncSession, group: MergeGroup) -> int:
    """Fold the duplicates into the survivor. Nothing is deleted."""
    survivor = await session.get(MemoryEntry, uuid.UUID(group.survivor_id))
    if survivor is None:
        return 0

    merged_count = 0
    for other_id in group.merged_ids:
        other = await session.get(MemoryEntry, uuid.UUID(other_id))
        if other is None:
            continue
        # The usage of the duplicates is real usage of the same fact, so it moves to the
        # survivor rather than being discarded with the row.
        survivor.use_count += other.use_count
        survivor.confidence = max(survivor.confidence, other.confidence)
        other.status = "merged"
        structured = dict(other.structured or {})
        structured["merged_into"] = group.survivor_id
        other.structured = structured
        merged_count += 1

    await session.flush()
    return merged_count


# ------------------------------------------------------------------------- decay --


def is_stale(memory: MemoryEntry, *, now: datetime | None = None) -> tuple[bool, str]:
    """Whether a memory should leave retrieval, and why.

    Confidence gates it as well as time: a memory the user confirmed does not decay on a
    timer, because being sure is not the same as being recent.
    """
    if memory.status != "active":
        return False, ""
    if memory.confidence >= DECAY_CONFIDENCE_BELOW:
        return False, ""

    moment = now or datetime.now(UTC)
    reference = memory.last_used_at or memory.created_at
    if reference is None:
        return False, ""
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)

    age = (moment - reference).days
    if age >= DECAY_AFTER_DAYS:
        return True, f"{age} 天没被用过，置信度 {memory.confidence:.2f}"
    return False, ""


async def decay_stale(session: AsyncSession, *, user_id: uuid.UUID) -> tuple[int, list[str]]:
    """Move stale memories out of retrieval. Not deleted — see the module docstring."""
    rows = list(
        (
            await session.execute(
                select(MemoryEntry).where(
                    MemoryEntry.user_id == user_id, MemoryEntry.status == "active"
                )
            )
        ).scalars()
    )

    decayed = 0
    details: list[str] = []
    for row in rows:
        stale, reason = is_stale(row)
        if stale:
            row.status = "decayed"
            decayed += 1
            details.append(f"{row.content[:40]} —— {reason}")

    if decayed:
        await session.flush()
    return decayed, details


# ---------------------------------------------------------------------- conflicts --


def find_conflicts(memories: list[MemoryEntry]) -> list[Conflict]:
    """Memories about the same thing that disagree.

    Detected as: same kind, high similarity, and exactly one of them carries a negation.
    That is a deliberately narrow rule. "They contradict" is not decidable in general, and
    a detector that guessed would fill the review queue with pairs that only look
    contradictory — which is worse than missing some, because the queue stops being read.
    """
    conflicts: list[Conflict] = []
    active = [m for m in memories if m.status == "active"]

    for index, left in enumerate(active):
        for right in active[index + 1 :]:
            if left.kind != right.kind:
                continue
            score = similarity(left.content, right.content)
            if score < 0.5:
                continue
            if has_negation(left.content) == has_negation(right.content):
                # Same polarity: similar, not contradictory. Overlap alone is not a
                # conflict, or every paraphrase would be reported as one.
                continue
            conflicts.append(
                Conflict(
                    left_id=str(left.id),
                    right_id=str(right.id),
                    left=left.content,
                    right=right.content,
                    reason=f"同属「{left.kind}」，说法相似（{score:.0%}）但一个带否定、一个不带",
                )
            )
    return conflicts


async def raise_conflict_candidates(
    session: AsyncSession, *, user_id: uuid.UUID, conflicts: list[Conflict]
) -> int:
    """Ask the user to settle each conflict. **Neither existing row is touched.**

    The system proposes and the person decides — the same rule the memory confirmation
    step follows. A system that silently picked a winner would be editing someone's
    memory of themselves on their behalf.
    """
    created = 0
    for conflict in conflicts:
        existing = (
            await session.execute(
                select(MemoryCandidate).where(
                    MemoryCandidate.user_id == user_id,
                    MemoryCandidate.status == "pending",
                    MemoryCandidate.proposed_kind == "conflict",
                    MemoryCandidate.content.like(f"%{conflict.left[:20]}%"),
                )
            )
        ).scalars().first()
        if existing is not None:
            continue

        session.add(
            MemoryCandidate(
                user_id=user_id,
                proposed_kind="conflict",
                content=f"这两条记忆互相矛盾，哪一条对？\nA: {conflict.left}\nB: {conflict.right}",
                structured={
                    "conflict": True,
                    "left_id": conflict.left_id,
                    "right_id": conflict.right_id,
                    "reason": conflict.reason,
                },
                confidence=0.5,
                status="pending",
            )
        )
        created += 1

    if created:
        await session.flush()
    return created


# ------------------------------------------------------------------- the scan --


async def consolidate(
    session: AsyncSession, *, user_id: uuid.UUID, dry_run: bool = False
) -> ConsolidationReport:
    """The periodic pass: merge, decay, surface conflicts, and report what changed.

    `dry_run` computes the report without writing, so the numbers can be inspected before
    anything is changed — the same reason the assess stage exists for skills.
    """
    rows = list(
        (
            await session.execute(select(MemoryEntry).where(MemoryEntry.user_id == user_id))
        ).scalars()
    )
    active = [r for r in rows if r.status == "active"]

    report = ConsolidationReport(
        total=len(rows),
        active_before=len(active),
        average_use_count=(sum(r.use_count for r in rows) / len(rows)) if rows else 0.0,
        never_used=len([r for r in rows if r.use_count == 0]),
    )

    groups = find_merge_groups(active)
    conflicts = find_conflicts(active)

    # A contradiction must never be merged. Two memories that disagree are *similar* — that
    # is what makes them a contradiction rather than two unrelated facts — so without this
    # the merge step eats them before the conflict step can surface them, and the survivor
    # is whichever phrasing happened to be used more. That is the system silently deleting
    # one of the user's beliefs and keeping the other, which is the opposite of what a
    # conflict candidate is for.
    #
    # Measured: 「用户喜欢在汇报里用图表」 and 「用户不喜欢在汇报里用图表」 score 0.75,
    # comfortably above the 0.62 merge threshold. A test caught it.
    conflicting_ids = {c.left_id for c in conflicts} | {c.right_id for c in conflicts}
    groups = [g for g in groups if g.survivor_id not in conflicting_ids]

    report.merge_groups = groups
    report.conflict_list = conflicts
    report.conflicts = len(conflicts)

    if not dry_run:
        for group in groups:
            report.merged += await apply_merge(session, group)
        report.decayed, _ = await decay_stale(session, user_id=user_id)
        report.candidates_created = await raise_conflict_candidates(
            session, user_id=user_id, conflicts=conflicts
        )
    else:
        # A dry run still reports what *would* happen, so the numbers are comparable.
        report.merged = sum(len(g.merged_ids) for g in groups)
        report.decayed = len([r for r in active if is_stale(r)[0]])

    logger.info(
        "memory_consolidated",
        extra={
            "user": str(user_id),
            "merged": report.merged,
            "decayed": report.decayed,
            "conflicts": report.conflicts,
            "dry_run": dry_run,
        },
    )
    return report


# --------------------------------------------------------------------- forgetting --


async def purge(session: AsyncSession, *, user_id: uuid.UUID, memory_id: uuid.UUID) -> bool:
    """Actually delete a memory, and make sure it is really gone.

    The product's rule is 「可遗忘且真的遗忘」 — a delete that leaves the row behind would
    be a delete in name only. Returns whether a row was removed, so the caller can tell
    "deleted" from "there was nothing there".
    """
    result = await session.execute(
        delete(MemoryEntry).where(MemoryEntry.id == memory_id, MemoryEntry.user_id == user_id)
    )
    await session.flush()
    return bool(result.rowcount)


async def is_forgotten(session: AsyncSession, *, user_id: uuid.UUID, memory_id: uuid.UUID) -> bool:
    """Used by the tests, and by anything that needs to prove a deletion took."""
    row = (
        await session.execute(
            select(MemoryEntry).where(
                MemoryEntry.id == memory_id, MemoryEntry.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    return row is None


async def memory_stats(session: AsyncSession, *, user_id: uuid.UUID) -> dict[str, Any]:
    """The numbers the acceptance asks for, computed rather than estimated."""
    rows = list(
        (
            await session.execute(select(MemoryEntry).where(MemoryEntry.user_id == user_id))
        ).scalars()
    )
    candidates = list(
        (
            await session.execute(
                select(MemoryCandidate).where(MemoryCandidate.user_id == user_id)
            )
        ).scalars()
    )

    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1

    approved = len([c for c in candidates if c.status == "approved"])
    rejected = len([c for c in candidates if c.status == "rejected"])
    decided = approved + rejected

    return {
        "total": len(rows),
        "by_status": by_status,
        "active": by_status.get("active", 0),
        "merged": by_status.get("merged", 0),
        "decayed": by_status.get("decayed", 0),
        "average_use_count": round(sum(r.use_count for r in rows) / len(rows), 2) if rows else 0.0,
        "never_used": len([r for r in rows if r.use_count == 0]),
        "candidates_total": len(candidates),
        "candidates_pending": len([c for c in candidates if c.status == "pending"]),
        "rejected_ratio": round(rejected / decided, 3) if decided else None,
        "decay_after_days": DECAY_AFTER_DAYS,
        "merge_threshold": MERGE_THRESHOLD,
    }


# `noqa: RUF022` — `_terms` is deliberately first and outside the alphabetical order.
#
# It is a **re-export**: `scripts/sample_memory_similarity.py` imports it from this
# module. Ruff only lints `app/` and `tests/`, so it cannot see that use, and its F401
# fix on 2026-10-08 deleted the re-export and broke the script. Listing it in `__all__`
# is what keeps it alive, and putting it first is what keeps it visible to the next
# person who wonders why an unused import is sitting there.
__all__ = [  # noqa: RUF022
    "_terms",
    "DECAY_AFTER_DAYS",
    "DECAY_CONFIDENCE_BELOW",
    "MERGE_THRESHOLD",
    "Conflict",
    "ConsolidationReport",
    "MergeGroup",
    "apply_merge",
    "consolidate",
    "containment",
    "decay_stale",
    "find_conflicts",
    "find_merge_groups",
    "has_negation",
    "is_forgotten",
    "is_stale",
    "memory_stats",
    "named_tokens",
    "purge",
    "raise_conflict_candidates",
    "similarity",
]


"""Memory lifecycle: merge, decay, conflict, forget.

The acceptance numbers are all asserted here — merged count, decayed count, average
use_count, rejected ratio — because the task's requirement is 「必须给真实数字」 and a
number produced by a function nobody tested is not a number anybody should quote.

Three rules are asserted from both sides, because each has a failure mode that passes a
one-sided test:

* a merge that merges everything and one that merges nothing both look fine until you
  check that unrelated memories survive;
* a decay that decays everything and one that decays nothing likewise;
* a conflict detector that flags every paraphrase and one that flags none.

And the one that matters most: **`purge` must really purge.** 「可遗忘且真的遗忘」 is
falsifiable, so it is falsified here rather than asserted in prose.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.memory.lifecycle import (
    DECAY_AFTER_DAYS,
    MERGE_THRESHOLD,
    consolidate,
    find_conflicts,
    find_merge_groups,
    is_forgotten,
    is_stale,
    memory_stats,
    purge,
    similarity,
)
from app.models.context import MemoryCandidate, MemoryEntry
from app.models.user import User


async def make_user(session: AsyncSession) -> uuid.UUID:
    user = User(
        email=f"mem_{uuid.uuid4().hex[:10]}@aiworkbench.dev",
        username=f"mem_{uuid.uuid4().hex[:10]}",
        display_name="Memory Test",
        hashed_password="unused",
    )
    session.add(user)
    await session.flush()
    return user.id


def memory(
    user_id: uuid.UUID,
    content: str,
    *,
    kind: str = "fact",
    use_count: int = 0,
    confidence: float = 0.8,
    status: str = "active",
    last_used_at: datetime | None = None,
) -> MemoryEntry:
    row = MemoryEntry(
        user_id=user_id,
        kind=kind,
        scope="global",
        content=content,
        confidence=confidence,
        status=status,
        use_count=use_count,
    )
    row.last_used_at = last_used_at
    return row


# ------------------------------------------------------------------------- merging --


async def test_similarity_separates_same_from_different() -> None:
    same = similarity(
        "用户在做毕业设计，需要写文献综述",
        "用户正在做毕业设计，要写文献综述",
    )
    different = similarity(
        "用户在做毕业设计，需要写文献综述",
        "用户不吃香菜，口味清淡",
    )
    assert same >= MERGE_THRESHOLD, f"同一件事的两种说法应该达到阈值，实际 {same:.2f}"
    assert different < MERGE_THRESHOLD, f"不同的事不该达到阈值，实际 {different:.2f}"


async def test_merge_groups_keep_the_most_used_phrasing() -> None:
    user_id = uuid.uuid4()
    memories = [
        memory(user_id, "用户在做毕业设计，需要写文献综述", use_count=1),
        memory(user_id, "用户正在做毕业设计，要写文献综述", use_count=7),
        memory(user_id, "用户不吃香菜", use_count=0),
    ]
    groups = find_merge_groups(memories)
    assert len(groups) == 1, "只有那一对是重复的"
    assert groups[0].survivor_content == "用户正在做毕业设计，要写文献综述", "保留被用过最多的那条"
    assert len(groups[0].merged_ids) == 1


async def test_unrelated_memories_are_not_merged() -> None:
    """The other side. A merger that folds everything together destroys the library."""
    user_id = uuid.uuid4()
    memories = [
        memory(user_id, "用户在做毕业设计"),
        memory(user_id, "用户不吃香菜"),
        memory(user_id, "用户偏好图表充足的汇报"),
    ]
    assert find_merge_groups(memories) == []


async def test_different_kinds_are_never_merged() -> None:
    """A `preference` and a `fact` that share wording are not the same thing, and merging
    them would lose the distinction the kind carries."""
    user_id = uuid.uuid4()
    memories = [
        memory(user_id, "用户在做毕业设计", kind="fact"),
        memory(user_id, "用户在做毕业设计", kind="goal"),
    ]
    assert find_merge_groups(memories) == []


async def test_merge_accumulates_use_count_and_deletes_nothing(session: AsyncSession) -> None:
    user_id = await make_user(session)
    # The survivor is the one with the higher use count — the phrasing that has actually
    # been used is the one worth keeping. The first version of this test asserted on the
    # other object and reported the accumulation as broken when it was the assertion that
    # named the wrong row.
    fewer = memory(user_id, "用户在做毕业设计，需要写文献综述", use_count=2)
    more = memory(user_id, "用户正在做毕业设计，要写文献综述", use_count=5)
    session.add_all([fewer, more])
    await session.flush()

    report = await consolidate(session, user_id=user_id)

    assert report.merged == 1
    assert more.use_count == 7, "重复项的用量要累加，不能随行一起丢掉"
    assert fewer.status == "merged", "被合并的标记而不是删除"
    assert fewer.structured.get("merged_into") == str(more.id), "要指回存活的那条"
    assert report.total == 2, "一条都不能少"


# ------------------------------------------------------------------------- decay --


async def test_a_recent_memory_does_not_decay() -> None:
    row = memory(uuid.uuid4(), "用户在做毕业设计", confidence=0.6)
    row.created_at = datetime.now(UTC) - timedelta(days=3)
    stale, _ = is_stale(row)
    assert not stale


async def test_a_confident_memory_does_not_decay_on_a_timer() -> None:
    """Being sure is not the same as being recent. A memory the user confirmed does not
    expire because nobody happened to mention it."""
    row = memory(uuid.uuid4(), "用户在做毕业设计", confidence=0.95)
    row.created_at = datetime.now(UTC) - timedelta(days=DECAY_AFTER_DAYS * 4)
    stale, _ = is_stale(row)
    assert not stale


async def test_an_old_low_confidence_memory_decays() -> None:
    row = memory(uuid.uuid4(), "用户提过一次想学吉他", confidence=0.5)
    row.created_at = datetime.now(UTC) - timedelta(days=DECAY_AFTER_DAYS + 10)
    stale, reason = is_stale(row)
    assert stale
    assert "天没被用过" in reason


async def test_decay_marks_rather_than_deletes(session: AsyncSession) -> None:
    """「退出检索但不删除」 — a decayed memory can come back when it is mentioned again,
    which a deleted one cannot."""
    user_id = await make_user(session)
    stale_row = memory(user_id, "用户提过一次想学吉他", confidence=0.5)
    stale_row.created_at = datetime.now(UTC) - timedelta(days=DECAY_AFTER_DAYS + 30)
    fresh_row = memory(user_id, "用户在做毕业设计", confidence=0.9)
    session.add_all([stale_row, fresh_row])
    await session.flush()

    report = await consolidate(session, user_id=user_id)

    assert report.decayed == 1
    assert stale_row.status == "decayed"
    assert fresh_row.status == "active"
    assert report.total == 2, "衰减不是删除"


# --------------------------------------------------------------------- conflicts --


async def test_detects_two_memories_that_disagree() -> None:
    user_id = uuid.uuid4()
    memories = [
        memory(user_id, "用户喜欢在汇报里用图表"),
        memory(user_id, "用户不喜欢在汇报里用图表"),
    ]
    conflicts = find_conflicts(memories)
    assert len(conflicts) == 1
    assert "否定" in conflicts[0].reason


async def test_paraphrases_are_not_conflicts() -> None:
    """Overlap alone is not a contradiction, or every paraphrase would be reported and
    the review queue would stop being read."""
    user_id = uuid.uuid4()
    memories = [
        memory(user_id, "用户在做毕业设计，需要写文献综述"),
        memory(user_id, "用户正在做毕业设计，要写文献综述"),
    ]
    assert find_conflicts(memories) == []


async def test_unrelated_memories_are_not_conflicts() -> None:
    user_id = uuid.uuid4()
    memories = [
        memory(user_id, "用户喜欢图表"),
        memory(user_id, "用户不吃香菜"),
    ]
    assert find_conflicts(memories) == []


async def test_conflicts_become_candidates_without_changing_either_row(
    session: AsyncSession,
) -> None:
    """The system proposes and the person decides. A system that silently picked a winner
    would be editing someone's memory of themselves on their behalf."""
    user_id = await make_user(session)
    left = memory(user_id, "用户喜欢在汇报里用图表")
    right = memory(user_id, "用户不喜欢在汇报里用图表")
    session.add_all([left, right])
    await session.flush()

    report = await consolidate(session, user_id=user_id)

    assert report.conflicts == 1
    assert report.candidates_created == 1

    pending = (
        await session.execute(
            select_candidate(user_id)
        )
    ).scalars().all()
    assert len(pending) == 1
    assert pending[0].proposed_kind == "conflict"

    # Neither row changed. This is the assertion that matters.
    assert left.status == "active"
    assert right.status == "active"
    assert left.content == "用户喜欢在汇报里用图表"
    assert right.content == "用户不喜欢在汇报里用图表"


def select_candidate(user_id: uuid.UUID):
    from sqlalchemy import select

    return select(MemoryCandidate).where(
        MemoryCandidate.user_id == user_id, MemoryCandidate.status == "pending"
    )


async def test_the_same_conflict_is_not_raised_twice(session: AsyncSession) -> None:
    """Running the pass twice must not queue the same question twice — a queue that
    duplicates itself is a queue nobody reads."""
    user_id = await make_user(session)
    session.add_all(
        [
            memory(user_id, "用户喜欢在汇报里用图表"),
            memory(user_id, "用户不喜欢在汇报里用图表"),
        ]
    )
    await session.flush()

    first = await consolidate(session, user_id=user_id)
    second = await consolidate(session, user_id=user_id)
    assert first.candidates_created == 1
    assert second.candidates_created == 0


# ------------------------------------------------------------------- dry run --


async def test_dry_run_reports_without_writing(session: AsyncSession) -> None:
    """Numbers can be inspected before anything changes — the same reason the assess
    stage exists for skills."""
    user_id = await make_user(session)
    left = memory(user_id, "用户在做毕业设计，需要写文献综述", use_count=1)
    right = memory(user_id, "用户正在做毕业设计，要写文献综述", use_count=1)
    session.add_all([left, right])
    await session.flush()

    report = await consolidate(session, user_id=user_id, dry_run=True)

    assert report.merged == 1, "dry run 也要报出会发生什么"
    assert left.status == "active", "dry run 不能改数据"
    assert right.status == "active"


# ------------------------------------------------------------------ forgetting --


async def test_purge_really_removes_the_memory(session: AsyncSession) -> None:
    """「可遗忘且真的遗忘」 is falsifiable, so it is falsified here. A delete that leaves
    the row behind is a delete in name only."""
    user_id = await make_user(session)
    row = memory(user_id, "用户提过一次想学吉他")
    session.add(row)
    await session.flush()
    memory_id = row.id

    assert not await is_forgotten(session, user_id=user_id, memory_id=memory_id)
    assert await purge(session, user_id=user_id, memory_id=memory_id)
    assert await is_forgotten(session, user_id=user_id, memory_id=memory_id), "删除后必须查不到"

    # And a second purge reports that there was nothing to remove, rather than claiming
    # success — the difference between "deleted" and "was not there" is worth keeping.
    assert not await purge(session, user_id=user_id, memory_id=memory_id)


async def test_purge_cannot_remove_another_users_memory(session: AsyncSession) -> None:
    owner = await make_user(session)
    other = await make_user(session)
    row = memory(owner, "用户的私人事实")
    session.add(row)
    await session.flush()

    assert not await purge(session, user_id=other, memory_id=row.id)
    assert not await is_forgotten(session, user_id=owner, memory_id=row.id)


# ------------------------------------------------------------------ the numbers --


async def test_stats_report_real_counts(session: AsyncSession) -> None:
    """The acceptance asks for real numbers, so they are computed and checked."""
    user_id = await make_user(session)
    session.add_all(
        [
            memory(user_id, "用户在做毕业设计", use_count=3),
            memory(user_id, "用户不吃香菜", use_count=0),
            memory(user_id, "用户偏好图表", use_count=1, status="decayed"),
        ]
    )
    session.add_all(
        [
            MemoryCandidate(user_id=user_id, proposed_kind="fact", content="a", status="approved"),
            MemoryCandidate(user_id=user_id, proposed_kind="fact", content="b", status="rejected"),
            MemoryCandidate(user_id=user_id, proposed_kind="fact", content="c", status="pending"),
        ]
    )
    await session.flush()

    stats = await memory_stats(session, user_id=user_id)
    assert stats["total"] == 3
    assert stats["active"] == 2
    assert stats["decayed"] == 1
    assert stats["average_use_count"] == pytest.approx(1.33, abs=0.01)
    assert stats["never_used"] == 1
    assert stats["candidates_pending"] == 1
    assert stats["rejected_ratio"] == 0.5, "拒绝占比要能证明确认机制在工作"


async def test_report_summarises_in_a_readable_line(session: AsyncSession) -> None:
    user_id = await make_user(session)
    session.add_all(
        [
            memory(user_id, "用户在做毕业设计，需要写文献综述", use_count=2),
            memory(user_id, "用户正在做毕业设计，要写文献综述", use_count=1),
        ]
    )
    await session.flush()

    report = await consolidate(session, user_id=user_id)
    line = report.summarise()
    assert "合并 1 条" in line
    assert "平均被用过" in line


async def test_an_empty_library_reports_cleanly(session: AsyncSession) -> None:
    """A first-run account has nothing, and the report has to say so rather than crash or
    produce a misleading zero."""
    user_id = await make_user(session)
    report = await consolidate(session, user_id=user_id)
    assert report.total == 0
    assert report.merged == 0
    assert "空" in report.summarise()

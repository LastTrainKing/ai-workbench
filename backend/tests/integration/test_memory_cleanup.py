"""The one-time cleanup: it shows the plan, it changes nothing until told, and it is honest.

The library this was written for, measured before anything was changed:

    13 条 active
      ├─ 7 条  「这是一条浏览器测试写入的记忆」        测试垃圾
      ├─ 4 条  「用户偏好图表充足、页数精简…」         同一件事的四种说法
      └─ 2 条  封面信息 / 报告完整性                  真正不同的两件事

Three things are asserted here that a one-sided test would miss:

* **the plan writes nothing** — otherwise "先给用户看、再删" is decoration;
* **the two genuinely different memories survive** — a cleanup that merged everything
  would report the same counts as one that merged correctly;
* **`superseded` is not `rejected`** — folding a duplicate candidate into a fuller one
  is not the user saying no, and recording it as a refusal would silence the fact for
  good through the "you already refused this" rule.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.ai.memory.cleanup import apply_cleanup, plan_cleanup
from app.core.security import hash_password
from app.infrastructure.database import session_scope
from app.models.context import MemoryCandidate, MemoryEntry
from app.models.user import User


class ScriptedPlanner:
    """A planner whose grouping the test dictates."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    async def chat(self, messages, **kwargs):
        from app.ai.types import ChatResult

        self.prompts.append("\n".join(str(getattr(m, "content", "")) for m in messages))
        return ChatResult(content=json.dumps(self.payload, ensure_ascii=False))


async def seed(
    *,
    memories: list[tuple[str, int]] = (),
    candidates: list[str] = (),
) -> uuid.UUID:
    async with session_scope() as session:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            email=f"clean_{suffix}@aiworkbench.dev",
            username=f"clean_{suffix}",
            display_name="Cleanup Test",
            hashed_password=hash_password("test1234"),
        )
        session.add(user)
        await session.flush()
        for content, use_count in memories:
            session.add(
                MemoryEntry(
                    user_id=user.id,
                    kind="preference",
                    content=content,
                    confidence=0.9,
                    status="active",
                    use_count=use_count,
                )
            )
        for content in candidates:
            session.add(
                MemoryCandidate(
                    user_id=user.id,
                    proposed_kind="preference",
                    content=content,
                    confidence=0.6,
                    status="pending",
                )
            )
        await session.flush()
        return user.id


async def active(user_id: uuid.UUID) -> list[MemoryEntry]:
    async with session_scope() as session:
        return list(
            (
                await session.execute(
                    select(MemoryEntry)
                    .where(MemoryEntry.user_id == user_id, MemoryEntry.status == "active")
                    .order_by(MemoryEntry.use_count.desc())
                )
            ).scalars()
        )


async def by_status(user_id: uuid.UUID, status: str) -> list[MemoryCandidate]:
    async with session_scope() as session:
        return list(
            (
                await session.execute(
                    select(MemoryCandidate).where(
                        MemoryCandidate.user_id == user_id, MemoryCandidate.status == status
                    )
                )
            ).scalars()
        )


# --------------------------------------------------------------------------- #
# 1. The plan is a plan
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_plan_changes_nothing():
    user_id = await seed(
        memories=[("用户偏好图表充足", 10), ("用户偏好图表更多、页数更少", 5), ("用户不吃香菜", 2)]
    )
    planner = ScriptedPlanner(
        {"duplicates": [{"keep": 1, "merge": [2], "why": "同一件事"}], "litter": []}
    )

    async with session_scope() as session:
        plan = await plan_cleanup(session, user_id=user_id, llm=planner)

    assert plan.total_before == 3
    assert plan.total_after == 2
    assert len(plan.duplicates) == 1
    assert plan.duplicates[0].merged_use_count == 15, "保留的 10 次 + 并入的 5 次"

    rows = await active(user_id)
    assert len(rows) == 3, "看计划的时候不能动任何东西"


# --------------------------------------------------------------------------- #
# 2. Applying it
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_apply_merges_and_sums_the_usage():
    user_id = await seed(
        memories=[("用户偏好图表充足", 10), ("用户偏好图表更多、页数更少", 5), ("用户不吃香菜", 2)]
    )
    planner = ScriptedPlanner(
        {"duplicates": [{"keep": 1, "merge": [2], "why": "同一件事"}], "litter": []}
    )
    async with session_scope() as session:
        plan = await plan_cleanup(session, user_id=user_id, llm=planner)
    async with session_scope() as session:
        result = await apply_cleanup(session, user_id=user_id, plan=plan)

    assert result.merged == 1
    rows = await active(user_id)
    assert len(rows) == 2
    survivor = next(r for r in rows if r.content == "用户偏好图表充足")
    assert survivor.use_count == 15, f"用量要累加，实际 {survivor.use_count}"

    # The merged row is *marked*, not deleted — the fact that the user phrased it two
    # ways is itself information.
    async with session_scope() as session:
        merged_rows = list(
            (
                await session.execute(
                    select(MemoryEntry).where(
                        MemoryEntry.user_id == user_id, MemoryEntry.status == "merged"
                    )
                )
            ).scalars()
        )
    assert len(merged_rows) == 1
    assert merged_rows[0].structured.get("merged_into") == str(survivor.id)


@pytest.mark.asyncio
async def test_apply_deletes_the_litter():
    user_id = await seed(
        memories=[("这是一条浏览器测试写入的记忆", 0), ("用户不吃香菜", 3)]
    )
    planner = ScriptedPlanner(
        # Index 2, not 1: the plan lists memories by use count descending, so the row
        # with 3 uses is first and the litter is second. Getting this wrong is what the
        # assertion below would have caught — it deleted the wrong row and the test said
        # so, which is the point of asserting on content rather than on a count.
        {"duplicates": [], "litter": [{"index": 2, "why": "浏览器测试写入的调试文本"}]}
    )
    async with session_scope() as session:
        plan = await plan_cleanup(session, user_id=user_id, llm=planner)
    assert len(plan.litter) == 1

    async with session_scope() as session:
        result = await apply_cleanup(session, user_id=user_id, plan=plan)

    assert result.deleted == 1
    rows = await active(user_id)
    assert [r.content for r in rows] == ["用户不吃香菜"], "垃圾删掉，真的记忆留下"


@pytest.mark.asyncio
async def test_a_real_memory_is_never_merged_away():
    """The other side. A cleanup that folds everything looks identical on the counts."""
    user_id = await seed(
        memories=[
            ("用户偏好图表充足、页数精简", 100),
            ("用户在验收演示文稿时要求封面包含答辩人和指导教师信息", 50),
            ("用户在验收报告类交付物时要求内容完整", 40),
        ]
    )
    # The planner proposes nothing — which is what a working one does for these three.
    planner = ScriptedPlanner({"duplicates": [], "litter": []})
    async with session_scope() as session:
        plan = await plan_cleanup(session, user_id=user_id, llm=planner)
        await apply_cleanup(session, user_id=user_id, plan=plan)

    rows = await active(user_id)
    assert len(rows) == 3, "三条不同的事，一条都不能少"


# --------------------------------------------------------------------------- #
# 3. A hallucinated index is not trusted
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_out_of_range_index_is_ignored():
    user_id = await seed(memories=[("用户偏好图表充足", 10), ("用户不吃香菜", 2)])
    planner = ScriptedPlanner(
        {"duplicates": [{"keep": 99, "merge": [2], "why": "x"}], "litter": [{"index": 0, "why": "y"}]}
    )
    async with session_scope() as session:
        plan = await plan_cleanup(session, user_id=user_id, llm=planner)

    assert plan.duplicates == [], "越界的编号不该合并任何东西"
    assert plan.litter == []
    assert plan.rows_removed_from_retrieval == 0


@pytest.mark.asyncio
async def test_a_group_that_names_itself_as_the_duplicate_is_ignored():
    user_id = await seed(memories=[("用户偏好图表充足", 10), ("用户不吃香菜", 2)])
    planner = ScriptedPlanner({"duplicates": [{"keep": 1, "merge": [1], "why": "x"}], "litter": []})
    async with session_scope() as session:
        plan = await plan_cleanup(session, user_id=user_id, llm=planner)
    assert plan.duplicates == [], "保留项不能同时是并入项"


# --------------------------------------------------------------------------- #
# 4. Candidates are folded, and folding is not a refusal
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_duplicate_candidates_become_one_and_superseded_is_not_rejected():
    user_id = await seed(
        candidates=[
            "用户以「图表充足、页数精简」作为验收标准",
            "用户在验收演示文稿时会因图表太少、页数偏多而退回",
            "用户偏好图表充足、页数精简的演示文稿",
            "用户以后所有项目都用 Vue",
        ]
    )
    planner = ScriptedPlanner(
        {
            "duplicates": [],
            "litter": [],
            "candidate_duplicates": [{"keep": 3, "merge": [1, 2], "why": "同一件事"}],
        }
    )
    async with session_scope() as session:
        plan = await plan_cleanup(session, user_id=user_id, llm=planner)

    assert plan.candidates_before == 4
    assert plan.candidates_after == 2, "四条待确认，说的是同一件事的三条合成一条"
    assert plan.candidates_folded == 2

    async with session_scope() as session:
        result = await apply_cleanup(session, user_id=user_id, plan=plan)

    assert result.candidates_folded == 2
    assert len(await by_status(user_id, "pending")) == 2
    superseded = await by_status(user_id, "superseded")
    assert len(superseded) == 2

    # The distinction that matters. `rejected` feeds the "you already refused this"
    # rule in the evaluator; `superseded` must not, or folding a duplicate would
    # silence the fact permanently — a decision the user never made.
    assert await by_status(user_id, "rejected") == []
    for row in superseded:
        assert row.structured.get("superseded_by")
        assert row.structured.get("superseded_reason")


@pytest.mark.asyncio
async def test_the_evidence_count_survives_the_fold():
    """「这条我提过 N 次」 must still be true after the rows are folded together."""
    user_id = await seed(candidates=["用户偏好图表充足", "用户偏好图表更多"])
    async with session_scope() as session:
        keeper = (
            await session.execute(
                select(MemoryCandidate).where(MemoryCandidate.user_id == user_id)
            )
        ).scalars().first()
        assert keeper is not None
        keeper.structured = {"evidence_count": 5}
        await session.flush()

    planner = ScriptedPlanner(
        {"duplicates": [], "litter": [], "candidate_duplicates": [{"keep": 1, "merge": [2], "why": "x"}]}
    )
    async with session_scope() as session:
        plan = await plan_cleanup(session, user_id=user_id, llm=planner)
    async with session_scope() as session:
        await apply_cleanup(session, user_id=user_id, plan=plan)

    pending = await by_status(user_id, "pending")
    assert len(pending) == 1
    assert pending[0].structured.get("evidence_count") == 5


# --------------------------------------------------------------------------- #
# 5. The plan prompt gets both lists
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_planner_sees_memories_and_candidates():
    user_id = await seed(memories=[("用户偏好图表充足", 3)], candidates=["用户以后所有项目都用 Vue"])
    planner = ScriptedPlanner({"duplicates": [], "litter": [], "candidate_duplicates": []})
    async with session_scope() as session:
        await plan_cleanup(session, user_id=user_id, llm=planner)

    prompt = planner.prompts[0]
    assert "用户偏好图表充足" in prompt
    assert "用户以后所有项目都用 Vue" in prompt
    assert "candidate_duplicates" in prompt

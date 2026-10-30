"""The candidate layer deduplicates, or the user becomes the quality inspector.

The user's words: 「这种东西怎么能让用户自己调试呢，自己验证自己修正啊，自己修改可控的
记忆，现在这是啥逻辑啊」. What they were looking at: four pending candidates saying the
same thing, and twelve active memories of which six were test litter and five were one
preference phrased five ways.

The mechanism, read out of the code before anything was changed: `evaluate()` compared a
new statement only against `memory_entries` where `status = 'active'`. A restatement of
something already *pending* was invisible to it, and so was something already *rejected*.
Every rejection the user made therefore added another row — so the same fact queued up
four times and the user was asked to press 确认 four times for one decision.

Three lists now go to the judge, and its answer decides the route:

    active   →  bump the memory that is already there; do not ask again
    pending  →  bump the candidate that is already waiting; do not queue a second copy
    rejected →  do not propose it at all; a refusal is a decision, not a vote

Why the judge and not a threshold: `scripts/sample_memory_similarity.py` measures it.
「用户喜欢用 Python 写脚本」 vs 「用户喜欢用 Java 写脚本」 scores 0.778 / 0.875 —
higher than *every* genuine rephrasing in the sample. No lexical threshold separates
"same fact, different words" from "one word swapped"; the model can, and it is already
being called once per statement, so asking it costs nothing extra.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.ai.memory import MemoryEvaluator
from app.ai.memory.evaluator import Routing
from app.ai.memory.similarity import same_thing
from app.core.security import hash_password
from app.infrastructure.database import session_scope
from app.models.context import MemoryCandidate, MemoryEntry
from app.models.user import User


class ScriptedJudge:
    """A judge whose answers the test dictates, one call at a time."""

    def __init__(self, *answers: dict) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def chat(self, messages, **kwargs):
        from app.ai.types import ChatResult

        self.prompts.append("\n".join(str(getattr(m, "content", "")) for m in messages))
        answer = self.answers[min(len(self.prompts) - 1, len(self.answers) - 1)]
        return ChatResult(content=json.dumps(answer, ensure_ascii=False))


def verdict(
    *,
    content: str,
    confidence: float = 0.6,
    duplicates: str = "",
    contradicts: int = -1,
    kind: str = "preference",
) -> dict:
    return {
        "worth_remembering": True,
        "reason": "长期偏好",
        "kind": kind,
        "scope": "global",
        "confidence": confidence,
        "content": content,
        "contradicts": contradicts,
        "duplicates": duplicates,
    }


async def make_user() -> uuid.UUID:
    async with session_scope() as session:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            email=f"dedup_{suffix}@aiworkbench.dev",
            username=f"dedup_{suffix}",
            display_name="Dedup Test",
            hashed_password=hash_password("test1234"),
        )
        session.add(user)
        await session.flush()
        return user.id


async def candidates(user_id: uuid.UUID, status: str = "pending") -> list[MemoryCandidate]:
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


async def entries(user_id: uuid.UUID) -> list[MemoryEntry]:
    async with session_scope() as session:
        return list(
            (
                await session.execute(
                    select(MemoryEntry).where(
                        MemoryEntry.user_id == user_id, MemoryEntry.status == "active"
                    )
                )
            ).scalars()
        )


async def evaluate(user_id: uuid.UUID, llm, statement: str, context: str = "") -> Routing:
    async with session_scope() as session:
        evaluator = MemoryEvaluator(session, llm, user_id=user_id)
        result = await evaluator.evaluate(statement, context=context)
    return result.routing


# --------------------------------------------------------------------------- #
# 1. Three rejections of the same thing produce one candidate, not three
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_same_fact_stated_three_times_queues_once():
    """The acceptance, stated as a number: 3 in, 1 row out.

    The judge is scripted the way a working one behaves — it recognises the second and
    third statements as the same fact it has already proposed — and the assertion is
    about what lands in the table, not about what the evaluator returned.
    """
    user_id = await make_user()
    statement = "用户偏好图表充足、页数精简的演示文稿"
    llm = ScriptedJudge(
        verdict(content=statement),
        verdict(content=statement, duplicates="pending:1"),
        verdict(content=statement, duplicates="pending:1"),
    )

    # The first one is new, so it becomes a candidate. The next two are the same fact
    # again, and they must not become rows — that is the whole point.
    assert await evaluate(user_id, llm, statement) is Routing.CANDIDATE
    assert await evaluate(user_id, llm, statement) is Routing.DUPLICATE
    assert await evaluate(user_id, llm, statement) is Routing.DUPLICATE

    rows = await candidates(user_id)
    assert len(rows) == 1, f"同样的事提了三次，应该有 1 条候选，实际 {len(rows)} 条"
    # And the count is on the row, so the UI can say 「这条我提过 3 次」 rather than
    # showing the user three rows and asking them to press 确认 three times.
    assert rows[0].structured.get("evidence_count") == 3, rows[0].structured


# --------------------------------------------------------------------------- #
# 2. A rejected candidate is never proposed again
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_something_the_user_refused_is_not_offered_again():
    """A refusal is a decision. Asking twice is the system not listening."""
    user_id = await make_user()
    statement = "用户偏好图表充足、页数精简的演示文稿"

    async with session_scope() as session:
        session.add(
            MemoryCandidate(
                user_id=user_id,
                proposed_kind="preference",
                content=statement,
                reason="之前提过",
                confidence=0.6,
                status="rejected",
            )
        )
        await session.flush()

    llm = ScriptedJudge(verdict(content=statement, duplicates="rejected:1"))
    routing = await evaluate(user_id, llm, statement)

    assert routing is Routing.DUPLICATE
    assert await candidates(user_id, "pending") == [], "被拒绝过的信息不该再出现在待确认里"
    assert await entries(user_id) == [], "也不该绕过用户直接生效"


# --------------------------------------------------------------------------- #
# 3. A restatement of an active memory bumps it instead of asking again
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_restatement_of_a_live_memory_bumps_it_and_asks_nothing():
    user_id = await make_user()
    async with session_scope() as session:
        session.add(
            MemoryEntry(
                user_id=user_id,
                kind="preference",
                content="用户偏好图表充足的演示文稿",
                confidence=0.9,
                status="active",
                use_count=4,
            )
        )
        await session.flush()

    llm = ScriptedJudge(verdict(content="用户偏好图表充足的演示文稿", duplicates="active:1"))
    routing = await evaluate(user_id, llm, "做 PPT 多放点图表")

    assert routing is Routing.DUPLICATE
    rows = await entries(user_id)
    assert len(rows) == 1, "不该新建第二条"
    assert rows[0].use_count == 5, f"使用次数应该累加，实际 {rows[0].use_count}"
    assert await candidates(user_id) == [], "已经在库里的东西不该再问一次"


# --------------------------------------------------------------------------- #
# 4. The index is validated, not trusted
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_out_of_range_duplicate_index_is_ignored():
    """A hallucinated index must not bump an arbitrary row.

    `pending:9` with one candidate waiting resolves to "no duplicate" and the statement
    becomes a candidate — which is the safe direction, because it means the user decides.
    Silently clamping to the last row would edit a memory nobody asked it to touch.
    """
    user_id = await make_user()
    async with session_scope() as session:
        session.add(
            MemoryCandidate(
                user_id=user_id,
                proposed_kind="preference",
                content="用户偏好图表充足",
                confidence=0.6,
                status="pending",
            )
        )
        await session.flush()

    llm = ScriptedJudge(verdict(content="用户喜欢用深色主题", duplicates="pending:9"))
    routing = await evaluate(user_id, llm, "用户喜欢用深色主题")

    assert routing is Routing.CANDIDATE
    rows = await candidates(user_id)
    assert len(rows) == 2, "越界的编号不该被当成命中"
    untouched = next(r for r in rows if r.content == "用户偏好图表充足")
    assert untouched.structured.get("evidence_count") is None, "别的候选不该被改"


@pytest.mark.asyncio
async def test_an_unparseable_duplicate_answer_is_ignored():
    user_id = await make_user()
    llm = ScriptedJudge(verdict(content="用户喜欢用深色主题", duplicates="大概和第三条有点像"))
    assert await evaluate(user_id, llm, "用户喜欢用深色主题") is Routing.CANDIDATE
    assert len(await candidates(user_id)) == 1


# --------------------------------------------------------------------------- #
# 5. The near-copy guard, and the case it must refuse
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_rewritten_near_copy_is_caught_without_the_judge_naming_it():
    """The judge is shown the raw statement and may rewrite it into an existing row.

    It is not asked about the rewrite, so a cheap lexical backstop runs on the result.
    """
    user_id = await make_user()
    async with session_scope() as session:
        session.add(
            MemoryEntry(
                user_id=user_id,
                kind="preference",
                content="用户在做毕业设计，需要写文献综述",
                confidence=0.9,
                status="active",
                use_count=2,
            )
        )
        await session.flush()

    # The judge says "nothing duplicates this" — and then rewrites the statement into
    # something that nearly is an existing row.
    llm = ScriptedJudge(verdict(content="用户正在做毕业设计，要写文献综述", duplicates=""))
    routing = await evaluate(user_id, llm, "我在写毕业设计的文献综述")

    assert routing is Routing.DUPLICATE
    rows = await entries(user_id)
    assert len(rows) == 1 and rows[0].use_count == 3


def test_swapping_a_named_thing_is_not_a_near_copy():
    """The one case no threshold can get right, pinned as a unit test.

    Measured: this pair scores 0.778 by Jaccard and 0.875 by containment — above every
    genuine rephrasing in the sample. `same_thing` refuses it because the two statements
    do not name the same things, which is the only signal that separates them.
    """
    assert (
        same_thing("用户在做毕业设计，需要写文献综述", "用户正在做毕业设计，要写文献综述") is True
    )
    assert same_thing("用户喜欢用 Python 写脚本。", "用户喜欢用 Java 写脚本。") is False
    assert (
        same_thing(
            "用户以后所有项目都使用 Vue，不再使用 React。",
            "用户以后所有项目都用 Vue，不用 React 了。",
        )
        is True
    )


@pytest.mark.asyncio
async def test_the_judge_is_given_all_three_lists():
    """Not an implementation detail: if the rejected list is not sent, the model cannot
    know the user already said no, and the whole first half of this file is unreachable."""
    user_id = await make_user()
    async with session_scope() as session:
        session.add_all(
            [
                MemoryEntry(
                    user_id=user_id,
                    kind="fact",
                    content="用户在读计算机专业",
                    confidence=0.9,
                    status="active",
                ),
                MemoryCandidate(
                    user_id=user_id,
                    proposed_kind="preference",
                    content="用户偏好图表充足",
                    confidence=0.6,
                    status="pending",
                ),
                MemoryCandidate(
                    user_id=user_id,
                    proposed_kind="preference",
                    content="用户以后所有项目都用 Vue",
                    confidence=0.6,
                    status="rejected",
                ),
            ]
        )
        await session.flush()

    llm = ScriptedJudge(verdict(content="用户喜欢用深色主题"))
    await evaluate(user_id, llm, "用户喜欢用深色主题")

    prompt = llm.prompts[0]
    assert "active:1" in prompt and "用户在读计算机专业" in prompt
    assert "pending:1" in prompt and "用户偏好图表充足" in prompt
    assert "rejected:1" in prompt and "用户以后所有项目都用 Vue" in prompt

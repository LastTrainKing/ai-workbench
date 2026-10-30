"""Recall relevance: a memory is injected only if it bears on *this* request.

The reported failure, in the user's words:

    这次任务是「写个 300 字雨后小故事，做成 Word」。
    而它塞进上下文的三条记忆是：讲 PPT 页数的、讲报告结构的、讲答辩封面的。

    **收起来也还是错的：错的是召回，不是展示。**

`ContextAssembler._memory_block` ranked the user's memories by confidence and recency and
never looked at the request, so every run got the same handful. These tests pin the three
properties that fix it:

* a request that has nothing to do with the stored memories gets **zero** of them,
* a request that is about one of them gets that one,
* the rule is the measured one from `scripts/sample_memory_relevance.py`, not an overlap
  count — 「内容」 appearing in two memories must not count as evidence for either.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.memory.relevance import RARITY_FLOOR, rank, rarity, score, terms

#: The three memories this user actually has, verbatim. The whole point of the sample is
#: that it is the real text: a synthetic corpus would not have 「验收」 in two of them.
MEMORIES = [
    "用户在验收报告类交付物时要求内容完整：报告至少应包含背景和结论两部分，内容过简会被退回。",
    "用户偏好图表充足、文字精简、页数少的演示文稿，PPT 类交付物页数应控制在 12 页以内，"
    "图表太少、文字过密或页数偏多会被退回。",
    "用户在验收演示文稿时要求封面包含答辩人和指导教师信息，且正文字号需足够大以确保投影时后排可读。",
]


class _Memory:
    def __init__(self, content: str) -> None:
        self.content = content


def _rank(question: str) -> list[str]:
    return [m.content for _, m in rank(question, [_Memory(text) for text in MEMORIES])]


def test_the_reported_request_recalls_nothing() -> None:
    """The exact sentence from the report. This is the acceptance criterion."""
    assert _rank("写个 300 字雨后小故事，做成 Word") == []


@pytest.mark.parametrize(
    "question",
    [
        "解释一下 TCP 三次握手",
        "帮我写个 Python 快速排序",
        "今天北京天气怎么样",
        "推荐三本讲经济学的书",
        "把这段英文翻译成中文",
        "帮我起个网名",
    ],
)
def test_unrelated_requests_recall_nothing(question: str) -> None:
    """「不相关的宁可召回 0 条，不要凑数」 — a function that always returns something is
    not filtering."""
    assert _rank(question) == []


def test_a_request_about_one_memory_recalls_that_one() -> None:
    recalled = _rank("帮我写一份项目验收报告")
    assert recalled, "a report request must recall the report memory"
    assert recalled[0] == MEMORIES[0]


def test_a_request_about_the_slides_memory_recalls_it() -> None:
    recalled = _rank("PPT 页数控制在多少比较合适")
    assert recalled and recalled[0] == MEMORIES[1]


def test_a_term_shared_by_half_the_corpus_is_not_evidence() -> None:
    """The rule the overlap count got wrong.

    「验收」 appears in two of the three memories, so sharing it says nothing about
    *which* memory is about the request. Under the old `overlap >= 1` rule a single such
    term was enough on its own.
    """
    from app.ai.memory.relevance import document_frequencies

    df = document_frequencies([terms(text) for text in MEMORIES])
    assert df["验收"] == 2
    assert rarity(df["验收"], len(MEMORIES)) < RARITY_FLOOR
    assert _rank("验收") == []


def test_the_old_overlap_rule_would_have_recalled_that_term() -> None:
    """A negative control, so the test above is not passing by accident.

    It proves the new rule is doing work: the same input fires the old rule and does not
    fire the new one. Without this, `_rank(...) == []` could be true for any number of
    unrelated reasons.
    """
    query = terms("验收")
    old_rule = [text for text in MEMORIES if query & terms(text)]
    assert old_rule, "overlap >= 1 must fire here, or this control proves nothing"
    assert _rank("验收") == []


def test_rarity_is_one_for_a_unique_term_and_low_for_a_shared_one() -> None:
    corpus = [terms(text) for text in MEMORIES]
    from app.ai.memory.relevance import document_frequencies

    df = document_frequencies(corpus)
    assert df["答辩"] == 1
    assert rarity(df["答辩"], len(MEMORIES)) == pytest.approx(1.0)
    # 「验收」 is in two of three.
    assert df["验收"] == 2
    assert rarity(df["验收"], len(MEMORIES)) < RARITY_FLOOR


def test_rarity_does_not_call_everything_worthless_in_a_one_memory_corpus() -> None:
    """`corpus_size + 1` in the denominator is what stops this."""
    assert rarity(1, 1) == pytest.approx(1.0)


def test_score_is_zero_when_nothing_is_shared() -> None:
    from app.ai.memory.relevance import document_frequencies

    df = document_frequencies([terms(text) for text in MEMORIES])
    assert score("今天天气怎么样", MEMORIES[0], df=df, corpus_size=len(MEMORIES)) == 0.0


def test_rank_is_empty_for_an_empty_corpus() -> None:
    assert rank("随便什么", []) == []


def test_rank_respects_the_limit() -> None:
    """And returns the best first, so `[:limit]` is a top-N and not an arbitrary N."""
    recalled = rank(
        "做一份答辩 PPT，页数别太多，封面要写清楚", [_Memory(t) for t in MEMORIES], limit=1
    )
    assert len(recalled) == 1


def test_the_floor_is_the_measured_one() -> None:
    """A constant nobody can check is a constant that drifts.

    Re-measure with `scripts/sample_memory_relevance.py` if this fails; the sample and
    the distributions are printed by that script.
    """
    assert RARITY_FLOOR == 0.6


@pytest.mark.asyncio
async def test_the_assembler_injects_nothing_without_a_goal(
    session: Any, user_token: tuple[str, dict]
) -> None:
    """A caller that cannot say what the memories are for gets none of them.

    Returning the top-ranked memories here is precisely the bug: every run was given the
    same handful regardless of what it was about.
    """
    import uuid

    from app.ai.memory import ContextAssembler
    from app.models.context import MemoryEntry

    _, user = user_token
    user_id = uuid.UUID(str(user["id"]))
    for text in MEMORIES:
        session.add(MemoryEntry(user_id=user_id, kind="preference", content=text, status="active"))
    await session.flush()

    assembled = await ContextAssembler(session, user_id=user_id).assemble(goal="")
    assert all(block.kind != "memory" for block in assembled.blocks)


@pytest.mark.asyncio
async def test_the_assembler_injects_only_what_is_relevant(
    session: Any, user_token: tuple[str, dict]
) -> None:
    """The end-to-end shape of the fix, on the reported request."""
    import uuid

    from app.ai.memory import ContextAssembler
    from app.models.context import MemoryEntry

    _, user = user_token
    user_id = uuid.UUID(str(user["id"]))
    for text in MEMORIES:
        session.add(MemoryEntry(user_id=user_id, kind="preference", content=text, status="active"))
    await session.flush()

    unrelated = await ContextAssembler(session, user_id=user_id).assemble(
        goal="写个 300 字雨后小故事，做成 Word"
    )
    assert all(block.kind != "memory" for block in unrelated.blocks), (
        "a request about a short story must not be given memories about slide decks"
    )
    assert "答辩" not in unrelated.render()

    related = await ContextAssembler(session, user_id=user_id).assemble(
        goal="帮我写一份项目验收报告"
    )
    memory_block = next(block for block in related.blocks if block.kind == "memory")
    assert MEMORIES[0] in memory_block.text
    assert "答辩" not in memory_block.text

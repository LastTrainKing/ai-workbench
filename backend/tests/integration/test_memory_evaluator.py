"""A judge that could not run must not be indistinguishable from a judge that said no.

The bug this pins down ran for a whole round without being noticed. The memory
evaluator's judgement call asked for 500 completion tokens; the default provider is a
reasoning model, whose thinking is covered by the same budget, so the call raised
`ai_reasoning_budget_exhausted` — no answer at all. The exception was caught and turned
into a verdict:

    return Verdict(Routing.DROP, f"判定失败：{type(exc).__name__}", content=text)

`DROP` means "not worth remembering". So every delivery rejection was recorded as the
filter working, and the learning loop was not degraded — it was **off**. The only trace
was a log line.

This is the fifth instance of the same shape in this project, and the reason the fix is
in two parts: give the call a budget that works, *and* stop representing a failure as a
decision.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.ai.memory import MemoryEvaluator
from app.ai.memory.evaluator import Routing
from app.ai.providers.budget import REASONING_TOKENS, reasoning_budget
from app.core.errors import AIError
from app.infrastructure.database import session_scope
from app.models.user import User


class _RaisingLLM:
    """A provider that reports the reasoning budget as exhausted."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        raise AIError(
            "模型在思考阶段用尽了 token 预算，没有产出最终回答。",
            code="ai_reasoning_budget_exhausted",
        )


class _GarbledLLM:
    async def chat(self, messages, **kwargs):
        from app.ai.types import ChatResult

        return ChatResult(content="这不是 JSON，只是模型在解释它有多想帮忙。")


class _OkLLM:
    """A provider that answers properly, and reports what budget it was given."""

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.budgets: list[int] = []

    async def chat(self, messages, **kwargs):
        from app.ai.types import ChatResult

        self.budgets.append(kwargs.get("max_tokens", 0))
        return ChatResult(content=self.payload)


async def _user_id() -> uuid.UUID:
    async with session_scope() as session:
        existing = (await session.execute(select(User).limit(1))).scalar_one_or_none()
        if existing is not None:
            return existing.id
        from app.core.security import hash_password

        suffix = uuid.uuid4().hex[:8]
        user = User(
            email=f"mem_{suffix}@aiworkbench.dev",
            username=f"mem_{suffix}",
            display_name="Memory Test",
            hashed_password=hash_password("test1234"),
        )
        session.add(user)
        await session.flush()
        return user.id


@pytest.mark.asyncio
async def test_a_judge_failure_propagates_instead_of_becoming_a_verdict():
    """The regression. A failure must not be representable as 'not worth remembering'."""
    user_id = await _user_id()
    async with session_scope() as session:
        evaluator = MemoryEvaluator(session, _RaisingLLM(), user_id=user_id)
        with pytest.raises(AIError) as caught:
            await evaluator.evaluate("用户退回了《答辩 PPT》，理由是：图表太少。")
        assert caught.value.code == "ai_reasoning_budget_exhausted"


@pytest.mark.asyncio
async def test_an_unparseable_judgement_also_propagates():
    user_id = await _user_id()
    async with session_scope() as session:
        evaluator = MemoryEvaluator(session, _GarbledLLM(), user_id=user_id)
        with pytest.raises(ValueError, match="不是合法 JSON"):
            await evaluator.evaluate("用户退回了《答辩 PPT》，理由是：图表太少。")


@pytest.mark.asyncio
async def test_the_judge_gets_a_budget_a_reasoning_model_can_think_in():
    """500 tokens is what broke it; the assertion is that the cap is not small."""
    user_id = await _user_id()
    llm = _OkLLM(
        '{"worth_remembering": false, "reason": "一次性事务", "kind": "fact",'
        ' "scope": "global", "confidence": 0.1, "content": "", "contradicts": -1}'
    )
    async with session_scope() as session:
        evaluator = MemoryEvaluator(session, llm, user_id=user_id)
        verdict = await evaluator.evaluate("用户退回了《答辩 PPT》，理由是：图表太少。")

    assert verdict.routing is Routing.DROP
    assert llm.budgets, "the judge was never called"
    assert llm.budgets[0] == reasoning_budget()
    assert llm.budgets[0] >= REASONING_TOKENS or llm.budgets[0] >= 2000


@pytest.mark.asyncio
async def test_a_high_confidence_fact_lands_and_carries_its_origin():
    user_id = await _user_id()
    llm = _OkLLM(
        '{"worth_remembering": true, "reason": "长期标准", "kind": "preference",'
        ' "scope": "global", "confidence": 0.95,'
        ' "content": "[evaluator-test] 用户要求交付物必须附图表。", "contradicts": -1}'
    )
    origin = {
        "origin": {
            "kind": "delivery",
            "decision": "rejected",
            "deliverable_title": "毕业设计答辩",
            "version": 1,
            "build_run_id": "00000000-0000-0000-0000-000000000000",
        }
    }
    async with session_scope() as session:
        evaluator = MemoryEvaluator(session, llm, user_id=user_id)
        verdict = await evaluator.evaluate(
            "用户退回了《答辩 PPT》，理由是：图表太少。", source="delivery", structured=origin
        )
        assert verdict.routing is Routing.ACTIVE

    async with session_scope() as session:
        from app.models.context import MemoryEntry

        row = (
            await session.execute(
                select(MemoryEntry).where(
                    MemoryEntry.user_id == user_id,
                    MemoryEntry.content.like("[evaluator-test]%"),
                )
            )
        ).scalar_one()
        assert row.structured == origin
        assert row.source == "delivery"

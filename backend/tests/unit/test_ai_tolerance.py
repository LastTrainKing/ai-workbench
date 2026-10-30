"""Regression tests for the tolerance the AI layer needs against real models.

Every case here was found by running against the real DeepSeek endpoint, where
the model:
  * omits ``completion_criteria`` on some tasks even though the prompt requires it;
  * can be cut off by the token ceiling mid-JSON;
  * requires the literal word "json" before accepting ``response_format``.

None of these are hypothetical. Each test is the guard for one of them.
"""

from __future__ import annotations

import pytest

from app.ai.rag.pipeline import _repair_truncated_json, load_json_object
from app.core.errors import AIResponseFormatError
from app.services.ai_services import PlannerService, _derive_criterion


# --------------------------------------------------------------------------- #
# JSON tolerance
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("plain", '{"a": 1}'),
        ("fenced", '```json\n{"a": 1}\n```'),
        ("fenced_no_lang", '```\n{"a": 1}\n```'),
        ("prose_wrapped", '好的，结果是：{"a": 1} 希望有帮助'),
        ("leading_whitespace", '   \n  {"a": 1}'),
    ],
)
def test_valid_json_is_extracted(name: str, raw: str) -> None:
    assert load_json_object(raw) == {"a": 1}, name


@pytest.mark.parametrize(
    "raw",
    [
        '{"goal":"x","phases":[{"name":"阶段1","tasks":[{"title":"A","estimated_minutes":90},{"title":"练习',
        '{"goal":"x","phases":[{"name":"阶段1","tasks":[{"title":"A","estimated_minutes":90}',
        '{"goal":"x","phases":[{"name":"阶段1","tasks":[{"title":',
        '{"goal":"x","phases":[',
    ],
)
def test_truncated_json_is_repaired(raw: str) -> None:
    """A plan cut off mid-object must still yield its complete part."""
    payload = load_json_object(raw)
    assert isinstance(payload, dict)
    assert payload.get("goal") == "x"


def test_repair_preserves_complete_entries() -> None:
    raw = '{"phases":[{"name":"P1","tasks":[{"title":"A"},{"title":"B"},{"title":"C'
    repaired = _repair_truncated_json(raw)
    assert repaired is not None
    titles = [t["title"] for t in repaired["phases"][0]["tasks"]]
    assert titles == ["A", "B"], "the incomplete tail entry must be dropped, not invented"


def test_unrecoverable_input_still_raises() -> None:
    with pytest.raises(AIResponseFormatError):
        load_json_object("this is not json at all")


def test_empty_input_returns_empty_object() -> None:
    assert load_json_object("") == {}


def test_repair_is_bounded_on_pathological_input() -> None:
    """A deeply malformed blob must terminate rather than spin."""
    assert _repair_truncated_json("{" * 5_000) is None


# --------------------------------------------------------------------------- #
# the completion-criteria contract
# --------------------------------------------------------------------------- #
def test_model_omission_is_repaired_not_shipped_empty() -> None:
    raw = {
        "phases": [
            {
                "name": "阶段 1",
                "tasks": [
                    {"title": "读完 RAG 笔记", "completion_criteria": "能复述三种分块策略的取舍"},
                    {"title": "写一份调优记录"},
                ],
            }
        ]
    }
    plan = PlannerService._normalise_plan(raw, "掌握 RAG", 7)
    criteria = [t["completion_criteria"] for t in plan["phases"][0]["tasks"]]

    assert criteria[0] == "能复述三种分块策略的取舍", "the model's own criterion must be kept"
    assert criteria[1], "the missing one must be filled in, never left empty"
    assert "写一份调优记录" in criteria[1]


def test_derived_criterion_names_a_deliverable() -> None:
    criterion = _derive_criterion("整理 JVM 调优参数")
    assert "整理 JVM 调优参数" in criterion
    assert "可交付" in criterion


def test_tasks_without_a_title_are_dropped() -> None:
    raw = {"phases": [{"name": "P", "tasks": [{"title": "  "}, {"title": "有效任务"}]}]}
    plan = PlannerService._normalise_plan(raw, "g", 7)
    assert [t["title"] for t in plan["phases"][0]["tasks"]] == ["有效任务"]


def test_phases_without_usable_tasks_are_dropped() -> None:
    raw = {"phases": [{"name": "空阶段", "tasks": []}, {"name": "P2", "tasks": [{"title": "T"}]}]}
    plan = PlannerService._normalise_plan(raw, "g", 7)
    assert [p["name"] for p in plan["phases"]] == ["P2"]


def test_garbage_plan_falls_back_to_a_minimal_executable_plan() -> None:
    plan = PlannerService._normalise_plan({}, "掌握 RAG 检索", 7)
    assert plan["phases"], "a plan must always be produced"
    assert plan["goal"] == "掌握 RAG 检索"
    assert all(t["completion_criteria"] for p in plan["phases"] for t in p["tasks"]), (
        "even the fallback must satisfy the contract"
    )


def test_invalid_priority_is_normalised() -> None:
    raw = {"phases": [{"name": "P", "tasks": [{"title": "T", "priority": "URGENT!!!"}]}]}
    plan = PlannerService._normalise_plan(raw, "g", 7)
    assert plan["phases"][0]["tasks"][0]["priority"] == "MEDIUM"


def test_non_integer_estimate_is_defaulted() -> None:
    raw = {"phases": [{"name": "P", "tasks": [{"title": "T", "estimated_minutes": "一小时"}]}]}
    plan = PlannerService._normalise_plan(raw, "g", 7)
    assert plan["phases"][0]["tasks"][0]["estimated_minutes"] == 60


# --------------------------------------------------------------------------- #
# structured-output retry
# --------------------------------------------------------------------------- #
class _FlakyProvider:
    """Returns prose first, valid JSON on the retry — the observed behaviour."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls = 0

    async def chat(self, messages, **kwargs):
        from app.ai.types import ChatResult, Usage

        self.calls += 1
        content = self._replies.pop(0) if self._replies else "{}"
        return ChatResult(
            content=content,
            provider="test",
            model="test",
            usage=Usage(prompt_tokens=1, completion_tokens=1),
        )


@pytest.mark.asyncio
async def test_structured_request_retries_unparseable_output() -> None:
    from app.ai.rag.pipeline import request_structured

    provider = _FlakyProvider(["这是一段说明文字，没有 JSON。", '{"summary": "ok"}'])
    payload, _chat = await request_structured(provider, [], attempts=3)
    assert payload == {"summary": "ok"}
    assert provider.calls == 2, "it should have retried exactly once"


@pytest.mark.asyncio
async def test_structured_request_succeeds_first_time() -> None:
    from app.ai.rag.pipeline import request_structured

    provider = _FlakyProvider(['{"a": 1}'])
    payload, _ = await request_structured(provider, [], attempts=3)
    assert payload == {"a": 1}
    assert provider.calls == 1, "a good first answer must not be retried"


@pytest.mark.asyncio
async def test_structured_request_gives_up_after_the_attempt_limit() -> None:
    from app.ai.rag.pipeline import request_structured

    provider = _FlakyProvider(["prose", "more prose", "still prose", "and more"])
    with pytest.raises(AIResponseFormatError):
        await request_structured(provider, [], attempts=3)
    assert provider.calls == 3, "it must stop at the limit rather than loop"

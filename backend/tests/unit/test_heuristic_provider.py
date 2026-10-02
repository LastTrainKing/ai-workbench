"""Unit tests for the deterministic offline AI engine."""

from __future__ import annotations

import json
import math

import pytest

from app.ai.providers.heuristic import (
    HeuristicEmbeddingProvider,
    HeuristicLLMProvider,
    split_sentences,
    tokenize,
)
from app.ai.types import ChatMessage, ToolSpec

SAMPLE = """# JVM 笔记

## 垃圾回收

G1 收集器把堆划分为多个 Region，可以预测停顿时间。
ZGC 追求极低停顿，停顿时间与堆大小基本无关。

## 集合

HashMap 在 JDK 8 之后采用数组加链表加红黑树的结构。

## 待办

- TODO: 整理 JVM 调优参数
"""


# --------------------------------------------------------------------------- #
# tokenisation / sentence splitting
# --------------------------------------------------------------------------- #
def test_tokenize_handles_cjk_and_latin() -> None:
    tokens = tokenize("G1 收集器使用 Region 划分堆内存")
    assert "g1" in tokens
    assert "region" in tokens
    assert any("收集" in t for t in tokens)


def test_tokenize_drops_stopwords() -> None:
    assert "the" not in tokenize("the quick brown fox")
    assert "的" not in tokenize("我的笔记")


def test_split_sentences_handles_chinese_and_english() -> None:
    sentences = split_sentences("第一句。第二句！第三句？Fourth one. Fifth one.")
    assert len(sentences) == 5


# --------------------------------------------------------------------------- #
# embeddings
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_embedding_is_deterministic() -> None:
    provider = HeuristicEmbeddingProvider()
    first = await provider.embed(["垃圾回收算法"])
    second = await provider.embed(["垃圾回收算法"])
    assert first.vectors == second.vectors


@pytest.mark.asyncio
async def test_embedding_is_l2_normalised() -> None:
    provider = HeuristicEmbeddingProvider()
    result = await provider.embed(["JVM 内存结构与垃圾回收"])
    norm = math.sqrt(sum(v * v for v in result.vectors[0]))
    assert norm == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_embedding_dimension_is_stable() -> None:
    provider = HeuristicEmbeddingProvider()
    result = await provider.embed(["a", "b", "c"])
    assert result.dimension == 384
    assert all(len(v) == 384 for v in result.vectors)


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@pytest.mark.asyncio
async def test_related_text_scores_higher_than_unrelated_text() -> None:
    provider = HeuristicEmbeddingProvider()
    result = await provider.embed(
        [
            "垃圾回收 收集器",
            "G1 收集器是垃圾回收器，可以预测停顿时间",
            "HashMap 的默认负载因子是 0.75，容量总是 2 的幂",
        ]
    )
    query, related, unrelated = result.vectors
    assert _cosine(query, related) > _cosine(query, unrelated)


@pytest.mark.asyncio
async def test_empty_embedding_input() -> None:
    provider = HeuristicEmbeddingProvider()
    result = await provider.embed([])
    assert result.vectors == []


# --------------------------------------------------------------------------- #
# chat / generation
# --------------------------------------------------------------------------- #
def _messages(system: str, user: str, context: str = "") -> list[ChatMessage]:
    body = f"{user}\n\n【资料 1】来源：测试文档\n{context}" if context else user
    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=body),
    ]


@pytest.mark.asyncio
async def test_task_marker_routes_to_summary() -> None:
    provider = HeuristicLLMProvider()
    result = await provider.chat(
        _messages("<!-- task: document_summary -->", "总结这份文档", SAMPLE),
        json_mode=True,
    )
    payload = json.loads(result.content)
    assert payload["summary"]
    assert isinstance(payload.get("keywords"), list)


@pytest.mark.asyncio
async def test_knowledge_extraction_returns_all_sections() -> None:
    provider = HeuristicLLMProvider()
    result = await provider.chat(
        _messages("<!-- task: knowledge_extraction -->", "抽取知识", SAMPLE),
        json_mode=True,
    )
    payload = json.loads(result.content)
    for key in ("concepts", "key_points", "terms", "todos", "outline"):
        assert key in payload, key
    # The document really does contain a TODO line, so it must be found.
    assert any("JVM 调优" in str(t.get("title", "")) for t in payload["todos"])


@pytest.mark.asyncio
async def test_task_planner_produces_phases_with_criteria() -> None:
    provider = HeuristicLLMProvider()
    result = await provider.chat(
        _messages("<!-- task: task_planner -->", "一个月内掌握 JVM", SAMPLE),
        json_mode=True,
    )
    payload = json.loads(result.content)
    assert payload["phases"]
    for phase in payload["phases"]:
        assert phase["name"]
        for task in phase["tasks"]:
            assert task["title"]
            assert task["completion_criteria"]
            assert isinstance(task["estimated_minutes"], int)
            assert task["priority"] in {"LOW", "MEDIUM", "HIGH", "URGENT"}


@pytest.mark.asyncio
async def test_rag_answer_only_uses_supplied_context() -> None:
    provider = HeuristicLLMProvider()
    result = await provider.chat(
        _messages("<!-- task: rag_answer -->", "G1 有什么特点？", SAMPLE),
    )
    assert "Region" in result.content or "停顿" in result.content
    assert "根据检索到的资料" in result.content


@pytest.mark.asyncio
async def test_rag_answer_without_context_is_honest() -> None:
    provider = HeuristicLLMProvider()
    result = await provider.chat(_messages("<!-- task: rag_answer -->", "任何问题"))
    assert "没有检索到可用资料" in result.content


@pytest.mark.asyncio
async def test_usage_is_reported() -> None:
    provider = HeuristicLLMProvider()
    result = await provider.chat(_messages("<!-- task: document_summary -->", "总结", SAMPLE))
    assert result.usage.prompt_tokens > 0
    assert result.usage.completion_tokens > 0
    assert result.usage.total_tokens == (
        result.usage.prompt_tokens + result.usage.completion_tokens
    )


# --------------------------------------------------------------------------- #
# agent tool policy
# --------------------------------------------------------------------------- #
TOOLS = [
    ToolSpec(
        name="knowledge_search",
        description="search",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    ),
    ToolSpec(
        name="task_create_batch",
        description="create tasks",
        parameters={
            "type": "object",
            "properties": {"from_goal": {"type": "string"}},
            "required": ["from_goal"],
        },
    ),
    ToolSpec(
        name="note_create",
        description="create a note",
        parameters={"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
    ),
]


@pytest.mark.asyncio
async def test_agent_starts_with_knowledge_search() -> None:
    provider = HeuristicLLMProvider()
    result = await provider.chat(
        _messages("<!-- task: agent_system -->", "根据知识库制定学习计划并创建任务"),
        tools=TOOLS,
    )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "knowledge_search"
    assert result.finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_agent_advances_through_the_tool_sequence() -> None:
    provider = HeuristicLLMProvider()
    messages = _messages("<!-- task: agent_system -->", "根据知识库制定学习计划并创建任务")
    messages.append(
        ChatMessage(role="tool", name="knowledge_search", tool_call_id="1", content="{}")
    )
    result = await provider.chat(messages, tools=TOOLS)
    assert result.tool_calls[0].name == "task_create_batch"


@pytest.mark.asyncio
async def test_agent_terminates_when_all_tools_are_used() -> None:
    provider = HeuristicLLMProvider()
    messages = _messages("<!-- task: agent_system -->", "根据知识库制定学习计划并创建任务")
    for name in ("knowledge_search", "task_create_batch", "note_create"):
        messages.append(ChatMessage(role="tool", name=name, tool_call_id=name, content="{}"))
    result = await provider.chat(messages, tools=TOOLS)
    assert result.tool_calls == []
    assert result.content


def test_provider_reports_tool_calling_support() -> None:
    assert HeuristicLLMProvider().supports_tool_calling is True

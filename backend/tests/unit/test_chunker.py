"""Unit tests for the heading-aware chunker."""

from __future__ import annotations

from app.ai.rag.chunker import chunk_text

DOC = """# JVM 学习笔记

## 垃圾回收

G1 收集器把堆划分为多个 Region，可以预测停顿时间，是 JDK 9 之后的默认收集器。
ZGC 追求极低停顿，停顿时间与堆大小基本无关。

## 集合框架

HashMap 在 JDK 8 之后采用数组加链表加红黑树的结构。
链表长度超过 8 且容量不小于 64 时转为红黑树，最坏查询复杂度从 O(n) 降到 O(log n)。
默认负载因子 0.75，容量总是 2 的幂。

## 并发

线程池的核心参数包括 corePoolSize、maximumPoolSize、keepAliveTime 和 workQueue。
生产环境禁止直接 new Thread，必须使用线程池。
"""


def test_chunks_are_produced() -> None:
    chunks = chunk_text(DOC, chunk_size=200, overlap=40)
    assert len(chunks) >= 3
    assert all(chunk.content.strip() for chunk in chunks)


def test_indices_are_sequential() -> None:
    chunks = chunk_text(DOC, chunk_size=150, overlap=30)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_heading_breadcrumb_is_prefixed() -> None:
    chunks = chunk_text(DOC, chunk_size=200, overlap=40)
    headings = {c.heading for c in chunks}
    assert "JVM 学习笔记 > 垃圾回收" in headings
    # The breadcrumb is part of the embedded text, not just metadata.
    gc_chunk = next(c for c in chunks if c.heading.endswith("垃圾回收"))
    assert gc_chunk.content.startswith("JVM 学习笔记 > 垃圾回收")


def test_chunk_size_is_respected() -> None:
    chunks = chunk_text(DOC, chunk_size=120, overlap=20)
    # Allow the documented 2x slack for a single oversized sentence.
    assert all(c.char_count <= 260 for c in chunks), [c.char_count for c in chunks]


def test_overlap_preserves_context() -> None:
    long_text = "\n\n".join(f"这是第 {i} 个句子，用于测试重叠逻辑是否生效。" for i in range(40))
    chunks = chunk_text(long_text, chunk_size=150, overlap=60)
    assert len(chunks) > 1
    # Consecutive chunks must share some content when overlap > 0.
    shared = set(chunks[0].content.split("。")) & set(chunks[1].content.split("。"))
    assert shared, "expected the overlap window to carry content across chunks"


def test_empty_input_returns_nothing() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_invalid_chunk_size_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        chunk_text("some text", chunk_size=0)


def test_overlap_is_clamped_to_half_of_chunk_size() -> None:
    chunks = chunk_text(DOC, chunk_size=100, overlap=10_000)
    assert chunks
    assert all(c.char_count <= 220 for c in chunks)


def test_token_estimate_is_populated() -> None:
    chunks = chunk_text(DOC, chunk_size=200, overlap=40)
    assert all(c.token_estimate > 0 for c in chunks)
    assert all(c.char_count == len(c.content) for c in chunks)


def test_single_oversized_sentence_is_split() -> None:
    text = "很长的一句话" * 500  # no sentence terminators at all
    chunks = chunk_text(text, chunk_size=200, overlap=0)
    assert len(chunks) > 1
    assert all(c.char_count <= 200 for c in chunks)

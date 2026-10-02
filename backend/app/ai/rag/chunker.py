"""Heading-aware recursive text chunker.

Design decisions (see docs/rag.md for the full rationale):

1. **Structure first.**  Markdown headings split the document into semantic
   sections before any size-based splitting happens, so a chunk never mixes two
   unrelated topics.
2. **Sentence-boundary splits.**  Within a section we accumulate whole
   sentences/paragraphs until the target size is reached, so we never cut a
   sentence in half.
3. **Overlap.**  Consecutive chunks share ``overlap`` characters to preserve
   context across boundaries — important for retrieval quality.
4. **Heading breadcrumb.**  Every chunk is prefixed with its heading path
   ("JVM > 垃圾回收") which materially improves embedding quality and makes the
   citation UI readable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ai.types import estimate_tokens

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])|(?<=[.!?])\s+|\n{2,}")
_MIN_CHUNK_CHARS = 120


@dataclass(slots=True)
class TextChunk:
    index: int
    content: str
    heading: str = ""
    char_count: int = 0
    token_estimate: int = 0

    def __post_init__(self) -> None:
        self.char_count = len(self.content)
        self.token_estimate = estimate_tokens(self.content)


@dataclass(slots=True)
class _Section:
    heading_path: list[str] = field(default_factory=list)
    text: str = ""


def _split_sections(text: str) -> list[_Section]:
    """Split raw text into (heading breadcrumb, body) sections."""
    sections: list[_Section] = []
    stack: list[str] = []
    current = _Section(heading_path=[], text="")
    position = 0

    for match in _HEADING.finditer(text):
        body = text[position : match.start()]
        if body.strip():
            current.text = body
            sections.append(current)
        level = len(match.group(1))
        title = match.group(2).strip()
        stack = stack[: level - 1]
        while len(stack) < level - 1:
            stack.append("")
        stack.append(title)
        current = _Section(heading_path=[h for h in stack if h], text="")
        position = match.end()

    tail = text[position:]
    if tail.strip():
        current.text = tail
        sections.append(current)
    elif current.heading_path:
        sections.append(current)
    return sections


def _split_sentences(text: str) -> list[str]:
    parts = [p for p in _SENTENCE_SPLIT.split(text) if p is not None]
    out: list[str] = []
    for part in parts:
        stripped = part.strip()
        if stripped:
            out.append(stripped)
    return out


def _pack(sentences: list[str], size: int, overlap: int) -> list[str]:
    """Greedily pack sentences into windows of ~``size`` chars with overlap."""
    chunks: list[str] = []
    buffer: list[str] = []
    length = 0

    for sentence in sentences:
        # A single oversized sentence becomes its own (hard-split) chunk.
        if len(sentence) > size * 2:
            if buffer:
                chunks.append(" ".join(buffer))
                buffer, length = [], 0
            step = size - overlap if size > overlap else size
            for i in range(0, len(sentence), max(1, step)):
                chunks.append(sentence[i : i + size])
            continue

        if length + len(sentence) + 1 > size and buffer:
            chunks.append(" ".join(buffer))
            # Rebuild the tail of the previous window to create the overlap.
            tail: list[str] = []
            tail_len = 0
            for s in reversed(buffer):
                if tail_len + len(s) > overlap:
                    break
                tail.insert(0, s)
                tail_len += len(s) + 1
            buffer = tail
            length = tail_len

        buffer.append(sentence)
        length += len(sentence) + 1

    if buffer:
        chunks.append(" ".join(buffer))
    return [c.strip() for c in chunks if c.strip()]


def chunk_text(
    text: str,
    *,
    chunk_size: int = 900,
    overlap: int = 150,
) -> list[TextChunk]:
    if not text or not text.strip():
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    overlap = max(0, min(overlap, chunk_size // 2))

    sections = _split_sections(text)
    if not sections:
        sections = [_Section(heading_path=[], text=text)]

    raw_chunks: list[tuple[str, str]] = []
    for section in sections:
        breadcrumb = " > ".join(section.heading_path)
        body = section.text.strip()
        if not body:
            continue
        # Very short sections are kept whole: splitting them hurts retrieval.
        if len(body) <= chunk_size:
            raw_chunks.append((breadcrumb, body))
            continue
        for piece in _pack(_split_sentences(body), chunk_size, overlap):
            if len(piece) < _MIN_CHUNK_CHARS and raw_chunks:
                # Merge a runt into the previous chunk of the same section.
                prev_crumb, prev_text = raw_chunks[-1]
                if prev_crumb == breadcrumb and len(prev_text) + len(piece) <= chunk_size * 2:
                    raw_chunks[-1] = (prev_crumb, f"{prev_text}\n{piece}")
                    continue
            raw_chunks.append((breadcrumb, piece))

    chunks: list[TextChunk] = []
    for index, (breadcrumb, content) in enumerate(raw_chunks):
        # The breadcrumb is embedded with the text: it adds topical context that
        # the raw paragraph may lack ("JVM > 垃圾回收 — G1 的回收周期...").
        display = f"{breadcrumb}\n{content}" if breadcrumb else content
        chunks.append(TextChunk(index=index, content=display, heading=breadcrumb))
    return chunks

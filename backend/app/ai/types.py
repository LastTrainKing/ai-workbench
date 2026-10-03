"""Provider-agnostic AI value objects.

These types are the contract between the AI layer and the rest of the
application.  Nothing outside ``app.ai.providers`` knows which vendor is in use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class ChatMessage:
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    # Populated on assistant messages that request tool executions; required by
    # the OpenAI contract so that the following ``role="tool"`` message can be
    # correlated with the call that produced it.
    tool_calls: list[dict[str, Any]] | None = None
    # Reasoning models (DeepSeek thinking mode, and similar) reject a follow-up
    # turn that omits the previous assistant turn's chain of thought:
    #   "The `reasoning_content` in the thinking mode must be passed back".
    # Only set for providers that emit it, so other vendors never see the field.
    reasoning_content: str | None = None

    def to_openai(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role}
        # Assistant messages that only carry tool_calls must send content=null.
        if self.content or not self.tool_calls:
            msg["content"] = self.content
        else:
            msg["content"] = None
        if self.name:
            msg["name"] = self.name
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.reasoning_content:
            msg["reasoning_content"] = self.reasoning_content
        return msg


@dataclass(slots=True)
class ToolCallRequest:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def merged(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass(slots=True)
class ChatResult:
    content: str
    model: str = ""
    provider: str = ""
    usage: Usage = field(default_factory=Usage)
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
    #: Chain of thought for reasoning models; must be echoed back on the next
    #: turn of a tool-calling loop (see ``ChatMessage.reasoning_content``).
    reasoning_content: str = ""

    @property
    def truncated(self) -> bool:
        """True when the model hit the token ceiling instead of finishing."""
        return self.finish_reason == "length"


@dataclass(slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str = ""
    provider: str = ""
    dimension: int = 0
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0


@dataclass(slots=True)
class RetrievedChunk:
    """A single retrieved context unit, always traceable back to its source."""

    chunk_id: str
    document_id: str
    document_title: str
    knowledge_base_id: str
    chunk_index: int
    content: str
    score: float
    vector_score: float = 0.0
    keyword_score: float = 0.0
    heading: str = ""
    source: str = "hybrid"

    def to_citation(self, index: int) -> dict[str, Any]:
        return {
            "index": index,
            "document_id": self.document_id,
            "document_title": self.document_title,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "heading": self.heading,
            "score": round(self.score, 4),
            "snippet": self.content[:400],
        }


@dataclass(slots=True)
class ToolSpec:
    """Declaration of a tool exposed to the agent."""

    name: str
    description: str
    parameters: dict[str, Any]
    category: str = "general"
    requires_confirmation: bool = False

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(slots=True)
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""
    latency_ms: int = 0
    summary: str = ""

    def to_payload(self, limit: int = 4000) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok}
        if self.error:
            payload["error"] = self.error
        if self.data is not None:
            text = self.data if isinstance(self.data, str) else _json_safe(self.data)
            payload["data"] = text if len(str(text)) <= limit else str(text)[:limit] + "...<truncated>"
        return payload


def _json_safe(value: Any) -> Any:
    import json

    try:
        json.dumps(value, default=str)
        return value
    except (TypeError, ValueError):
        return str(value)


def estimate_tokens(text: str) -> int:
    """Dependency-free token estimate.

    We deliberately avoid ``tiktoken``: it downloads BPE vocabulary files at
    runtime, which breaks offline/local-first usage.  A blended heuristic
    (CJK ≈ 1 token/char, latin ≈ 1 token/4 chars) is accurate enough for
    budgeting and for the token counters shown on the dashboard.
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return max(1, cjk + round(other / 4))

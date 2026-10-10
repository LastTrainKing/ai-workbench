"""Provider-agnostic AI value objects.

These types are the contract between the AI layer and the rest of the
application.  Nothing outside ``app.ai.providers`` knows which vendor is in use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
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
class ChatStreamChunk:
    """One piece of a streamed completion.

    Streaming is not "the same answer, delivered differently". The user sees the answer
    appear while it is being written, which is the difference between a chat interface and
    a form that returns a paragraph after a pause. It also means the transport is different:
    server-sent events, read line by line, with a terminator — so this type carries the
    pieces the caller needs to tell "still going" from "finished" from "failed".

    ``usage`` is only present on the final chunk, and only when the provider is asked to
    include it. A provider that does not report it leaves it None rather than zero: zero
    would look like a measured value.
    """

    delta: str = ""
    #: Set on the last chunk the provider sends ("stop", "length", …).
    finish_reason: str = ""
    #: Token counts, when the provider reports them on the stream.
    usage: Usage | None = None
    #: Chain of thought, for reasoning models that stream it separately.
    reasoning_delta: str = ""


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
    """Declaration of a tool exposed to the agent.

    The first five fields describe what the tool *does* and are what the model
    sees. The rest describe what it is *allowed* to do, and are enforced by the
    permission layer rather than by the model — a model that ignores them changes
    nothing, because the executor checks them again before anything runs.

    The governance fields all have safe defaults so the existing business tools
    (knowledge search, tasks, notes) keep working untouched; they are read-only
    and operate on the database, not on the machine.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    category: str = "general"
    requires_confirmation: bool = False

    # --- governance (enforced by app.ai.tools, not by the model) -------------
    #: Minimum permission level the caller must hold. 0=observe, 1=workspace,
    #: 2=system, 3=full control. See app.ai.tools.permissions.PermissionLevel.
    permission_level: int = 1
    #: low | medium | high | critical. Drives the confirmation prompt.
    risk_level: str = "low"
    #: Hard ceiling on a single invocation, enforced by the executor.
    timeout_seconds: float = 30.0
    #: JSON schema of the result, for documentation and UI rendering.
    output_schema: dict[str, Any] | None = None
    #: always | on_write | never — how much detail the audit log keeps.
    audit_policy: str = "always"
    #: True when the tool can change something on the machine or in the database.
    mutates_state: bool = False
    #: True when repeating the call with the same arguments leaves the same state.
    #:
    #: Only meaningful when `mutates_state` is True; a read-only tool is replayable
    #: by construction and is classified as such without asking.
    #:
    #: **The default is False, and that is the safe direction.** Claiming a tool is
    #: idempotent is a claim about its implementation, and getting that claim wrong is
    #: precisely the failure the invocation record exists to prevent — a duplicated
    #: `file_append` corrupts a file, a duplicated `git_commit` writes a second commit.
    #: A tool nobody has classified is therefore treated as unsafe to replay, so the
    #: cost of forgetting to classify is a manual check rather than a repeated side
    #: effect. The four tools that are marked idempotent are marked because their
    #: implementation was read, not because their name sounded harmless.
    idempotent: bool = False

    def replay_class(self) -> str:
        """How a recovery may treat a repeat of this call.

        ``read_only``  — repeating is free; there is no state to duplicate.
        ``idempotent`` — repeating converges to the same state.
        ``unsafe``     — repeating may duplicate a side effect. **Never auto-replayed.**

        This is the single place the three-way classification is decided, so the
        executor, the recovery pass and the UI cannot drift apart on what a tool is.
        """
        if not self.mutates_state:
            return "read_only"
        return "idempotent" if self.idempotent else "unsafe"

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def governance(self) -> dict[str, Any]:
        """The part of the spec the UI and the permission layer care about."""
        return {
            "permission_level": self.permission_level,
            "risk_level": self.risk_level,
            "timeout_seconds": self.timeout_seconds,
            "audit_policy": self.audit_policy,
            "mutates_state": self.mutates_state,
            # The derived three-way classification, so the UI does not have to
            # re-implement the rule from `mutates_state` + `idempotent` and get it
            # subtly different.
            "replay_class": self.replay_class(),
            "output_schema": self.output_schema,
        }


@dataclass(slots=True)
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""
    latency_ms: int = 0
    summary: str = ""
    #: Process exit code for the terminal and project tools. ``None`` for tools
    #: that do not run a process, which is most of them — so it stays optional
    #: and the existing tools are unaffected.
    exit_code: int | None = None

    def to_payload(self, limit: int = 4000) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok}
        if self.error:
            payload["error"] = self.error
        if self.data is not None:
            text = self.data if isinstance(self.data, str) else _json_safe(self.data)
            payload["data"] = text if len(str(text)) <= limit else str(text)[:limit] + "...<truncated>"
        return payload


class ReconcileVerdict(StrEnum):
    """What a check of the world could establish about an interrupted invocation.

    **Every verdict is a statement about the state, not about history.** A crash destroys
    the only record of what the tool observed; nothing can reconstruct that. What a check
    can do is look at the target *now* and answer whether the state the invocation intended
    is there. Recovery does not need history — it needs to know whether re-running is
    necessary, and for that the state is the right question.

    That framing is also what keeps these three verdicts honest rather than convenient:

      ``APPLIED``      the intended state is present. Re-running would add nothing.
      ``NOT_APPLIED``  the intended state is absent. The call did not take effect, or was
                       undone afterwards — either way re-running would not duplicate it.
      ``UNDECIDABLE``  the check could not tell. This is the default and it stays the
                       default: a tool with no check reports this, and nothing is replayed
                       on the strength of a guess.
    """

    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    UNDECIDABLE = "UNDECIDABLE"


@dataclass(slots=True)
class ReconcileOutcome:
    """The result of checking one invocation against the world."""

    verdict: ReconcileVerdict
    #: One sentence a person can act on. It is shown in the run detail view, so it says
    #: what was compared rather than only what was concluded.
    detail: str = ""
    #: What the check actually looked at — path, digest, index contents. Recorded so the
    #: conclusion can be re-examined without re-running the check.
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "detail": self.detail,
            "evidence": _json_safe(self.evidence),
        }


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

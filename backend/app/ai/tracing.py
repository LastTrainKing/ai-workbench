"""AI execution tracing.

Every AI invocation — chat, extraction, planning, agent step — is recorded as an
``ai_runs`` row (plus ``tool_calls`` / ``ai_run_sources`` children) so the product
can answer "what did the AI actually do?".

The recorder is intentionally explicit (``start`` → mutations → ``finish``)
instead of a decorator, because a run must survive partial failure: if the model
times out at step 4 of an agent loop, we still want the first three tool calls.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.types import ChatResult, Usage, estimate_tokens
from app.core.logging import get_logger, new_run_id, run_id_var
from app.domain.enums import RunStatus, RunType, ToolCallStatus
from app.models.ai import AgentRun, AIRun, AIRunSource, ToolCall

logger = get_logger(__name__)

# Rough public price table (USD per 1M tokens) used only for an order-of-magnitude
# cost estimate shown in the UI. Unknown models report 0 instead of guessing.
_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "qwen-plus": (0.40, 1.20),
    "qwen-turbo": (0.05, 0.20),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
}


def estimate_cost(model: str, usage: Usage) -> float:
    for key, (prompt_price, completion_price) in _PRICES.items():
        if key in (model or "").lower():
            return round(
                usage.prompt_tokens / 1_000_000 * prompt_price
                + usage.completion_tokens / 1_000_000 * completion_price,
                6,
            )
    return 0.0


@dataclass
class RunRecorder:
    """Mutable handle for a single AI execution."""

    session: AsyncSession
    run_id: str
    model_row: AIRun
    started: float = field(default_factory=time.perf_counter)
    usage: Usage = field(default_factory=Usage)
    retries: int = 0
    _finished: bool = False

    # -------------------------------------------------------------- factories --
    @classmethod
    async def start(
        cls,
        session: AsyncSession,
        *,
        run_type: RunType | str,
        owner_id: uuid.UUID,
        provider: str = "",
        model: str = "",
        prompt_name: str = "",
        prompt_version: int = 0,
        request_payload: dict[str, Any] | None = None,
        project_id: uuid.UUID | None = None,
        knowledge_base_id: uuid.UUID | None = None,
        parent_run_id: str = "",
    ) -> RunRecorder:
        run_id = new_run_id()
        run_id_var.set(run_id)
        row = AIRun(
            run_id=run_id,
            owner_id=owner_id,
            project_id=project_id,
            knowledge_base_id=knowledge_base_id,
            parent_run_id=parent_run_id,
            run_type=str(run_type),
            status=RunStatus.RUNNING.value,
            provider=provider,
            model=model,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            request_payload=_sanitize(request_payload or {}),
            tools_used=[],
            created_at=datetime.now(UTC),
        )
        session.add(row)
        await session.flush()
        logger.info(
            "ai_run_started",
            extra={"run_type": str(run_type), "provider": provider, "model": model},
        )
        return cls(session=session, run_id=run_id, model_row=row)

    # ------------------------------------------------------------------ mutate --
    def record_usage(self, usage: Usage | None) -> None:
        if usage:
            self.usage = self.usage.merged(usage)

    def record_retry(self, count: int = 1) -> None:
        self.retries += count

    def set_retrieval(self, meta: dict[str, Any], count: int) -> None:
        self.model_row.retrieval_meta = _sanitize(meta)
        self.model_row.retrieval_count = count

    def set_result_payload(self, payload: dict[str, Any]) -> None:
        self.model_row.result_payload = _sanitize(payload)

    async def add_sources(self, sources: list[dict[str, Any]]) -> None:
        for source in sources:
            document_id = _as_uuid(source.get("document_id"))
            chunk_id = _as_uuid(source.get("chunk_id"))
            self.session.add(
                AIRunSource(
                    ai_run_id=self.model_row.id,
                    document_id=document_id,
                    chunk_id=chunk_id,
                    document_title=str(source.get("document_title") or "")[:300],
                    chunk_index=int(source.get("chunk_index") or -1),
                    score=float(source.get("score") or 0.0),
                    snippet=str(source.get("snippet") or "")[:1000],
                )
            )
        await self.session.flush()

    async def attach_agent_run(self, *, goal: str, max_steps: int) -> AgentRun:
        agent = AgentRun(
            ai_run_id=self.model_row.id,
            owner_id=self.model_row.owner_id,
            goal=goal[:4000],
            status=RunStatus.RUNNING.value,
            max_steps=max_steps,
            plan=[],
            scratchpad=[],
        )
        self.session.add(agent)
        await self.session.flush()
        return agent

    # ------------------------------------------------------------------ finish --
    async def finish(
        self,
        *,
        status: RunStatus | str = RunStatus.SUCCEEDED,
        response_text: str = "",
        result_payload: dict[str, Any] | None = None,
        error_code: str = "",
        error_message: str = "",
        chat_result: ChatResult | None = None,
    ) -> AIRun:
        if self._finished:
            return self.model_row
        self._finished = True

        if chat_result is not None:
            self.record_usage(chat_result.usage)
            self.model_row.provider = chat_result.provider or self.model_row.provider
            self.model_row.model = chat_result.model or self.model_row.model

        latency_ms = int((time.perf_counter() - self.started) * 1000)
        row = self.model_row
        row.status = str(status)
        row.response_text = response_text[:60_000]
        row.latency_ms = latency_ms
        row.retry_count = self.retries
        row.error_code = error_code
        row.error_message = error_message[:4000]
        row.prompt_tokens = self.usage.prompt_tokens
        row.completion_tokens = self.usage.completion_tokens
        row.total_tokens = self.usage.total_tokens or (
            self.usage.prompt_tokens + self.usage.completion_tokens
        )
        row.cost_estimate = estimate_cost(row.model, self.usage)
        if result_payload:
            row.result_payload = _sanitize(result_payload)
        row.finished_at = datetime.now(UTC)
        await self.session.flush()

        logger.info(
            "ai_run_finished",
            extra={
                "status": row.status,
                "latency_ms": latency_ms,
                "tokens": row.total_tokens,
                "tools": row.tools_used,
                "error": error_code or None,
            },
        )
        run_id_var.set("-")
        return row


# --------------------------------------------------------------------------- #
# Tool invocations: intent before execution, outcome after
# --------------------------------------------------------------------------- #
def new_invocation_id() -> str:
    """A stable id for one tool invocation, minted before anything runs."""
    return f"inv_{uuid.uuid4().hex}"


def replay_class_of(spec: Any) -> str:
    """`read_only` | `idempotent` | `unsafe` for a tool spec, tolerating a missing one.

    A tool the registry cannot describe is treated as `unsafe`. Guessing "read-only"
    for an unknown tool would be the one guess that turns a recovery into a duplicate
    side effect, so the unknown case falls to the conservative answer.
    """
    if spec is None:
        return "unsafe"
    classifier = getattr(spec, "replay_class", None)
    if callable(classifier):
        return str(classifier())
    if not getattr(spec, "mutates_state", False):
        return "read_only"
    return "idempotent" if getattr(spec, "idempotent", False) else "unsafe"


async def begin_tool_call(
    session: AsyncSession,
    *,
    run: AIRun,
    tool_name: str,
    arguments: dict[str, Any],
    step_index: int,
    mutates_state: bool = False,
    agent_run_id: uuid.UUID | None = None,
    invocation_id: str | None = None,
) -> ToolCall:
    """Persist the **intent** to call a tool, and hand back the row.

    This is the half that did not exist. `record_tool_call` wrote the row only after
    the tool returned, so the window between "the tool started" and "the tool's result
    was written" left no evidence at all — a process killed inside it produced a run
    that showed no tool call had ever been attempted, and re-running the goal repeated
    the tool. For a `file_append` or a `git_commit` that is a duplicated side effect.

    The caller **must commit** before handing the call to the executor. A flush only
    puts the row in an uncommitted transaction, which is invisible to any other session
    and is discarded outright if the process dies — exactly the failure being fixed.

    `invocation_id` is an optional caller-supplied idempotency key. Passing the same one
    twice returns the existing row rather than creating a second one, so a retry that
    repeats an already-accepted invocation is visible as a retry rather than as a second
    execution.

    The row is created as `RUNNING`: from this instant, if the process dies, nothing can
    say whether the side effect landed. That is the honest state, and it is what makes
    `UNKNOWN` reachable — a row that never existed could not be recovered as anything.
    """
    from sqlalchemy import select

    if invocation_id:
        existing = (
            await session.execute(select(ToolCall).where(ToolCall.invocation_id == invocation_id))
        ).scalar_one_or_none()
        if existing is not None:
            logger.info(
                "tool_invocation_replayed",
                extra={"invocation_id": invocation_id, "tool": existing.tool_name},
            )
            return existing

    now = datetime.now(UTC)
    call = ToolCall(
        ai_run_id=run.id,
        agent_run_id=agent_run_id,
        step_index=step_index,
        tool_name=tool_name,
        arguments=_sanitize(arguments),
        result={},
        status=ToolCallStatus.RUNNING.value,
        error_message="",
        latency_ms=0,
        created_at=now,
        invocation_id=invocation_id or new_invocation_id(),
        started_at=now,
        mutates_state=bool(mutates_state),
    )
    session.add(call)

    used = list(run.tools_used or [])
    if tool_name not in used:
        used.append(tool_name)
    run.tools_used = used
    run.tool_call_count = (run.tool_call_count or 0) + 1

    await session.flush()
    return call


async def finish_tool_call(
    session: AsyncSession,
    call: ToolCall,
    *,
    ok: bool,
    result: dict[str, Any] | None = None,
    latency_ms: int = 0,
    error_message: str = "",
) -> ToolCall:
    """Persist the **outcome** of an invocation whose intent was already recorded.

    `finished_at` is what closes the window `begin_tool_call` opened. A row that has
    `started_at` and no `finished_at` is the definition of "we do not know".
    """
    call.status = (
        ToolCallStatus.SUCCEEDED.value if ok else ToolCallStatus.FAILED.value
    )
    call.result = _sanitize(result or {})
    call.latency_ms = int(latency_ms or 0)
    call.error_message = (error_message or "")[:2000]
    call.finished_at = datetime.now(UTC)
    await session.flush()
    return call


async def record_skipped_call(
    session: AsyncSession,
    *,
    run: AIRun,
    tool_name: str,
    arguments: dict[str, Any],
    step_index: int,
    reason: str,
    agent_run_id: uuid.UUID | None = None,
) -> ToolCall:
    """Record a call that was refused before dispatch, so nothing ran and nothing can have.

    Written in one phase on purpose: there is no window here. `SKIPPED` is a terminal
    state, and `finished_at` is set immediately because the decision and its outcome are
    the same event.
    """
    now = datetime.now(UTC)
    call = ToolCall(
        ai_run_id=run.id,
        agent_run_id=agent_run_id,
        step_index=step_index,
        tool_name=tool_name,
        arguments=_sanitize(arguments),
        result=_sanitize({"skipped": True, "reason": reason}),
        status=ToolCallStatus.SKIPPED.value,
        error_message=reason[:2000],
        latency_ms=0,
        created_at=now,
        invocation_id=new_invocation_id(),
        started_at=now,
        finished_at=now,
        mutates_state=False,
    )
    session.add(call)
    used = list(run.tools_used or [])
    if tool_name not in used:
        used.append(tool_name)
    run.tools_used = used
    run.tool_call_count = (run.tool_call_count or 0) + 1
    await session.flush()
    return call


async def resolve_orphan_tool_calls(session: AsyncSession) -> list[ToolCall]:
    """Turn `RUNNING` invocations left by a dead process into `UNKNOWN`.

    A `RUNNING` row at startup is not running: the registry is empty by definition, so
    no process is driving it. But it is not `FAILED` either — the tool may have completed
    its side effect and died before the result was written. `UNKNOWN` is the only status
    that does not assert something the system cannot know.

    `finished_at` is deliberately left `NULL`. Stamping it would say the invocation
    ended, and the whole point of the row is that its end was never observed.
    """
    from sqlalchemy import select

    rows = list(
        (
            await session.execute(
                select(ToolCall).where(ToolCall.status == ToolCallStatus.RUNNING.value)
            )
        ).scalars()
    )
    for call in rows:
        call.status = ToolCallStatus.UNKNOWN.value
        call.error_message = (
            call.error_message
            or "进程在工具调用期间终止，无法确认该操作是否已经产生效果"
        )
    if rows:
        await session.flush()
        logger.warning(
            "orphan_tool_calls_resolved",
            extra={
                "count": len(rows),
                "unsafe": [c.tool_name for c in rows if c.mutates_state][:10],
            },
        )
    return rows


async def unresolved_tool_calls(session: AsyncSession, ai_run_id: uuid.UUID) -> list[ToolCall]:
    """The invocations of one run whose effect cannot be determined, mutating first."""
    from sqlalchemy import select

    rows = list(
        (
            await session.execute(
                select(ToolCall).where(
                    ToolCall.ai_run_id == ai_run_id,
                    ToolCall.status == ToolCallStatus.UNKNOWN.value,
                )
            )
        ).scalars()
    )
    return [c for c in rows if c.mutates_state]


async def persist_terminal_record(session: AsyncSession, *, context: str) -> None:
    """Commit the run record so the caller's rollback cannot discard it.

    **Why this exists.** `session_scope` — which is what every API route uses — rolls back
    on any exception. The executor and the RAG pipeline both write `status=FAILED`, an error
    code and an error message, and then re-raise, so the caller's rollback erased exactly
    the record they exist to write: a failed run left no trace at all.

    That contradicts this module's own guarantee. The docstring above says a run must
    survive partial failure — "if the model times out at step 4 of an agent loop, we still
    want the first three tool calls" — and it was the failure case, the one where the record
    matters most, that was being dropped. The product states the same guarantee to its
    users: every AI invocation is recorded so it can answer "what did the AI actually do?".

    **The trade-off, stated rather than implied.** Committing here also commits any business
    write the tools performed before the failure — a task the agent created, a note it
    wrote. The alternative is losing the record of work that demonstrably happened, and
    leaving the database disagreeing with what the agent already reported to the user. The
    success path commits the same writes; only the run's terminal status differs.

    A failure to commit is logged and swallowed: the original exception is the one the
    caller needs to see, and replacing it with a database error would hide the cause.
    """
    from sqlalchemy.exc import SQLAlchemyError

    try:
        await session.commit()
    except SQLAlchemyError as exc:
        logger.error(
            "terminal_record_not_persisted",
            extra={"context": context, "error": f"{type(exc).__name__}: {exc}"},
        )


def _as_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str) and value:
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


_MAX_STRING = 8000
# Deep-but-legitimate payloads exist: a task planner result is
# root → phases[] → phase{} → tasks[] → task{} → subtasks[] → subtask{} → values
# (8 levels). A shallow cap would silently replace real values with
# "<max-depth>", so the guard sits well above any structure the product produces
# while still bounding pathological input.
_MAX_DEPTH = 14


def _sanitize(payload: Any, depth: int = 0) -> Any:
    """Trim large blobs so an AI run row can never blow up the database."""
    if depth > _MAX_DEPTH:
        return "<max-depth>"
    if isinstance(payload, dict):
        return {str(k): _sanitize(v, depth + 1) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_sanitize(v, depth + 1) for v in payload[:200]]
    if isinstance(payload, str):
        return payload if len(payload) <= _MAX_STRING else payload[:_MAX_STRING] + "...<truncated>"
    if isinstance(payload, (int, float, bool)) or payload is None:
        return payload
    return str(payload)[:1000]


def truncate_for_prompt(text: str, max_tokens: int) -> str:
    """Budget-aware truncation for context assembly."""
    if estimate_tokens(text) <= max_tokens:
        return text
    # Character-based approximation is fine here; we keep 10% headroom.
    approx_chars = int(max_tokens * 3.6)
    return text[:approx_chars] + "\n...<内容已按上下文预算截断>"

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
from app.domain.enums import RunStatus, RunType
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

    async def record_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        status: str,
        step_index: int,
        latency_ms: int,
        error_message: str = "",
        agent_run_id: uuid.UUID | None = None,
    ) -> ToolCall:
        call = ToolCall(
            ai_run_id=self.model_row.id,
            agent_run_id=agent_run_id,
            step_index=step_index,
            tool_name=tool_name,
            arguments=_sanitize(arguments),
            result=_sanitize(result),
            status=status,
            error_message=error_message[:2000],
            latency_ms=latency_ms,
            created_at=datetime.now(UTC),
        )
        self.session.add(call)
        used = list(self.model_row.tools_used or [])
        if tool_name not in used:
            used.append(tool_name)
        self.model_row.tools_used = used
        self.model_row.tool_call_count = (self.model_row.tool_call_count or 0) + 1
        await self.session.flush()
        return call

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

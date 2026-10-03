"""RAG pipeline: rewrite → retrieve → assemble → generate → cite.

Every stage is recorded on the AI run so the UI can show the full trace:
which query variants were used, how many candidates each leg returned, which
chunks made it into the context window, and which citation numbers map to which
document/chunk.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts.registry import get_prompt
from app.ai.providers.factory import build_llm_provider
from app.ai.rag.retriever import Retriever
from app.ai.tracing import RunRecorder, truncate_for_prompt
from app.ai.types import ChatMessage, RetrievedChunk, estimate_tokens
from app.core.config import settings
from app.core.errors import AIError, AIResponseFormatError
from app.core.logging import get_logger
from app.domain.enums import RunStatus, RunType

logger = get_logger(__name__)

_CITATION = re.compile(r"\[(\d{1,2})\]")
_CONTEXT_BUDGET_TOKENS = 6000


@dataclass(slots=True)
class RAGAnswer:
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    used_citation_ids: list[int] = field(default_factory=list)
    retrieved: list[RetrievedChunk] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    retrieval_meta: dict[str, Any] = field(default_factory=dict)
    grounded: bool = False
    provider: str = ""
    model: str = ""
    run_id: str = ""
    latency_ms: int = 0


class RAGPipeline:
    def __init__(self, retriever: Retriever | None = None) -> None:
        self.retriever = retriever or Retriever()
        self.llm, self.llm_info = build_llm_provider()

    # ---------------------------------------------------------------- rewrite --
    async def _rewrite_query(
        self, session: AsyncSession, question: str
    ) -> list[str]:
        """Expand the question into retrieval-friendly variants.

        A failure here is *not* fatal: we simply fall back to the raw question.
        """
        try:
            prompt = await get_prompt(session, "query_rewrite")
            rendered = prompt.render(question=question)
            result = await self.llm.chat(
                [
                    ChatMessage(role="system", content=rendered),
                    ChatMessage(role="user", content=question),
                ],
                temperature=0.0,
                max_tokens=256,
                json_mode=True,
            )
            payload = _loads(result.content)
            queries = [q for q in payload.get("queries", []) if isinstance(q, str) and q.strip()]
            if question not in queries:
                queries.insert(0, question)
            return queries[:3] or [question]
        except (AIError, ValueError, TypeError) as exc:
            logger.info("query_rewrite_skipped", extra={"error": f"{type(exc).__name__}: {exc}"})
            return [question]

    # -------------------------------------------------------------- assemble ---
    @staticmethod
    def _assemble_context(chunks: list[RetrievedChunk]) -> tuple[str, list[RetrievedChunk]]:
        """Number the sources and fit them into the context budget."""
        parts: list[str] = []
        used: list[RetrievedChunk] = []
        budget = _CONTEXT_BUDGET_TOKENS
        for index, chunk in enumerate(chunks, start=1):
            body = truncate_for_prompt(chunk.content, max(400, budget))
            cost = estimate_tokens(body) + 30
            if cost > budget and used:
                break
            label = f"【资料 {index}】来源：{chunk.document_title}"
            if chunk.heading:
                label += f" ／ {chunk.heading}"
            label += f"（片段 #{chunk.chunk_index}）"
            parts.append(f"{label}\n{body}")
            used.append(chunk)
            budget -= cost
            if budget <= 200:
                break
        return "\n\n---\n\n".join(parts), used

    # ------------------------------------------------------------------- run ---
    async def answer(
        self,
        session: AsyncSession,
        *,
        question: str,
        owner_id: uuid.UUID,
        knowledge_base_ids: list[uuid.UUID] | None = None,
        document_ids: list[uuid.UUID] | None = None,
        mode: str = "hybrid",
        top_k: int | None = None,
        history: list[dict[str, str]] | None = None,
        project_id: uuid.UUID | None = None,
    ) -> RAGAnswer:
        question = (question or "").strip()
        if not question:
            raise AIError("Question must not be empty", status_code=422)

        prompt = await get_prompt(session, "rag_answer")
        recorder = await RunRecorder.start(
            session,
            run_type=RunType.RAG_ANSWER,
            owner_id=owner_id,
            provider=self.llm_info.kind,
            model=self.llm.model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            request_payload={
                "question": question,
                "knowledge_base_ids": [str(k) for k in (knowledge_base_ids or [])],
                "mode": mode,
                "top_k": top_k,
            },
            project_id=project_id,
            knowledge_base_id=knowledge_base_ids[0] if knowledge_base_ids else None,
        )

        try:
            queries = await self._rewrite_query(session, question)

            merged: dict[str, RetrievedChunk] = {}
            dense = sparse = 0
            took_ms = 0
            for query in queries:
                result = await self.retriever.retrieve(
                    session,
                    query=query,
                    knowledge_base_ids=knowledge_base_ids,
                    document_ids=document_ids,
                    mode=mode,
                    top_k=top_k,
                )
                dense += result.dense_hits
                sparse += result.sparse_hits
                took_ms += result.took_ms
                for chunk in result.chunks:
                    existing = merged.get(chunk.chunk_id)
                    # Keep the best score seen for a chunk across query variants.
                    if existing is None or chunk.score > existing.score:
                        merged[chunk.chunk_id] = chunk

            ranked = sorted(merged.values(), key=lambda c: -c.score)
            context, used = self._assemble_context(ranked)

            retrieval_meta = {
                "mode": mode,
                "queries": queries,
                "dense_hits": dense,
                "sparse_hits": sparse,
                "unique_chunks": len(merged),
                "used_chunks": len(used),
                "context_tokens": estimate_tokens(context),
                "retrieval_ms": took_ms,
            }
            recorder.set_retrieval(retrieval_meta, len(used))

            if not used:
                answer_text = (
                    "知识库中没有检索到与该问题相关的资料。\n\n"
                    "建议：确认文档已完成解析与向量化（状态为 COMPLETED），"
                    "或换用更具体的关键词重新提问。"
                )
                await recorder.finish(
                    status=RunStatus.SUCCEEDED,
                    response_text=answer_text,
                    result_payload={"grounded": False, "citations": []},
                )
                return RAGAnswer(
                    answer=answer_text,
                    retrieved=[],
                    queries=queries,
                    retrieval_meta=retrieval_meta,
                    grounded=False,
                    provider=self.llm_info.kind,
                    model=self.llm.model,
                    run_id=recorder.run_id,
                    latency_ms=recorder.model_row.latency_ms,
                )

            history_text = ""
            if history:
                history_text = "\n".join(
                    f"{'用户' if h.get('role') == 'user' else '助手'}: {h.get('content', '')[:300]}"
                    for h in history[-4:]
                ) or "（无）"
            else:
                history_text = "（无）"

            rendered = prompt.render(
                context=context, question=question, history=history_text
            )
            chat = await self.llm.chat(
                [
                    ChatMessage(role="system", content=rendered),
                    ChatMessage(role="user", content=question),
                ],
                temperature=settings.ai_temperature,
                max_tokens=settings.ai_max_tokens,
            )
            recorder.record_usage(chat.usage)

            citations = [chunk.to_citation(i) for i, chunk in enumerate(used, start=1)]
            used_ids = sorted(
                {int(m) for m in _CITATION.findall(chat.content) if 1 <= int(m) <= len(used)}
            )
            grounded = bool(used_ids)
            if not grounded:
                # The model produced prose without markers: append the source list
                # so the answer is still traceable instead of silently ungrounded.
                chat.content = (
                    chat.content.rstrip()
                    + "\n\n> 说明：模型未在正文中标注引用编号，以下为本次检索使用的资料。"
                )

            await recorder.add_sources(
                [
                    {
                        **citation,
                        "document_title": used[citation["index"] - 1].document_title,
                    }
                    for citation in citations
                ]
            )
            await recorder.finish(
                status=RunStatus.SUCCEEDED,
                response_text=chat.content,
                result_payload={
                    "grounded": grounded,
                    "citations": citations,
                    "used_citation_ids": used_ids,
                },
                chat_result=chat,
            )

            return RAGAnswer(
                answer=chat.content,
                citations=citations,
                used_citation_ids=used_ids,
                retrieved=used,
                queries=queries,
                retrieval_meta=retrieval_meta,
                grounded=grounded,
                provider=chat.provider,
                model=chat.model,
                run_id=recorder.run_id,
                latency_ms=chat.latency_ms,
            )

        except AIError as exc:
            await recorder.finish(
                status=RunStatus.TIMEOUT if "timeout" in exc.code else RunStatus.FAILED,
                error_code=exc.code,
                error_message=exc.message,
            )
            raise
        except Exception as exc:
            await recorder.finish(
                status=RunStatus.FAILED,
                error_code="unexpected_error",
                error_message=f"{type(exc).__name__}: {exc}",
            )
            raise


def _close_open_structures(fragment: str) -> str | None:
    """Close an unterminated string and every open bracket/brace."""
    # Drop a trailing partial token, e.g. `"title": "通读并整理 `.
    if fragment.count('"') % 2 == 1:
        cut = fragment.rfind('"')
        if cut < 0:
            return None
        fragment = fragment[:cut]

    stack: list[str] = []
    in_string = False
    escaped = False
    for char in fragment:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]" and stack:
            stack.pop()

    repaired = fragment.rstrip()
    # A dangling separator or key (`{"title":` / `[{},`) has no value to close.
    while repaired and repaired[-1] in ",:[":
        repaired = repaired[:-1].rstrip()
    if not repaired:
        return None
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    return repaired


def _repair_truncated_json(text: str) -> dict[str, Any] | None:
    """Best-effort recovery of a JSON object cut off by the token ceiling.

    Reasoning models spend part of their budget thinking, so a long structured
    answer can hit ``max_tokens`` mid-object. Recovering the complete part is far
    more useful than a hard failure: the planner can normalise a plan with three
    finished phases instead of none.

    The algorithm closes the open structures; if that still does not parse, it
    trims back to the previous structural boundary and tries again, bounded to a
    fixed number of attempts so a pathological input cannot spin.
    """
    start = text.find("{")
    if start < 0:
        return None
    fragment = text[start:]

    for _ in range(80):
        candidate = _close_open_structures(fragment)
        if candidate:
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                return data

        cut = max(fragment.rfind(","), fragment.rfind("["), fragment.rfind("{"))
        if cut <= 0:
            return None
        fragment = fragment[:cut]
    return None


def load_json_object(text: str) -> dict[str, Any]:
    """Tolerant JSON extraction.

    Models wrap JSON in prose or fences, and reasoning models can be cut off by
    the token ceiling. Three escalating strategies are applied before giving up:
    strip fences → locate the outermost object → repair a truncated object.
    """
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)

    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            pass

    repaired = _repair_truncated_json(cleaned)
    if repaired is not None:
        logger.warning(
            "json_repaired_after_truncation",
            extra={"recovered_keys": sorted(repaired)[:12], "chars": len(cleaned)},
        )
        return repaired

    raise AIResponseFormatError(
        "Model returned content that is not valid JSON",
        details={"preview": cleaned[:400], "length": len(cleaned)},
    ) from None


# Backwards-compatible private alias used internally.
_loads = load_json_object


async def request_structured(
    provider: Any,
    messages: Sequence[ChatMessage],
    *,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    attempts: int = 3,
) -> tuple[dict[str, Any], Any]:
    """Call the model and parse JSON, retrying when the model returns prose.

    The HTTP retry policy in :mod:`app.core.retry` cannot help here: the request
    succeeds with HTTP 200 and it is the *content* that is unusable. Language
    models occasionally answer a JSON request with a paragraph, or get cut off
    mid-object in a way the repair pass cannot salvage.

    Observed in practice against DeepSeek: the same document analysis request
    failed roughly one time in five with "Model returned content that is not
    valid JSON" and succeeded on an immediate retry. Asking again is far better
    than surfacing a 502 for something that is transient by nature.

    Returns ``(payload, chat_result)`` so the caller can still record tokens and
    latency from the attempt that actually produced the answer.
    """
    last_error: AIResponseFormatError | None = None

    for attempt in range(1, max(1, attempts) + 1):
        chat = await provider.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        try:
            return load_json_object(chat.content), chat
        except AIResponseFormatError as exc:
            last_error = exc
            logger.warning(
                "structured_output_unparseable",
                extra={
                    "attempt": attempt,
                    "attempts": attempts,
                    "finish_reason": chat.finish_reason,
                    "chars": len(chat.content or ""),
                    "preview": (chat.content or "")[:160],
                },
            )
            if attempt >= attempts:
                break

    assert last_error is not None
    raise last_error

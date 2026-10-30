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
from app.ai.providers.budget import reasoning_budget
from app.ai.providers.factory import build_llm_provider
from app.ai.rag.retriever import Retriever
from app.ai.tracing import RunRecorder, persist_terminal_record, truncate_for_prompt
from app.ai.types import ChatMessage, RetrievedChunk, estimate_tokens
from app.core.config import settings
from app.core.errors import AIError, AIResponseFormatError
from app.core.logging import get_logger
from app.domain.enums import RunStatus, RunType

logger = get_logger(__name__)

_CITATION = re.compile(r"\[(\d{1,2})\]")
_CONTEXT_BUDGET_TOKENS = 6000

#: Sentence/paragraph boundaries used to split an answer into claim-bearing units.
_CLAIM_BOUNDARY = re.compile(r"[。！？!?\n]+")
#: A unit shorter than this is a fragment ("见上", "1."), not a claim.
_MIN_CLAIM_CHARS = 8


@dataclass(slots=True)
class CitationReport:
    """What the program actually verified about an answer's citations.

    Three different things were being conflated, and they are kept apart here:

    * ``retrieved_sources`` — what the search returned and the model was shown.
    * ``used_citations`` — the numbers the answer's text actually refers to.
    * ``grounded`` — what *this program* established, which is structural: every
      claim-bearing sentence carries a marker that resolves to a real source.

    ``grounded`` is **not** a claim that each sentence is entailed by its source. That
    would need fact-level verification, which this pipeline does not do, and the reason
    string says so rather than leaving the reader to infer a stronger guarantee.
    """

    source_count: int
    valid_ids: list[int]
    invalid_ids: list[int]
    claim_units: int
    supported_units: int
    grounded: bool
    reason: str

    @property
    def coverage(self) -> float:
        """Fraction of claim-bearing units that carry a valid citation."""
        if not self.claim_units:
            return 0.0
        return round(self.supported_units / self.claim_units, 4)

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_count": self.source_count,
            "valid_citation_ids": self.valid_ids,
            "invalid_citation_ids": self.invalid_ids,
            "claim_units": self.claim_units,
            "supported_units": self.supported_units,
            "coverage": self.coverage,
            "grounded": self.grounded,
            "reason": self.reason,
        }


def analyse_citations(answer: str, source_count: int) -> CitationReport:
    """Decide whether every claim in ``answer`` is backed by a retrieved source.

    The previous rule was ``grounded = bool(valid_ids)``: **one** valid number anywhere
    made the whole answer grounded, including the paragraphs around it that cited
    nothing. The product's own README then called the result "citation-enforced", which
    was never true of the code.

    The check here is structural, not semantic. A sentence is *supported* when it carries
    at least one marker resolving to a real source. Whether that source entails the
    sentence is a different question and this function does not answer it.
    """
    text = answer or ""
    markers = [int(m) for m in _CITATION.findall(text)]
    valid = sorted({m for m in markers if 1 <= m <= source_count})
    invalid = sorted({m for m in markers if m < 1 or m > source_count})

    units = [u.strip() for u in _CLAIM_BOUNDARY.split(text)]
    units = [u for u in units if len(u) >= _MIN_CLAIM_CHARS]

    def is_supported(unit: str) -> bool:
        return any(1 <= int(m) <= source_count for m in _CITATION.findall(unit))

    supported_units = sum(1 for u in units if is_supported(u))

    if source_count == 0:
        reason = "没有检索到任何资料，无从验证"
    elif not markers:
        reason = "回答正文没有任何引用编号"
    elif invalid and not valid:
        reason = f"引用编号全部越界（{invalid}），可用范围是 1..{source_count}"
    elif not units:
        reason = "回答没有可判定的结论句"
    elif supported_units < len(units):
        reason = f"只有 {supported_units}/{len(units)} 条结论带有效引用"
    else:
        reason = ""

    grounded = bool(
        source_count > 0 and units and not invalid and valid and supported_units == len(units)
    )
    return CitationReport(
        source_count=source_count,
        valid_ids=valid,
        invalid_ids=invalid,
        claim_units=len(units),
        supported_units=supported_units,
        grounded=grounded,
        reason=reason,
    )


@dataclass(slots=True)
class RAGAnswer:
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    used_citation_ids: list[int] = field(default_factory=list)
    invalid_citation_ids: list[int] = field(default_factory=list)
    retrieved: list[RetrievedChunk] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    retrieval_meta: dict[str, Any] = field(default_factory=dict)
    #: True only when `analyse_citations` found every claim-bearing unit cited.
    grounded: bool = False
    #: The detail behind `grounded`, including why it is false.
    citation_report: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    run_id: str = ""
    latency_ms: int = 0


class RAGPipeline:
    def __init__(self, retriever: Retriever | None = None) -> None:
        self.retriever = retriever or Retriever()
        self.llm, self.llm_info = build_llm_provider()

    # ---------------------------------------------------------------- rewrite --
    async def _rewrite_query(self, session: AsyncSession, question: str) -> list[str]:
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
                # Same cap, same reason: a 256-token budget is eaten by the thinking
                # phase, and the failure surfaces as "no queries rewritten" rather than
                # as an error.
                max_tokens=reasoning_budget(),
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
        #: Personal context block assembled by ContextAssembler. Prepended to the
        #: retrieved material so the answer can reflect who is asking without the
        #: caller having to know how that block is built.
        extra_context: str = "",
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
                history_text = (
                    "\n".join(
                        f"{'用户' if h.get('role') == 'user' else '助手'}: {h.get('content', '')[:300]}"
                        for h in history[-4:]
                    )
                    or "（无）"
                )
            else:
                history_text = "（无）"

            rendered = prompt.render(
                context=(f"{extra_context}\n\n{context}" if extra_context.strip() else context),
                question=question,
                history=history_text,
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
            report = analyse_citations(chat.content, len(used))

            # §9.2: a missing or broken citation is retried **once**, with the
            # requirement restated, rather than papered over by appending a source list.
            # One retry, not a loop: a model that cannot cite on the second attempt will
            # not cite on the fifth, and each attempt costs the user money.
            #
            # Not attempted for the offline engine. It is a deterministic extractor that
            # strips the source labels rather than citing them, so asking again produces
            # the same answer at the cost of a second pass — a retry that cannot change
            # its own outcome is just latency.
            if not report.grounded and self.llm_info.kind != "offline":
                retry = await self.llm.chat(
                    [
                        ChatMessage(role="system", content=rendered),
                        ChatMessage(role="user", content=question),
                        ChatMessage(role="assistant", content=chat.content),
                        ChatMessage(
                            role="user",
                            content=(
                                "上面的回答没有被接受：每一条结论都必须带上资料编号。"
                                f"请重写，编号只能使用 1 到 {len(used)} 之间的数字，"
                                "不要引用范围外的编号，也不要编造资料里没有的内容。"
                            ),
                        ),
                    ],
                    temperature=settings.ai_temperature,
                    max_tokens=settings.ai_max_tokens,
                )
                recorder.record_usage(retry.usage)
                retry_report = analyse_citations(retry.content, len(used))
                if retry_report.grounded:
                    chat = retry
                    report = retry_report

            grounded = report.grounded
            if not grounded:
                # Stated, not hidden. The answer is still returned — it may well be
                # right — but it is labelled as unverified, and the reason is given.
                chat.content = (
                    chat.content.rstrip()
                    + "\n\n> ⚠️ 未验证：本次回答没有被程序证实为逐条有据。"
                    + (f"原因：{report.reason}。" if report.reason else "")
                    + "\n> 下面列出本次检索到的资料，请自行核对。"
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
                    "used_citation_ids": report.valid_ids,
                    "citation_report": report.to_payload(),
                },
                chat_result=chat,
            )

            return RAGAnswer(
                answer=chat.content,
                citations=citations,
                used_citation_ids=report.valid_ids,
                invalid_citation_ids=report.invalid_ids,
                retrieved=used,
                queries=queries,
                retrieval_meta=retrieval_meta,
                grounded=grounded,
                citation_report=report.to_payload(),
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
            # Without this the caller's rollback discards the record of a failed answer,
            # which is the case the tracing exists for.
            await persist_terminal_record(session, context=f"rag:{exc.code}")
            raise
        except Exception as exc:
            await recorder.finish(
                status=RunStatus.FAILED,
                error_code="unexpected_error",
                error_message=f"{type(exc).__name__}: {exc}",
            )
            await persist_terminal_record(session, context="rag:unexpected_error")
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

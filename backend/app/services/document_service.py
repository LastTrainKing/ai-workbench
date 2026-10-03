"""Document ingestion and analysis service.

Ingestion pipeline (every stage is persisted, so progress survives a restart)::

    UPLOAD → VALIDATE → STORE → PARSE → NORMALISE → CHUNK → PERSIST CHUNKS
           → EMBED (batched) → UPSERT VECTORS → COMPLETED
                                        ↘ FAILED (with a human-readable reason)

The heavy half runs in the background worker (see ``worker/runner.py``), never
inside the HTTP request, so uploading a 20 MB PDF returns immediately.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.parsers.documents import ParsedDocument, parse_file
from app.ai.prompts.registry import get_prompt
from app.ai.providers.factory import build_llm_provider
from app.ai.rag.chunker import chunk_text
from app.ai.rag.embedding import EmbeddingService
from app.ai.rag.pipeline import load_json_object as _loads
from app.ai.rag.pipeline import request_structured
from app.ai.tracing import RunRecorder
from app.ai.types import ChatMessage, estimate_tokens
from app.core.config import settings
from app.core.errors import AppError, DocumentParseError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.enums import (
    DocumentStatus,
    EmbeddingStatus,
    JobType,
    RunStatus,
    RunType,
)
from app.infrastructure.files import delete_file
from app.infrastructure.vector_store import VectorPoint, get_vector_store
from app.models.knowledge import Concept, Document, DocumentChunk
from app.repositories.ai_repo import ActivityRepository, JobRepository
from app.repositories.knowledge_repo import (
    ConceptRepository,
    DocumentChunkRepository,
    DocumentRepository,
    KnowledgeBaseRepository,
    TagRepository,
)
from app.repositories.work_repo import NoteRepository

logger = get_logger(__name__)

ANALYSIS_ACTIONS = {
    "summary",
    "concepts",
    "todos",
    "terms",
    "study_plan",
    "interview",
    "note",
    "cards",
}


class DocumentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.documents = DocumentRepository(session)
        self.chunks = DocumentChunkRepository(session)
        self.bases = KnowledgeBaseRepository(session)
        self.tags = TagRepository(session)
        self.concepts = ConceptRepository(session)
        self.jobs = JobRepository(session)
        self.notes = NoteRepository(session)
        self.activity = ActivityRepository(session)

    # =====================================================================
    # Registration (runs inside the request)
    # =====================================================================
    async def register_upload(
        self,
        *,
        owner_id: uuid.UUID,
        knowledge_base_id: uuid.UUID,
        stored,  # app.infrastructure.files.StoredFile
        title: str | None = None,
        project_id: uuid.UUID | None = None,
    ) -> tuple[Document, Any, bool]:
        """Create the Document row and enqueue the ingestion job."""
        kb = await self.bases.get_owned(knowledge_base_id, owner_id)
        if kb is None:
            raise NotFoundError("Knowledge base not found")

        duplicate = await self.documents.find_duplicate(kb.id, stored.content_hash)
        if duplicate is not None:
            delete_file(stored.path)
            logger.info(
                "duplicate_upload_skipped",
                extra={"document_id": str(duplicate.id), "hash": stored.content_hash[:12]},
            )
            return duplicate, None, True

        document = Document(
            owner_id=owner_id,
            knowledge_base_id=kb.id,
            project_id=project_id or kb.project_id,
            title=(title or stored.safe_name)[:300],
            original_filename=stored.original_name[:300],
            stored_path=str(stored.path),
            mime_type="",
            file_ext=stored.suffix,
            size_bytes=stored.size_bytes,
            content_hash=stored.content_hash,
            status=DocumentStatus.PENDING.value,
            progress=0,
            doc_metadata={"uploaded_filename": stored.original_name},
        )
        self.session.add(document)
        await self.session.flush()

        job = await self.jobs.enqueue(
            job_type=JobType.INGEST_DOCUMENT.value,
            payload={"document_id": str(document.id), "owner_id": str(owner_id)},
            owner_id=owner_id,
            priority=50,
            max_attempts=settings.worker_max_attempts,
        )
        await self.activity.record(
            owner_id=owner_id,
            event_type="document.uploaded",
            title=f"上传文档：{document.title}",
            entity_type="document",
            entity_id=str(document.id),
            project_id=document.project_id,
            metadata={"size_bytes": document.size_bytes, "job_id": str(job.id)},
        )
        logger.info(
            "document_registered",
            extra={
                "document_id": str(document.id),
                "job_id": str(job.id),
                "size_bytes": document.size_bytes,
            },
        )
        return document, job, False

    # =====================================================================
    # Ingestion pipeline (runs inside the worker)
    # =====================================================================
    async def ingest(self, document_id: uuid.UUID, progress=None) -> dict[str, Any]:
        document = await self.documents.get(document_id)
        if document is None:
            raise NotFoundError(f"Document {document_id} no longer exists")
        # Held as a plain UUID: ``_record_failure`` rolls the session back, which
        # expires ORM instances, so failure handling must not read attributes.
        doc_id = document.id

        async def report(percent: int, stage: str) -> None:
            document.progress = max(0, min(100, percent))
            document.doc_metadata = {**(document.doc_metadata or {}), "stage": stage}
            await self.session.flush()
            logger.info(
                "ingest_stage",
                extra={"document_id": str(document.id), "percent": percent, "stage": stage},
            )
            if progress:
                await progress(percent, stage)

        document.status = DocumentStatus.PROCESSING.value
        document.error_message = ""
        await report(5, "PARSING")

        from pathlib import Path

        path = Path(document.stored_path)
        try:
            parsed: ParsedDocument = parse_file(path)
        except AppError as exc:
            await self._record_failure(doc_id, f"{exc.code}: {exc.message}")
            raise

        document.language = parsed.language
        document.page_count = parsed.page_count
        document.char_count = len(parsed.text)
        document.title = (document.title or parsed.title)[:300] or parsed.title
        document.doc_metadata = {
            **(document.doc_metadata or {}),
            **{str(k): v for k, v in parsed.metadata.items()},
            "warnings": parsed.warnings,
        }
        await report(25, "CHUNKING")

        pieces = chunk_text(
            parsed.text,
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
        )
        if not pieces:
            await self._record_failure(
                doc_id, "document_parse_error: 分块后没有任何有效内容"
            )
            raise DocumentParseError("Chunking produced no content")

        # Re-ingesting the same document must not duplicate chunks or vectors.
        await self.session.execute(
            DocumentChunk.__table__.delete().where(DocumentChunk.document_id == document.id)
        )
        await self.session.flush()
        try:
            await get_vector_store().delete_by_document(document.id)
        except Exception as exc:
            logger.warning(
                "vector_delete_on_reingest_failed",
                extra={"document_id": str(document.id), "error": str(exc)},
            )

        rows: list[DocumentChunk] = []
        for piece in pieces:
            row = DocumentChunk(
                document_id=document.id,
                knowledge_base_id=document.knowledge_base_id,
                chunk_index=piece.index,
                content=piece.content,
                heading=piece.heading[:300],
                char_count=piece.char_count,
                token_estimate=piece.token_estimate,
                embedding_status=EmbeddingStatus.PENDING.value,
            )
            self.session.add(row)
            rows.append(row)
        await self.session.flush()
        document.chunk_count = len(rows)
        document.embedded_chunk_count = 0
        await report(45, "EMBEDDING")

        embedder = EmbeddingService()
        store = get_vector_store()
        embedded = 0
        failed = 0
        batch_size = max(1, settings.embedding_batch_size)

        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            try:
                result = await embedder.embed_texts([row.content for row in batch])
            except AppError as exc:
                for row in batch:
                    row.embedding_status = EmbeddingStatus.FAILED.value
                    row.embedding_error = f"{exc.code}: {exc.message}"[:2000]
                failed += len(batch)
                logger.warning(
                    "embedding_batch_failed",
                    extra={
                        "document_id": str(document.id),
                        "batch_start": start,
                        "error": f"{exc.code}: {exc.message}",
                    },
                )
                await self.session.flush()
                continue

            points: list[VectorPoint] = []
            for row, vector in zip(batch, result.vectors, strict=True):
                point_id = str(uuid.uuid4())
                row.vector_id = point_id
                row.embedding_status = EmbeddingStatus.EMBEDDED.value
                row.embedding_model = result.model
                row.embedding_error = ""
                points.append(
                    VectorPoint(
                        id=point_id,
                        vector=vector,
                        payload={
                            "chunk_id": str(row.id),
                            "document_id": str(document.id),
                            "knowledge_base_id": str(document.knowledge_base_id),
                            "owner_id": str(document.owner_id),
                            "chunk_index": row.chunk_index,
                            "heading": row.heading,
                            "document_title": document.title,
                            "content": row.content[:1200],
                            "embedding_model": result.model,
                        },
                    )
                )
            try:
                await store.upsert(points)
                embedded += len(points)
            except AppError as exc:
                for row in batch:
                    row.embedding_status = EmbeddingStatus.FAILED.value
                    row.embedding_error = f"{exc.code}: {exc.message}"[:2000]
                failed += len(batch)
                logger.warning(
                    "vector_upsert_failed",
                    extra={"document_id": str(document.id), "error": str(exc)},
                )
            await self.session.flush()

            percent = 45 + int(50 * min(1.0, (start + len(batch)) / len(rows)))
            await report(percent, "EMBEDDING")

        document.embedded_chunk_count = embedded
        document.processed_at = datetime.now(UTC)

        if embedded == 0:
            await self._record_failure(
                doc_id,
                "embedding_error: 所有分块都未能生成向量，请检查 Embedding Provider 配置",
            )
            raise AppError(
                "embedding_error: 所有分块都未能生成向量，请检查 Embedding Provider 配置",
                code="embedding_error",
                status_code=502,
            )

        if failed:
            # Partial success is surfaced, not hidden: the document is usable but
            # the operator can see exactly how many chunks are missing vectors.
            document.status = DocumentStatus.COMPLETED.value
            document.error_message = f"部分分块向量化失败（{failed}/{len(rows)}）"
        else:
            document.status = DocumentStatus.COMPLETED.value
            document.error_message = ""

        # Keep the knowledge base embedding model/dimension in sync for the UI.
        kb = await self.bases.get(document.knowledge_base_id)
        if kb is not None and embedder.dimension:
            kb.embedding_model = embedder.model
            kb.vector_size = embedder.dimension

        await report(100, "COMPLETED")
        await self.session.flush()

        await self.activity.record(
            owner_id=document.owner_id,
            event_type="document.ingested",
            title=f"文档解析完成：{document.title}（{embedded} 个分块）",
            entity_type="document",
            entity_id=str(document.id),
            project_id=document.project_id,
            metadata={
                "chunks": len(rows),
                "embedded": embedded,
                "failed": failed,
                "model": embedder.model,
            },
        )
        logger.info(
            "document_ingested",
            extra={
                "document_id": str(document.id),
                "chunks": len(rows),
                "embedded": embedded,
                "failed": failed,
            },
        )
        return {
            "document_id": str(document.id),
            "chunks": len(rows),
            "embedded": embedded,
            "failed": failed,
            "model": embedder.model,
            "dimension": embedder.dimension,
        }

    async def _record_failure(self, document_id: uuid.UUID, message: str) -> None:
        """Persist a FAILED status in its **own** transaction.

        Two problems have to be solved at once here:

        1. The ingestion pipeline runs inside the caller's transaction, which is
           rolled back when it raises. Writing the failure state on that same
           session would discard it, leaving the document stuck in
           ``PROCESSING`` forever with no explanation.
        2. Writing it from a *second* connection while the first still holds the
           row lock would self-deadlock: the new UPDATE waits for a lock the
           doomed transaction never releases.

        So we first roll the aborted transaction back (releasing its locks — the
        partial work is worthless anyway), and only then record the failure
        through an independent session.
        """
        from app.infrastructure.database import session_scope
        from app.repositories.knowledge_repo import DocumentRepository

        try:
            await self.session.rollback()
        except Exception as exc:
            logger.warning(
                "ingest_rollback_failed",
                extra={"document_id": str(document_id), "error": str(exc)},
            )

        try:
            async with session_scope() as session:
                document = await DocumentRepository(session).get(document_id)
                if document is None or document.status == DocumentStatus.COMPLETED.value:
                    return
                document.status = DocumentStatus.FAILED.value
                document.error_message = message[:2000]
                document.progress = 0
        except Exception as exc:
            logger.error(
                "failed_to_record_document_failure",
                extra={"document_id": str(document_id), "error": str(exc)},
            )

    # =====================================================================
    # Queries
    # =====================================================================
    async def get(self, *, owner_id: uuid.UUID, document_id: uuid.UUID) -> Document:
        document = await self.documents.get_owned(document_id, owner_id)
        if document is None:
            raise NotFoundError("Document not found")
        return document

    async def detail(self, *, owner_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, Any]:
        document = await self.get(owner_id=owner_id, document_id=document_id)
        job = await self.jobs.latest_for_document(document.id)
        return {
            "document": document,
            "job_status": job.status if job else None,
            "job_stage": job.stage if job else None,
            "job_error": job.error_message if job else None,
            "embedding_progress": await self.chunks.embedding_progress(document.id),
            "tags": list(document.tags),
        }

    async def content(self, *, owner_id: uuid.UUID, document_id: uuid.UUID, limit: int = 40_000) -> dict[str, Any]:
        document = await self.get(owner_id=owner_id, document_id=document_id)
        rows = await self.chunks.for_document(document.id)
        text = "\n\n".join(chunk.content for chunk in rows)
        truncated = len(text) > limit
        return {
            "document_id": str(document.id),
            "title": document.title,
            "content": text[:limit],
            "char_count": len(text),
            "truncated": truncated,
        }

    async def delete(self, *, owner_id: uuid.UUID, document_id: uuid.UUID) -> None:
        document = await self.get(owner_id=owner_id, document_id=document_id)
        title = document.title
        path = document.stored_path
        try:
            await get_vector_store().delete_by_document(document.id)
        except Exception as exc:
            logger.warning(
                "vector_delete_failed",
                extra={"document_id": str(document.id), "error": str(exc)},
            )
        await self.session.delete(document)
        await self.session.flush()
        delete_file(path)
        await self.activity.record(
            owner_id=owner_id,
            event_type="document.deleted",
            title=f"删除文档：{title}",
            entity_type="document",
            entity_id=str(document_id),
        )

    async def reprocess(self, *, owner_id: uuid.UUID, document_id: uuid.UUID) -> Any:
        document = await self.get(owner_id=owner_id, document_id=document_id)
        document.status = DocumentStatus.PENDING.value
        document.progress = 0
        document.error_message = ""
        await self.session.flush()
        job = await self.jobs.enqueue(
            job_type=JobType.INGEST_DOCUMENT.value,
            payload={"document_id": str(document.id), "owner_id": str(owner_id)},
            owner_id=owner_id,
            priority=40,
            max_attempts=settings.worker_max_attempts,
        )
        return job

    async def update_metadata(
        self, *, owner_id: uuid.UUID, document_id: uuid.UUID, **changes: Any
    ) -> Document:
        document = await self.get(owner_id=owner_id, document_id=document_id)
        if changes.get("title"):
            document.title = str(changes["title"]).strip()[:300]
        if "project_id" in changes:
            document.project_id = changes["project_id"]
        if changes.get("tag_names") is not None:
            names = [str(n).strip() for n in changes["tag_names"] if str(n).strip()][:12]
            tags = [await self.tags.get_or_create(owner_id, name) for name in names]
            await self.session.flush()
            document.tags = tags
        await self.session.flush()
        return document

    # =====================================================================
    # AI analysis workflows
    # =====================================================================
    async def analyze(
        self,
        *,
        owner_id: uuid.UUID,
        document_id: uuid.UUID,
        actions: list[str],
        save_note: bool = True,
        project_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        document = await self.get(owner_id=owner_id, document_id=document_id)
        if document.status != DocumentStatus.COMPLETED.value:
            raise ValidationError(
                "Document must finish ingestion before it can be analysed",
                details={"status": document.status},
            )

        wanted = [a for a in actions if a in ANALYSIS_ACTIONS] or ["summary"]
        llm, info = build_llm_provider()
        content = await self._document_text(document, max_tokens=7000)

        recorder = await RunRecorder.start(
            self.session,
            run_type=RunType.DOCUMENT_SUMMARY if wanted == ["summary"] else RunType.KNOWLEDGE_EXTRACTION,
            owner_id=owner_id,
            provider=info.kind,
            model=llm.model,
            request_payload={"document_id": str(document.id), "actions": wanted},
            project_id=project_id or document.project_id,
            knowledge_base_id=document.knowledge_base_id,
        )

        result: dict[str, Any] = {"document_id": str(document.id), "actions": wanted}
        note_ids: list[str] = []

        try:
            if "summary" in wanted or "note" in wanted or "cards" in wanted:
                prompt = await get_prompt(self.session, "document_summary")
                recorder.model_row.prompt_name = prompt.name
                recorder.model_row.prompt_version = prompt.version
                # Retries the *parse*, not the HTTP call: the model sometimes
                # answers a JSON request with prose, and asking again works.
                payload, chat = await request_structured(
                    llm,
                    [
                        ChatMessage(
                            role="system",
                            content=prompt.render(title=document.title, content=content),
                        ),
                        ChatMessage(role="user", content=f"请分析文档《{document.title}》。"),
                    ],
                    temperature=0.2,
                    max_tokens=settings.ai_max_tokens,
                )
                recorder.record_usage(chat.usage)
                result["summary"] = payload.get("summary", "")
                result["key_points"] = payload.get("key_points", [])
                result["keywords"] = payload.get("keywords", [])
                result["difficulty"] = payload.get("difficulty", "")
                result["audience"] = payload.get("audience", "")
                if result["summary"]:
                    document.summary = str(result["summary"])[:4000]

            if any(a in wanted for a in ("concepts", "todos", "terms")):
                prompt = await get_prompt(self.session, "knowledge_extraction")
                recorder.model_row.prompt_name = prompt.name
                recorder.model_row.prompt_version = prompt.version
                chat = await llm.chat(
                    [
                        ChatMessage(
                            role="system",
                            content=prompt.render(title=document.title, content=content),
                        ),
                        ChatMessage(role="user", content="请抽取结构化知识。"),
                    ],
                    temperature=0.1,
                    max_tokens=settings.ai_max_tokens,
                    json_mode=True,
                )
                recorder.record_usage(chat.usage)
                extracted = _loads(chat.content)
                result["concepts"] = extracted.get("concepts", [])
                result["key_points"] = extracted.get("key_points", result.get("key_points", []))
                result["terms"] = extracted.get("terms", [])
                result["todos"] = extracted.get("todos", [])
                result["outline"] = extracted.get("outline", [])
                await self._persist_concepts(
                    owner_id=owner_id, document=document, names=result["concepts"]
                )
                result["linked_concepts"] = len(result["concepts"])

            if "interview" in wanted:
                prompt = await get_prompt(self.session, "interview_generator")
                chat = await llm.chat(
                    [
                        ChatMessage(
                            role="system",
                            content=prompt.render(
                                title=document.title, content=content, count=10
                            ),
                        ),
                        ChatMessage(role="user", content="生成面试题。"),
                    ],
                    temperature=0.4,
                    max_tokens=settings.ai_max_tokens,
                    json_mode=True,
                )
                recorder.record_usage(chat.usage)
                result["interview"] = _loads(chat.content).get("questions", [])

            if "study_plan" in wanted:
                prompt = await get_prompt(self.session, "study_plan")
                chat = await llm.chat(
                    [
                        ChatMessage(
                            role="system",
                            content=prompt.render(
                                goal=f"掌握《{document.title}》",
                                days=7,
                                context=content[:4000],
                            ),
                        ),
                        ChatMessage(role="user", content="生成 7 天学习计划。"),
                    ],
                    temperature=0.3,
                    max_tokens=settings.ai_max_tokens,
                    json_mode=True,
                )
                recorder.record_usage(chat.usage)
                result["study_plan"] = _loads(chat.content).get("days", [])

            if "cards" in wanted:
                result["cards"] = self._build_cards(
                    document.title, result.get("terms", []), result.get("key_points", [])
                )

            if save_note and any(a in wanted for a in ("note", "summary", "cards")):
                note = await self._save_analysis_note(
                    owner_id=owner_id,
                    document=document,
                    run_id=recorder.run_id,
                    result=result,
                    project_id=project_id,
                )
                note_ids.append(str(note.id))

            await recorder.finish(
                status=RunStatus.SUCCEEDED,
                response_text=json.dumps(result, ensure_ascii=False, default=str)[:60_000],
                result_payload=result,
            )
            result["run_id"] = recorder.run_id
            result["note_ids"] = note_ids
            result["provider"] = info.kind
            result["model"] = llm.model
            result["latency_ms"] = recorder.model_row.latency_ms
            await self.activity.record(
                owner_id=owner_id,
                event_type="document.analyzed",
                title=f"AI 分析文档：{document.title}",
                entity_type="document",
                entity_id=str(document.id),
                project_id=document.project_id,
                metadata={"actions": wanted, "run_id": recorder.run_id},
            )
            return result

        except AppError as exc:
            await recorder.finish(
                status=RunStatus.FAILED, error_code=exc.code, error_message=exc.message
            )
            raise
        except Exception as exc:
            await recorder.finish(
                status=RunStatus.FAILED,
                error_code="unexpected_error",
                error_message=f"{type(exc).__name__}: {exc}",
            )
            raise

    async def generate_note(
        self,
        *,
        owner_id: uuid.UUID,
        title: str,
        topic: str,
        knowledge_base_ids: list[uuid.UUID] | None = None,
    ) -> dict[str, str]:
        """Used by the agent's ``note_create`` tool."""
        from app.ai.rag.retriever import Retriever

        retriever = Retriever()
        result = await retriever.retrieve(
            self.session,
            query=topic,
            knowledge_base_ids=knowledge_base_ids,
            top_k=6,
        )
        context = "\n\n".join(
            f"【资料 {i}】{c.document_title} / {c.heading}\n{c.content[:1500]}"
            for i, c in enumerate(result.chunks, start=1)
        ) or "（知识库中没有检索到相关资料）"

        llm, info = build_llm_provider()
        prompt = await get_prompt(self.session, "note_generation")
        recorder = await RunRecorder.start(
            self.session,
            run_type=RunType.NOTE_GENERATION,
            owner_id=owner_id,
            provider=info.kind,
            model=llm.model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            request_payload={"title": title, "topic": topic},
        )
        try:
            chat = await llm.chat(
                [
                    ChatMessage(
                        role="system", content=prompt.render(title=title, context=context)
                    ),
                    ChatMessage(role="user", content=topic),
                ],
                temperature=0.3,
                max_tokens=settings.ai_max_tokens,
            )
            recorder.record_usage(chat.usage)
            await recorder.add_sources(
                [c.to_citation(i) for i, c in enumerate(result.chunks, start=1)]
            )
            await recorder.finish(
                status=RunStatus.SUCCEEDED,
                response_text=chat.content,
                result_payload={"chars": len(chat.content)},
                chat_result=chat,
            )
            return {"content_md": chat.content, "run_id": recorder.run_id}
        except AppError as exc:
            await recorder.finish(
                status=RunStatus.FAILED, error_code=exc.code, error_message=exc.message
            )
            raise

    # ------------------------------------------------------------------ helpers
    async def _document_text(self, document: Document, *, max_tokens: int) -> str:
        rows = await self.chunks.for_document(document.id)
        budget = max_tokens
        parts: list[str] = []
        for chunk in rows:
            cost = estimate_tokens(chunk.content)
            if cost > budget and parts:
                break
            parts.append(chunk.content)
            budget -= cost
            if budget <= 200:
                break
        return "\n\n".join(parts)

    async def _persist_concepts(
        self, *, owner_id: uuid.UUID, document: Document, names: list[Any]
    ) -> None:
        clean = [str(n).strip() for n in names if isinstance(n, str) and str(n).strip()][:20]
        for name in clean:
            concept = await self.concepts.get_or_create(owner_id, name)
            concept.mention_count = (concept.mention_count or 0) + 1
            await self.concepts.link_document(document.id, concept.id)
        await self.session.flush()

    @staticmethod
    def _build_cards(
        title: str, terms: list[Any], key_points: list[Any]
    ) -> list[dict[str, str]]:
        cards: list[dict[str, str]] = []
        for term in terms[:10]:
            if isinstance(term, dict):
                cards.append(
                    {
                        "front": str(term.get("term", ""))[:200],
                        "back": str(term.get("definition", ""))[:600],
                        "source": title,
                    }
                )
        for point in key_points[:6]:
            text = str(point)
            if len(text) < 12:
                continue
            cards.append(
                {
                    "front": f"要点：{text[:60]}…" if len(text) > 60 else f"要点：{text}",
                    "back": text[:600],
                    "source": title,
                }
            )
        return cards[:16]

    async def _save_analysis_note(
        self,
        *,
        owner_id: uuid.UUID,
        document: Document,
        run_id: str,
        result: dict[str, Any],
        project_id: uuid.UUID | None,
    ):
        lines: list[str] = [f"# {document.title} · AI 分析笔记", ""]
        if result.get("summary"):
            lines += ["## 摘要", "", str(result["summary"]), ""]
        if result.get("key_points"):
            lines += ["## 核心知识点", ""]
            lines += [f"- {p}" for p in result["key_points"][:12]]
            lines.append("")
        if result.get("concepts"):
            lines += ["## 概念", "", "、".join(str(c) for c in result["concepts"][:20]), ""]
        if result.get("terms"):
            lines += ["## 术语", ""]
            for term in result["terms"][:12]:
                if isinstance(term, dict):
                    lines.append(f"- **{term.get('term', '')}**：{term.get('definition', '')}")
            lines.append("")
        if result.get("todos"):
            lines += ["## 待办", ""]
            for todo in result["todos"][:12]:
                title = todo.get("title") if isinstance(todo, dict) else str(todo)
                lines.append(f"- [ ] {title}")
            lines.append("")
        if result.get("cards"):
            lines += ["## 知识卡片", ""]
            for card in result["cards"][:16]:
                lines.append(f"- **{card['front']}** → {card['back']}")
            lines.append("")
        lines += [
            "---",
            "",
            f"> 由 AI Workbench 生成 · AI Run `{run_id}` · 文档 `{document.id}`",
        ]

        from app.services.workspace_service import NoteService

        note = await NoteService(self.session).create_note(
            owner_id=owner_id,
            title=f"{document.title} · AI 分析笔记"[:300],
            content_md="\n".join(lines),
            project_id=project_id or document.project_id,
            knowledge_base_id=document.knowledge_base_id,
            source_document_id=document.id,
            source_run_id=run_id,
            is_ai_generated=True,
        )
        return note


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def normalize_actions(actions: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for action in actions or ["summary"]:
        key = re.sub(r"[^a-z_]", "", str(action).strip().lower())
        if key in ANALYSIS_ACTIONS and key not in cleaned:
            cleaned.append(key)
    return cleaned or ["summary"]


def document_summary_row(document: Document) -> dict[str, Any]:
    return {
        "id": str(document.id),
        "title": document.title,
        "status": document.status,
        "chunks": document.chunk_count,
        "embedded": document.embedded_chunk_count,
    }


__all__ = ["ANALYSIS_ACTIONS", "Concept", "DocumentService", "normalize_actions"]

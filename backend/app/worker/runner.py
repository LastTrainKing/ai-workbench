"""Background worker.

Why a database-backed queue instead of Celery/RQ/Arq
---------------------------------------------------
* Works natively on Windows (no fork/pre-fork or broker daemon required).
* The job row is committed in the **same transaction** as the business data, so
  a document can never be "saved but never processed".
* ``SELECT ... FOR UPDATE SKIP LOCKED`` gives safe multi-worker claiming.
* Job state (attempts, stage, progress, error) is directly queryable by the UI,
  which is exactly what the document upload page needs to display.

Reliability rules implemented here
----------------------------------
* bounded attempts with exponential backoff (``scheduled_at`` in the future);
* stale ``RUNNING`` jobs (crashed worker) are recovered on startup;
* failures are recorded with the error code — never swallowed;
* each job runs in its own session/transaction.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.domain.enums import DocumentStatus, JobStatus, JobType
from app.infrastructure.database import session_scope
from app.repositories.ai_repo import JobRepository

logger = get_logger(__name__)

WORKER_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"


class Worker:
    def __init__(self, *, concurrency: int | None = None) -> None:
        self.concurrency = max(1, concurrency or settings.worker_concurrency)
        self._stop = asyncio.Event()
        self._inflight: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------- lifecycle --
    async def start(self) -> None:
        configure_logging("worker")
        logger.info(
            "worker_starting",
            extra={"worker_id": WORKER_ID, "concurrency": self.concurrency},
        )
        await self._recover_stale()
        try:
            while not self._stop.is_set():
                claimed = await self._fill_slots()
                if self._stop.is_set():
                    break
                if claimed:
                    # Yield once so the freshly created job tasks actually start.
                    await asyncio.sleep(0)
                    continue

                # IMPORTANT — event-loop starvation guard.
                # ``_fill_slots`` returns 0 both when the queue is empty *and*
                # when every slot is busy. If we simply looped again here the
                # coroutine would never await anything, the event loop would
                # never get a chance to run the job tasks, and the whole process
                # (including the API when the worker runs in-process) would
                # freeze. Always yielding for the poll interval fixes that.
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=settings.worker_poll_interval)
        except asyncio.CancelledError:
            logger.info("worker_cancelled", extra={"worker_id": WORKER_ID})
            raise
        finally:
            await self._drain()

    def request_stop(self) -> None:
        self._stop.set()

    async def _drain(self) -> None:
        if self._inflight:
            logger.info("worker_draining", extra={"inflight": len(self._inflight)})
            await asyncio.gather(*self._inflight, return_exceptions=True)
        logger.info("worker_stopped", extra={"worker_id": WORKER_ID})

    # ------------------------------------------------------------- scheduling --
    async def _recover_stale(self) -> None:
        async with session_scope() as session:
            requeued = await JobRepository(session).requeue_stale(settings.worker_stale_job_seconds)
        if requeued:
            logger.warning("stale_jobs_requeued", extra={"count": requeued})

    async def _fill_slots(self) -> int:
        """Claim up to ``concurrency`` runnable jobs. Returns how many were claimed."""
        self._inflight = {t for t in self._inflight if not t.done()}
        slots = self.concurrency - len(self._inflight)
        if slots <= 0:
            return 0

        claimed = 0
        for _ in range(slots):
            job = await self._claim()
            if job is None:
                break
            claimed += 1
            self._inflight.add(asyncio.create_task(self._process(job)))
        return claimed

    async def _claim(self) -> dict[str, object] | None:
        async with session_scope() as session:
            job = await JobRepository(session).claim_next(WORKER_ID)
            if job is None:
                return None
            return {
                "id": job.id,
                "job_type": job.job_type,
                "payload": dict(job.payload or {}),
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
            }

    # -------------------------------------------------------------- execution --
    async def _process(self, job: dict[str, object]) -> None:
        job_id = job["id"]
        job_type = str(job["job_type"])
        logger.info("job_started", extra={"job_id": str(job_id), "job_type": job_type})

        async def progress(percent: int, stage: str) -> None:
            logger.info(
                "job_progress",
                extra={"job_id": str(job_id), "percent": percent, "stage": stage},
            )
            async with session_scope() as session:
                repo = JobRepository(session)
                row = await repo.get(job_id)  # type: ignore[arg-type]
                if row is None:
                    logger.warning("job_progress_missing_row", extra={"job_id": str(job_id)})
                    return
                row.progress = percent
                row.stage = stage

        try:
            handler = _HANDLERS.get(job_type)
            if handler is None:
                raise AppError(f"No handler registered for job type '{job_type}'")
            # A job-level timeout guarantees that a wedged handler cannot hold a
            # worker slot forever (and, in in-process mode, cannot pin the API).
            async with asyncio.timeout(settings.worker_job_timeout_seconds):
                await handler(job["payload"], progress)  # type: ignore[arg-type]
        except TimeoutError:
            await self._mark_failed(
                job,
                f"job_timeout: exceeded {settings.worker_job_timeout_seconds}s",
                requeue=False,
            )
        except asyncio.CancelledError:
            await self._mark_failed(job, "cancelled", requeue=True)
            raise
        except AppError as exc:
            await self._mark_failed(job, f"{exc.code}: {exc.message}")
        except Exception as exc:
            logger.exception("job_crashed", extra={"job_id": str(job_id)})
            await self._mark_failed(job, f"unexpected_error: {type(exc).__name__}: {exc}")
        else:
            await self._mark_succeeded(job)

    async def _mark_succeeded(self, job: dict[str, object]) -> None:
        async with session_scope() as session:
            repo = JobRepository(session)
            row = await repo.get(job["id"])  # type: ignore[arg-type]
            if row is None:
                return
            row.status = JobStatus.SUCCEEDED.value
            row.progress = 100
            row.stage = "DONE"
            row.finished_at = datetime.now(UTC)
            row.locked_by = ""
        logger.info("job_succeeded", extra={"job_id": str(job["id"])})

    async def _mark_failed(
        self, job: dict[str, object], error: str, *, requeue: bool = False
    ) -> None:
        attempts = int(job["attempts"])  # type: ignore[arg-type]
        max_attempts = int(job["max_attempts"])  # type: ignore[arg-type]
        will_retry = requeue or attempts < max_attempts

        async with session_scope() as session:
            repo = JobRepository(session)
            row = await repo.get(job["id"])  # type: ignore[arg-type]
            if row is None:
                return
            row.error_message = error[:4000]
            row.locked_by = ""
            if will_retry:
                delay = min(300, 5 * (2 ** max(0, attempts - 1)))
                row.status = JobStatus.QUEUED.value
                row.scheduled_at = datetime.now(UTC) + timedelta(seconds=delay)
                row.stage = f"RETRY_IN_{delay}S"
            else:
                row.status = JobStatus.FAILED.value
                row.finished_at = datetime.now(UTC)
                row.stage = "FAILED"

        # Reflect a permanent failure on the document so the UI can show it.
        if not will_retry and str(job["job_type"]) == JobType.INGEST_DOCUMENT.value:
            document_id = (job["payload"] or {}).get("document_id")  # type: ignore[union-attr]
            if document_id:
                async with session_scope() as session:
                    from app.repositories.knowledge_repo import DocumentRepository

                    document = await DocumentRepository(session).get(uuid.UUID(str(document_id)))
                    if document is not None and document.status != DocumentStatus.COMPLETED.value:
                        document.status = DocumentStatus.FAILED.value
                        document.error_message = error[:2000]

        logger.warning(
            "job_failed",
            extra={
                "job_id": str(job["id"]),
                "attempt": attempts,
                "max_attempts": max_attempts,
                "will_retry": will_retry,
                "error": error[:500],
            },
        )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def _handle_ingest(payload: dict[str, object], progress) -> None:
    from app.services.document_service import DocumentService

    document_id = uuid.UUID(str(payload["document_id"]))
    async with session_scope() as session:
        await DocumentService(session).ingest(document_id, progress=progress)


async def _handle_embed(payload: dict[str, object], progress) -> None:
    """Embed chunks that are still PENDING/FAILED (used after a provider outage)."""
    from app.ai.rag.embedding import EmbeddingService
    from app.infrastructure.vector_store import VectorPoint, get_vector_store
    from app.repositories.knowledge_repo import DocumentChunkRepository, DocumentRepository

    kb_id = (
        uuid.UUID(str(payload["knowledge_base_id"])) if payload.get("knowledge_base_id") else None
    )
    async with session_scope() as session:
        chunk_repo = DocumentChunkRepository(session)
        rows = await chunk_repo.pending_embedding(kb_id, limit=int(payload.get("limit") or 500))
        if not rows:
            await progress(100, "NOTHING_TO_DO")
            return

        embedder = EmbeddingService()
        store = get_vector_store()
        embedded = 0
        batch_size = max(1, settings.embedding_batch_size)
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            try:
                result = await embedder.embed_texts([row.content for row in batch])
            except AppError as exc:
                for row in batch:
                    row.embedding_status = "FAILED"
                    row.embedding_error = f"{exc.code}: {exc.message}"[:2000]
                await session.flush()
                continue
            document = await DocumentRepository(session).get(batch[0].document_id)
            title = document.title if document else ""
            points = []
            for row, vector in zip(batch, result.vectors, strict=True):
                if not row.vector_id:
                    row.vector_id = str(uuid.uuid4())
                row.embedding_status = "EMBEDDED"
                row.embedding_model = result.model
                row.embedding_error = ""
                points.append(
                    VectorPoint(
                        id=row.vector_id,
                        vector=vector,
                        payload={
                            "chunk_id": str(row.id),
                            "document_id": str(row.document_id),
                            "knowledge_base_id": str(row.knowledge_base_id),
                            "chunk_index": row.chunk_index,
                            "heading": row.heading,
                            "document_title": title,
                            "content": row.content[:1200],
                            "embedding_model": result.model,
                        },
                    )
                )
            await store.upsert(points)
            embedded += len(points)
            await session.flush()
            await progress(int(100 * min(1.0, (start + len(batch)) / len(rows))), "EMBEDDING")

        # Refresh per-document counters so the UI is accurate.
        document_ids = {row.document_id for row in rows}
        for document_id in document_ids:
            document = await DocumentRepository(session).get(document_id)
            if document is None:
                continue
            counts = await chunk_repo.embedding_progress(document_id)
            document.embedded_chunk_count = counts["embedded"]
            if counts["embedded"] and document.status != DocumentStatus.COMPLETED.value:
                document.status = DocumentStatus.COMPLETED.value
                document.error_message = ""
            elif counts["failed"] and not counts["embedded"]:
                document.status = DocumentStatus.FAILED.value
                document.error_message = "embedding_error: 所有分块向量化失败"
        await session.flush()
        logger.info("embed_job_completed", extra={"embedded": embedded})


async def _handle_reindex(payload: dict[str, object], progress) -> None:
    from app.models.knowledge import Document
    from app.repositories.knowledge_repo import DocumentRepository
    from app.services.document_service import DocumentService

    kb_id = uuid.UUID(str(payload["knowledge_base_id"]))
    async with session_scope() as session:
        documents = await DocumentRepository(session).list(Document.knowledge_base_id == kb_id)
    for index, document in enumerate(documents, start=1):
        async with session_scope() as session:
            await DocumentService(session).ingest(document.id)
        await progress(int(100 * index / max(1, len(documents))), "REINDEXING")


_HANDLERS = {
    JobType.INGEST_DOCUMENT.value: _handle_ingest,
    JobType.EMBED_DOCUMENT.value: _handle_embed,
    JobType.REINDEX_KNOWLEDGE_BASE.value: _handle_reindex,
}


async def run_worker(concurrency: int | None = None) -> None:
    worker = Worker(concurrency=concurrency)
    try:
        await worker.start()
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        worker.request_stop()


def run_worker_blocking(concurrency: int | None = None) -> None:
    try:
        asyncio.run(run_worker(concurrency))
    except KeyboardInterrupt:  # pragma: no cover
        print("\nworker stopped")

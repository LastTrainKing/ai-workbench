"""Integration tests for the background worker.

These tests exist because the worker shipped with a severe defect: the main loop
returned without awaiting anything whenever every slot was busy, which starved
the event loop and froze the entire process (including the HTTP API when the
worker runs in-process). ``test_worker_does_not_starve_the_event_loop`` is the
regression guard for that bug.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from app.domain.enums import DocumentStatus, JobStatus
from app.infrastructure.database import session_scope
from app.models.knowledge import Document
from app.repositories.ai_repo import JobRepository
from app.worker.runner import Worker

pytestmark = pytest.mark.integration


async def _enqueue(document_id: str, owner_id: str | None = None) -> uuid.UUID:
    async with session_scope() as session:
        job = await JobRepository(session).enqueue(
            job_type="INGEST_DOCUMENT",
            payload={"document_id": document_id, "owner_id": owner_id or ""},
            owner_id=uuid.UUID(owner_id) if owner_id else None,
            priority=10,
            max_attempts=2,
        )
        return job.id


async def _upload(client: httpx.AsyncClient, headers: dict, kb_id: str, name: str, body: bytes):
    return await client.post(
        "/documents/upload",
        headers=headers,
        files={"file": (name, io.BytesIO(body), "application/octet-stream")},
        data={"knowledge_base_id": kb_id},
    )


# --------------------------------------------------------------------------- #
# queue mechanics
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_claim_next_marks_the_job_running() -> None:
    async with session_scope() as session:
        repo = JobRepository(session)
        job = await repo.enqueue(job_type="INGEST_DOCUMENT", payload={"document_id": "x"})

    async with session_scope() as session:
        claimed = await JobRepository(session).claim_next("worker-1")

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.RUNNING.value
    assert claimed.attempts == 1
    assert claimed.locked_by == "worker-1"
    assert claimed.started_at is not None


@pytest.mark.asyncio
async def test_claim_next_returns_none_when_the_queue_is_empty() -> None:
    async with session_scope() as session:
        assert await JobRepository(session).claim_next("worker-1") is None


@pytest.mark.asyncio
async def test_future_jobs_are_not_claimed_early() -> None:
    async with session_scope() as session:
        await JobRepository(session).enqueue(
            job_type="INGEST_DOCUMENT", payload={"document_id": "x"}, delay_seconds=600
        )
    async with session_scope() as session:
        assert await JobRepository(session).claim_next("worker-1") is None


@pytest.mark.asyncio
async def test_higher_priority_jobs_are_claimed_first() -> None:
    async with session_scope() as session:
        repo = JobRepository(session)
        low = await repo.enqueue(job_type="INGEST_DOCUMENT", payload={"tag": "low"}, priority=100)
        high = await repo.enqueue(job_type="INGEST_DOCUMENT", payload={"tag": "high"}, priority=1)

    async with session_scope() as session:
        first = await JobRepository(session).claim_next("w")
    assert first is not None and first.id == high.id

    async with session_scope() as session:
        second = await JobRepository(session).claim_next("w")
    assert second is not None and second.id == low.id


@pytest.mark.asyncio
async def test_stale_running_jobs_are_requeued() -> None:
    async with session_scope() as session:
        job = await JobRepository(session).enqueue(
            job_type="INGEST_DOCUMENT", payload={"document_id": "x"}
        )
        job.status = JobStatus.RUNNING.value
        job.started_at = datetime.now(UTC) - timedelta(seconds=3600)
        job.locked_by = "dead-worker"
        await session.flush()

    async with session_scope() as session:
        recovered = await JobRepository(session).requeue_stale(900)
    assert recovered == 1

    async with session_scope() as session:
        refreshed = await JobRepository(session).get(job.id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.QUEUED.value
    assert refreshed.locked_by == ""


@pytest.mark.asyncio
async def test_job_stats_are_grouped_by_status() -> None:
    async with session_scope() as session:
        await JobRepository(session).enqueue(job_type="INGEST_DOCUMENT", payload={})
    async with session_scope() as session:
        stats = await JobRepository(session).stats()
    assert stats.get(JobStatus.QUEUED.value, 0) >= 1


# --------------------------------------------------------------------------- #
# the regression guard
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_worker_does_not_starve_the_event_loop(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    """A saturated worker must still let the event loop run other coroutines.

    With the original implementation, the moment every slot was busy the loop
    spun without awaiting, so ``_process`` tasks were never scheduled and any
    concurrent coroutine — including the request handler — hung forever.

    The guard measures the **maximum gap between yields of a concurrent
    coroutine** while the worker is saturated. A starved loop produces a gap of
    seconds (or never yields at all, tripping the pytest timeout).
    """
    upload = await _upload(client, auth_headers, knowledge_base["id"], "notes.md", sample_markdown)
    document_id = upload.json()["data"]["document"]["id"]
    await _enqueue(document_id)

    worker = Worker(concurrency=1)
    worker_task = asyncio.create_task(worker.start())

    loop = asyncio.get_running_loop()
    ticks = 0
    max_gap = 0.0
    last = loop.time()
    completed = False
    deadline = last + 30

    try:
        while loop.time() < deadline:
            await asyncio.sleep(0.2)
            now = loop.time()
            max_gap = max(max_gap, now - last)
            last = now
            ticks += 1

            async with session_scope() as session:
                document = await session.get(Document, uuid.UUID(document_id))
                status = document.status if document else None
            if status == DocumentStatus.COMPLETED.value:
                completed = True
                break
    finally:
        worker.request_stop()
        await asyncio.wait_for(worker_task, timeout=30)

    assert ticks >= 1, "the event loop was completely starved by the worker"
    assert max_gap < 3.0, f"the event loop stalled for {max_gap:.2f}s while the worker was busy"
    assert completed, "the worker did not finish the job within the deadline"

    async with session_scope() as session:
        document = await session.get(Document, uuid.UUID(document_id))
    assert document is not None
    assert document.status == DocumentStatus.COMPLETED.value
    assert document.embedded_chunk_count == document.chunk_count
    assert document.embedded_chunk_count > 0


@pytest.mark.asyncio
async def test_worker_marks_a_job_succeeded(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    upload = await _upload(client, auth_headers, knowledge_base["id"], "ok.md", sample_markdown)
    document_id = upload.json()["data"]["document"]["id"]
    job_id = await _enqueue(document_id)

    worker = Worker(concurrency=1)
    task = asyncio.create_task(worker.start())
    try:
        for _ in range(100):
            await asyncio.sleep(0.2)
            async with session_scope() as session:
                job = await JobRepository(session).get(job_id)
            if job is not None and job.status in (
                JobStatus.SUCCEEDED.value,
                JobStatus.FAILED.value,
            ):
                break
    finally:
        worker.request_stop()
        await asyncio.wait_for(task, timeout=30)

    async with session_scope() as session:
        job = await JobRepository(session).get(job_id)
    assert job is not None
    assert job.status == JobStatus.SUCCEEDED.value
    assert job.progress == 100
    assert job.stage == "DONE"
    assert job.finished_at is not None


@pytest.mark.asyncio
async def test_failed_job_is_retried_then_marked_failed(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict
) -> None:
    """A permanently broken document must end up FAILED, not retried forever."""
    upload = await _upload(
        client, auth_headers, knowledge_base["id"], "broken.pdf", b"%PDF-1.4 broken"
    )
    document_id = upload.json()["data"]["document"]["id"]

    async with session_scope() as session:
        job = await JobRepository(session).enqueue(
            job_type="INGEST_DOCUMENT",
            payload={"document_id": document_id},
            priority=10,
            max_attempts=1,  # fail fast so the test does not wait for backoff
        )
        job_id = job.id

    worker = Worker(concurrency=1)
    task = asyncio.create_task(worker.start())
    try:
        for _ in range(100):
            await asyncio.sleep(0.2)
            async with session_scope() as session:
                refreshed = await JobRepository(session).get(job_id)
            if refreshed is not None and refreshed.status == JobStatus.FAILED.value:
                break
    finally:
        worker.request_stop()
        await asyncio.wait_for(task, timeout=30)

    async with session_scope() as session:
        refreshed = await JobRepository(session).get(job_id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.FAILED.value
    assert "document_parse_error" in refreshed.error_message
    assert refreshed.locked_by == ""

    detail = (await client.get(f"/documents/{document_id}", headers=auth_headers)).json()["data"]
    assert detail["status"] == DocumentStatus.FAILED.value
    assert "document_parse_error" in detail["error_message"]


@pytest.mark.asyncio
async def test_worker_unknown_job_type_fails_cleanly() -> None:
    async with session_scope() as session:
        job = await JobRepository(session).enqueue(
            job_type="NOT_A_REAL_JOB", payload={}, max_attempts=1
        )
        job_id = job.id

    worker = Worker(concurrency=1)
    task = asyncio.create_task(worker.start())
    try:
        for _ in range(60):
            await asyncio.sleep(0.2)
            async with session_scope() as session:
                refreshed = await JobRepository(session).get(job_id)
            if refreshed is not None and refreshed.status == JobStatus.FAILED.value:
                break
    finally:
        worker.request_stop()
        await asyncio.wait_for(task, timeout=30)

    async with session_scope() as session:
        refreshed = await JobRepository(session).get(job_id)
    assert refreshed is not None
    assert refreshed.status == JobStatus.FAILED.value
    assert "No handler registered" in refreshed.error_message


@pytest.mark.asyncio
async def test_worker_drains_in_flight_jobs_on_shutdown(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    upload = await _upload(client, auth_headers, knowledge_base["id"], "drain.md", sample_markdown)
    document_id = upload.json()["data"]["document"]["id"]
    await _enqueue(document_id)

    worker = Worker(concurrency=2)
    task = asyncio.create_task(worker.start())
    await asyncio.sleep(0.5)
    worker.request_stop()
    await asyncio.wait_for(task, timeout=30)  # must not raise or hang

    async with session_scope() as session:
        document = await session.get(Document, uuid.UUID(document_id))
    # Draining either finished the job or left it for a later attempt; what
    # matters is that shutdown was clean and the document is not wedged.
    assert document is not None
    assert document.status in (
        DocumentStatus.PENDING.value,
        DocumentStatus.PROCESSING.value,
        DocumentStatus.COMPLETED.value,
    )


@pytest.mark.asyncio
async def test_truncate_between_tests_leaves_no_residue() -> None:
    """Sanity check on the isolation fixture used by the whole suite."""
    async with session_scope() as session:
        count = (await session.execute(text("SELECT count(*) FROM jobs"))).scalar_one()
    assert count == 0, "the _clean_state fixture must truncate every table"

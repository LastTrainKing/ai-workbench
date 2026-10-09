"""Repositories for AI runs, prompts, jobs and the activity feed."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select, update

from app.core.logging import get_logger
from app.domain.enums import DocumentStatus, JobStatus, RunStatus
from app.models.ai import AgentRun, AIRun, AIRunSource, Prompt, PromptVersion, ToolCall
from app.models.job import ActivityEvent, Job
from app.models.knowledge import Document
from app.repositories.base import BaseRepository, apply_search

logger = get_logger(__name__)


def _document_ids(jobs: list[Job]) -> list[uuid.UUID]:
    """The document ids an ``INGEST_DOCUMENT`` job payload names, in order."""
    found: list[uuid.UUID] = []
    for job in jobs:
        raw = (job.payload or {}).get("document_id")
        if not raw:
            continue
        try:
            found.append(uuid.UUID(str(raw)))
        except (ValueError, TypeError):
            continue
    return found


class AIRunRepository(BaseRepository[AIRun]):
    model = AIRun

    def query(
        self,
        owner_id: uuid.UUID,
        *,
        run_type: str | None = None,
        status: str | None = None,
        project_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> Select[Any]:
        stmt = select(AIRun).where(AIRun.owner_id == owner_id)
        if run_type:
            stmt = stmt.where(AIRun.run_type == run_type)
        if status:
            stmt = stmt.where(AIRun.status == status)
        if project_id:
            stmt = stmt.where(AIRun.project_id == project_id)
        stmt = apply_search(stmt, AIRun.response_text, search)
        return stmt.order_by(AIRun.created_at.desc())

    async def by_run_id(self, run_id: str, owner_id: uuid.UUID) -> AIRun | None:
        """Find a run by its display id **or** by its primary key.

        Two ids identify an AI run and they are different values: `run_id` is the
        display string (`run_ab12…`) and `id` is the UUID. Callers hold one or the
        other — `BuildRun.ai_run_id` is the UUID, the URL of a run page is the display
        id — and an endpoint that only accepts one of them returns "not found" for a
        run that plainly exists.

        That is exactly what happened: a completed build run exposed its UUID, the
        report endpoint resolved by display id, and reading the run's own report came
        back `404 AI run not found`. An id is an id; both are accepted.
        """
        run = await self.get_or_none(AIRun.run_id == run_id, AIRun.owner_id == owner_id)
        if run is not None:
            return run
        try:
            as_uuid = uuid.UUID(str(run_id))
        except (ValueError, AttributeError, TypeError):
            return None
        return await self.get_or_none(AIRun.id == as_uuid, AIRun.owner_id == owner_id)

    async def tool_calls(self, ai_run_id: uuid.UUID) -> list[ToolCall]:
        return await self.list_of(
            ToolCall, ToolCall.ai_run_id == ai_run_id, order_by=ToolCall.step_index
        )

    async def sources(self, ai_run_id: uuid.UUID) -> list[AIRunSource]:
        return await self.list_of(AIRunSource, AIRunSource.ai_run_id == ai_run_id)

    async def agent_run(self, ai_run_id: uuid.UUID) -> AgentRun | None:
        return await self.get_one_of(AgentRun, AgentRun.ai_run_id == ai_run_id)

    # --------------------------------------------------------------- metrics --
    async def metrics(self, owner_id: uuid.UUID, *, days: int = 30) -> dict[str, Any]:
        since = datetime.now(UTC) - timedelta(days=days)
        row = (
            await self.session.execute(
                select(
                    func.count(AIRun.id).label("total"),
                    func.coalesce(func.avg(AIRun.latency_ms), 0).label("avg_latency"),
                    func.coalesce(func.sum(AIRun.total_tokens), 0).label("tokens"),
                    func.coalesce(func.sum(AIRun.cost_estimate), 0).label("cost"),
                ).where(AIRun.owner_id == owner_id, AIRun.created_at >= since)
            )
        ).one()
        failed = await self.count(
            AIRun.owner_id == owner_id,
            AIRun.created_at >= since,
            AIRun.status.in_([RunStatus.FAILED.value, RunStatus.TIMEOUT.value]),
        )
        total = int(row.total or 0)
        by_type = (
            await self.session.execute(
                select(AIRun.run_type, func.count(AIRun.id))
                .where(AIRun.owner_id == owner_id, AIRun.created_at >= since)
                .group_by(AIRun.run_type)
            )
        ).all()
        by_model = (
            await self.session.execute(
                select(AIRun.model, func.count(AIRun.id))
                .where(AIRun.owner_id == owner_id, AIRun.created_at >= since)
                .group_by(AIRun.model)
            )
        ).all()
        return {
            "window_days": days,
            "total_runs": total,
            "failed_runs": failed,
            "failure_rate": round(failed / total, 4) if total else 0.0,
            "avg_latency_ms": int(row.avg_latency or 0),
            "total_tokens": int(row.tokens or 0),
            "estimated_cost_usd": round(float(row.cost or 0.0), 6),
            "by_type": {str(k): int(v) for k, v in by_type},
            "by_model": {str(k or "unknown"): int(v) for k, v in by_model},
        }

    async def daily_counts(self, owner_id: uuid.UUID, days: int = 14) -> list[dict[str, Any]]:
        since = datetime.now(UTC) - timedelta(days=days)
        bucket = func.date_trunc("day", AIRun.created_at).label("day")
        rows = (
            await self.session.execute(
                select(
                    bucket,
                    func.count(AIRun.id),
                    func.coalesce(func.sum(AIRun.total_tokens), 0),
                    func.coalesce(func.avg(AIRun.latency_ms), 0),
                )
                .where(AIRun.owner_id == owner_id, AIRun.created_at >= since)
                .group_by(bucket)
                .order_by(bucket)
            )
        ).all()
        return [
            {
                "date": day.date().isoformat() if day else "",
                "runs": int(count or 0),
                "tokens": int(tokens or 0),
                "avg_latency_ms": int(latency or 0),
            }
            for day, count, tokens, latency in rows
        ]

    async def document_usage(self, owner_id: uuid.UUID, limit: int = 10) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                select(
                    AIRunSource.document_id,
                    AIRunSource.document_title,
                    func.count(AIRunSource.id).label("uses"),
                )
                .join(AIRun, AIRun.id == AIRunSource.ai_run_id)
                .where(AIRun.owner_id == owner_id, AIRunSource.document_id.is_not(None))
                .group_by(AIRunSource.document_id, AIRunSource.document_title)
                .order_by(func.count(AIRunSource.id).desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                "document_id": str(doc_id),
                "document_title": title,
                "uses": int(uses or 0),
            }
            for doc_id, title, uses in rows
        ]


class PromptRepository(BaseRepository[Prompt]):
    model = Prompt

    async def by_name(self, name: str) -> Prompt | None:
        return await self.get_or_none(Prompt.name == name)

    async def current_version(self, prompt_id: uuid.UUID) -> PromptVersion | None:
        return await self.get_one_of(
            PromptVersion,
            PromptVersion.prompt_id == prompt_id,
            PromptVersion.is_current.is_(True),
        )

    async def versions(self, prompt_id: uuid.UUID) -> list[PromptVersion]:
        return await self.list_of(
            PromptVersion,
            PromptVersion.prompt_id == prompt_id,
            order_by=PromptVersion.version.desc(),
        )

    async def latest_version_number(self, prompt_id: uuid.UUID) -> int:
        value = (
            await self.session.execute(
                select(func.coalesce(func.max(PromptVersion.version), 0)).where(
                    PromptVersion.prompt_id == prompt_id
                )
            )
        ).scalar_one()
        return int(value or 0)

    async def deactivate_all(self, prompt_id: uuid.UUID) -> None:
        await self.session.execute(
            update(PromptVersion)
            .where(PromptVersion.prompt_id == prompt_id)
            .values(is_current=False)
        )


class JobRepository(BaseRepository[Job]):
    model = Job

    async def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        owner_id: uuid.UUID | None = None,
        priority: int = 100,
        max_attempts: int = 3,
        delay_seconds: int = 0,
    ) -> Job:
        job = Job(
            job_type=job_type,
            payload=payload,
            status=JobStatus.QUEUED.value,
            priority=priority,
            max_attempts=max_attempts,
            scheduled_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
            owner_id=owner_id,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def claim_next(self, worker_id: str) -> Job | None:
        """Atomically claim the next runnable job.

        ``FOR UPDATE SKIP LOCKED`` lets several workers run safely against the
        same table without any external broker.
        """
        stmt = (
            select(Job)
            .where(
                Job.status == JobStatus.QUEUED.value,
                Job.scheduled_at <= datetime.now(UTC),
            )
            .order_by(Job.priority, Job.scheduled_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job = (await self.session.execute(stmt)).scalar_one_or_none()
        if job is None:
            return None
        job.status = JobStatus.RUNNING.value
        job.started_at = datetime.now(UTC)
        job.locked_by = worker_id
        job.attempts += 1
        await self.session.flush()
        return job

    async def latest_for_document(self, document_id: uuid.UUID) -> Job | None:
        stmt = (
            select(Job)
            .where(Job.job_type == "INGEST_DOCUMENT")
            .where(Job.payload["document_id"].astext == str(document_id))
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def requeue_stale(self, older_than_seconds: int) -> int:
        """Recover jobs orphaned by a crashed worker — within their attempt budget.

        This used to move **every** stale ``RUNNING`` job back to ``QUEUED`` without
        looking at ``attempts``. So a job that crashes the worker every time it runs was
        retried forever: ``claim_next`` increments ``attempts``, and this method ignored
        the ceiling it was incrementing towards, which meant ``max_attempts`` bounded
        nothing across restarts. On a desktop product that restarts often, "bounded
        attempts" has to mean bounded *across* restarts, or it means nothing.

        Now a stale job whose attempts are spent becomes terminal ``FAILED`` with the
        reason and an end time and is never requeued; one that still has attempts left
        goes back to the queue **with a backoff**, so a crash loop is slowed rather than
        spun. Returns the number requeued.
        """
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=older_than_seconds)

        stale = list(
            (
                await self.session.execute(
                    select(Job).where(
                        Job.status == JobStatus.RUNNING.value,
                        Job.started_at < cutoff,
                    )
                )
            ).scalars()
        )
        if not stale:
            return 0

        requeued = 0
        exhausted: list[Job] = []
        for job in stale:
            if job.attempts >= job.max_attempts:
                job.status = JobStatus.FAILED.value
                job.finished_at = now
                job.locked_by = ""
                job.error_message = (
                    f"任务在 {job.attempts} 次尝试后仍未完成（上限 {job.max_attempts}）；"
                    "运行进程可能反复中断，已停止重试。"
                )
                exhausted.append(job)
                continue
            # Same backoff shape as the in-run retry, so the two paths cannot disagree
            # about how long a retry waits.
            delay = min(300, 5 * (2 ** max(0, job.attempts - 1)))
            job.status = JobStatus.QUEUED.value
            job.scheduled_at = now + timedelta(seconds=delay)
            job.locked_by = ""
            requeued += 1

        # A terminal job must not leave its document saying PROCESSING. Otherwise the
        # queue is empty, the job is FAILED, and the document looks busy forever — the
        # exact split-brain the two status columns have to avoid.
        for document_id in _document_ids(exhausted):
            document = await self.session.get(Document, document_id)
            if document is not None and document.status != DocumentStatus.COMPLETED.value:
                document.status = DocumentStatus.FAILED.value

        await self.session.flush()
        if exhausted:
            logger.error(
                "stale_jobs_exhausted",
                extra={
                    "count": len(exhausted),
                    "job_ids": [str(j.id) for j in exhausted[:10]],
                },
            )
        return requeued

    async def stats(self, owner_id: uuid.UUID | None = None) -> dict[str, int]:
        stmt = select(Job.status, func.count(Job.id))
        if owner_id:
            stmt = stmt.where(Job.owner_id == owner_id)
        rows = (await self.session.execute(stmt.group_by(Job.status))).all()
        return {str(status): int(count) for status, count in rows}


class ActivityRepository(BaseRepository[ActivityEvent]):
    model = ActivityEvent

    async def record(
        self,
        *,
        owner_id: uuid.UUID,
        event_type: str,
        title: str,
        entity_type: str = "",
        entity_id: str = "",
        project_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ActivityEvent:
        event = ActivityEvent(
            owner_id=owner_id,
            project_id=project_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            title=title[:300],
            event_metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def recent(self, owner_id: uuid.UUID, limit: int = 12) -> list[ActivityEvent]:
        return await self.list(
            ActivityEvent.owner_id == owner_id,
            order_by=ActivityEvent.created_at.desc(),
            limit=limit,
        )

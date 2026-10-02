"""Repositories for AI runs, prompts, jobs and the activity feed."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, select, update

from app.domain.enums import JobStatus, RunStatus
from app.models.ai import AgentRun, AIRun, AIRunSource, Prompt, PromptVersion, ToolCall
from app.models.job import ActivityEvent, Job
from app.repositories.base import BaseRepository, apply_search


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
        return await self.get_or_none(AIRun.run_id == run_id, AIRun.owner_id == owner_id)

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
        """Recover jobs orphaned by a crashed worker."""
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        result = await self.session.execute(
            update(Job)
            .where(Job.status == JobStatus.RUNNING.value, Job.started_at < cutoff)
            .values(
                status=JobStatus.QUEUED.value,
                scheduled_at=datetime.now(UTC),
                locked_by="",
            )
        )
        return int(result.rowcount or 0)

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

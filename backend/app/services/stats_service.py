"""Dashboard statistics and the knowledge-graph projection.

Every number returned here is computed from the database — there is no seeded or
hard-coded demo data anywhere in the product.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.factory import build_embedding_provider, build_llm_provider
from app.core.cache import cache_get, cache_set, make_key, namespace_version
from app.core.config import settings
from app.core.dates import day_bounds, week_bounds
from app.domain.enums import DocumentStatus, ProjectStatus, TaskStatus
from app.infrastructure.files import storage_usage
from app.models.ai import AIRun, AIRunSource
from app.models.job import ActivityEvent
from app.models.knowledge import Concept, Document, DocumentChunk, DocumentConcept, KnowledgeBase
from app.models.note import Note
from app.models.project import Project
from app.models.task import Task, TaskDocument
from app.repositories.ai_repo import ActivityRepository, AIRunRepository, JobRepository
from app.repositories.knowledge_repo import DocumentRepository, KnowledgeBaseRepository
from app.repositories.work_repo import NoteRepository, ProjectRepository, TaskRepository


class StatsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tasks = TaskRepository(session)
        self.documents = DocumentRepository(session)
        self.bases = KnowledgeBaseRepository(session)
        self.projects = ProjectRepository(session)
        self.notes = NoteRepository(session)
        self.runs = AIRunRepository(session)
        self.activity = ActivityRepository(session)
        self.jobs = JobRepository(session)

    async def dashboard(self, *, owner_id: uuid.UUID) -> dict[str, Any]:
        version = await namespace_version("stats")
        key = make_key("stats", owner_id, version)
        cached = await cache_get("stats", key)
        if cached:
            cached["cached"] = True
            return cached

        today_start, today_end = day_bounds()
        week_start, week_end = week_bounds()
        now = datetime.now(UTC)

        # ---------------------------------------------------------- tasks ---
        status_counts = await self.tasks.status_counts(owner_id)
        total_tasks = sum(status_counts.values())
        done_tasks = status_counts.get(TaskStatus.DONE.value, 0)
        open_tasks = total_tasks - done_tasks - status_counts.get(TaskStatus.CANCELLED.value, 0)
        overdue = int(
            (
                await self.session.execute(
                    select(func.count(Task.id)).where(
                        Task.owner_id == owner_id,
                        Task.due_date.is_not(None),
                        Task.due_date < now,
                        Task.status.notin_([TaskStatus.DONE.value, TaskStatus.CANCELLED.value]),
                    )
                )
            ).scalar_one()
        )
        completed_this_week = int(
            (
                await self.session.execute(
                    select(func.count(Task.id)).where(
                        Task.owner_id == owner_id,
                        Task.status == TaskStatus.DONE.value,
                        Task.completed_at.is_not(None),
                        Task.completed_at >= week_start,
                        Task.completed_at < week_end,
                    )
                )
            ).scalar_one()
        )
        due_today = await self.tasks.due_today(owner_id, start=today_start, end=today_end)
        high_priority = await self.tasks.list(
            Task.owner_id == owner_id,
            Task.priority.in_(["HIGH", "URGENT"]),
            Task.status.notin_([TaskStatus.DONE.value, TaskStatus.CANCELLED.value]),
            order_by=Task.due_date.asc().nulls_last(),
            limit=6,
        )

        # ------------------------------------------------------ knowledge ---
        kb_count = await self.bases.count(
            KnowledgeBase.owner_id == owner_id, KnowledgeBase.is_archived.is_(False)
        )
        doc_counts = await self.documents.count_by_status(owner_id)
        total_docs = sum(doc_counts.values())
        chunk_totals = (
            await self.session.execute(
                select(
                    func.count(DocumentChunk.id),
                    func.coalesce(func.sum(DocumentChunk.token_estimate), 0),
                    func.coalesce(func.sum(DocumentChunk.char_count), 0),
                )
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(Document.owner_id == owner_id)
            )
        ).one()
        concept_count = int(
            (
                await self.session.execute(
                    select(func.count(Concept.id)).where(Concept.owner_id == owner_id)
                )
            ).scalar_one()
        )
        storage = storage_usage()

        # ------------------------------------------------------------- ai ---
        ai_metrics = await self.runs.metrics(owner_id, days=30)
        ai_today = int(
            (
                await self.session.execute(
                    select(func.count(AIRun.id)).where(
                        AIRun.owner_id == owner_id, AIRun.created_at >= today_start
                    )
                )
            ).scalar_one()
        )

        # ------------------------------------------------------- workspace ---
        project_count = await self.projects.count(
            Project.owner_id == owner_id, Project.status == ProjectStatus.ACTIVE.value
        )
        note_count = await self.notes.count(Note.owner_id == owner_id)

        recent_documents = await self.documents.recent(owner_id, limit=5)
        recent_notes = await self.notes.recent(owner_id, limit=5)
        recent_runs = await self.runs.list(
            AIRun.owner_id == owner_id, order_by=AIRun.created_at.desc(), limit=5
        )
        recent_tasks = await self.tasks.recent(owner_id, limit=5)
        activity = await self.activity.recent(owner_id, limit=12)

        llm, llm_info = build_llm_provider()
        _, embedding_info = build_embedding_provider()

        payload = {
            "cached": False,
            "generated_at": now.isoformat(),
            "tasks": {
                "total": total_tasks,
                "open": open_tasks,
                "done": done_tasks,
                "overdue": overdue,
                "completed_this_week": completed_this_week,
                "due_today": [_task_brief(t) for t in due_today],
                "high_priority": [_task_brief(t) for t in high_priority],
                "by_status": status_counts,
            },
            "knowledge": {
                "knowledge_bases": kb_count,
                "documents": total_docs,
                "documents_by_status": doc_counts,
                "chunks": int(chunk_totals[0] or 0),
                "chunk_tokens": int(chunk_totals[1] or 0),
                "chunk_chars": int(chunk_totals[2] or 0),
                "concepts": concept_count,
                "storage_files": storage["files"],
                "storage_bytes": storage["bytes"],
            },
            "ai": {
                **ai_metrics,
                "runs_today": ai_today,
                "cache": _cache_stats(),
            },
            "workspace": {
                "projects": project_count,
                "notes": note_count,
            },
            "recent": {
                "documents": [_document_brief(d) for d in recent_documents],
                "notes": [_note_brief(n) for n in recent_notes],
                "tasks": [_task_brief(t) for t in recent_tasks],
                "ai_runs": [_run_brief(r) for r in recent_runs],
                "activity": [_activity_brief(a) for a in activity],
            },
            "providers": {
                "llm": llm_info.as_dict(),
                "embedding": embedding_info.as_dict(),
                "llm_model": llm.model,
            },
            "jobs": await self.jobs.stats(owner_id),
        }
        await cache_set("stats", key, payload, settings.cache_ttl_stats_seconds)
        return payload


def _cache_stats() -> dict[str, Any]:
    from app.core.cache import cache_stats

    return cache_stats()


def _task_brief(task: Task) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "title": task.title,
        "status": task.status,
        "priority": task.priority,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "project_id": str(task.project_id) if task.project_id else None,
        "estimated_minutes": task.estimated_minutes,
        "source": task.source,
    }


def _document_brief(document: Document) -> dict[str, Any]:
    return {
        "id": str(document.id),
        "title": document.title,
        "status": document.status,
        "chunk_count": document.chunk_count,
        "knowledge_base_id": str(document.knowledge_base_id),
        "created_at": document.created_at.isoformat() if document.created_at else None,
    }


def _note_brief(note: Note) -> dict[str, Any]:
    return {
        "id": str(note.id),
        "title": note.title,
        "is_ai_generated": note.is_ai_generated,
        "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        "excerpt": (note.content_md or "")[:140],
    }


def _run_brief(run: AIRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "run_type": run.run_type,
        "status": run.status,
        "model": run.model,
        "provider": run.provider,
        "latency_ms": run.latency_ms,
        "total_tokens": run.total_tokens,
        "tool_call_count": run.tool_call_count,
        "retrieval_count": run.retrieval_count,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "prompt_name": run.prompt_name,
        "prompt_version": run.prompt_version,
    }


def _activity_brief(event: ActivityEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "event_type": event.event_type,
        "title": event.title,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "project_id": str(event.project_id) if event.project_id else None,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


class GraphService:
    """Projects the relational model into a node/edge graph.

    Deliberately a *real* projection of foreign keys and join tables rather than
    a decorative 3D visualisation.  Node types: project, knowledge_base,
    document, concept, task, note, ai_run.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def build(
        self,
        *,
        owner_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
        limit: int = 250,
        include_concepts: bool = True,
        include_runs: bool = True,
    ) -> dict[str, Any]:
        limit = max(20, min(limit, 800))
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []

        def add_node(node_id: str, node_type: str, label: str, **meta: Any) -> None:
            if node_id not in nodes and len(nodes) < limit:
                nodes[node_id] = {
                    "id": node_id,
                    "type": node_type,
                    "label": label[:120],
                    "meta": meta,
                }

        def add_edge(source: str, target: str, edge_type: str, weight: float = 1.0) -> None:
            if source in nodes and target in nodes:
                edges.append(
                    {"source": source, "target": target, "type": edge_type, "weight": weight}
                )

        # ------------------------------------------------------------ projects
        project_stmt = select(Project).where(Project.owner_id == owner_id)
        if project_id:
            project_stmt = project_stmt.where(Project.id == project_id)
        projects = list((await self.session.execute(project_stmt)).scalars().all())
        for project in projects:
            add_node(f"project:{project.id}", "project", project.name, color=project.color)

        # ----------------------------------------------------- knowledge bases
        kb_stmt = select(KnowledgeBase).where(KnowledgeBase.owner_id == owner_id)
        if project_id:
            kb_stmt = kb_stmt.where(KnowledgeBase.project_id == project_id)
        bases = list((await self.session.execute(kb_stmt)).scalars().all())
        for kb in bases:
            add_node(
                f"kb:{kb.id}",
                "knowledge_base",
                kb.name,
                color=kb.color,
                documents=0,
            )
            if kb.project_id:
                add_edge(f"project:{kb.project_id}", f"kb:{kb.id}", "contains")

        # ------------------------------------------------------------ documents
        doc_stmt = select(Document).where(
            Document.owner_id == owner_id, Document.status == DocumentStatus.COMPLETED.value
        )
        if project_id:
            doc_stmt = doc_stmt.where(Document.project_id == project_id)
        doc_stmt = doc_stmt.order_by(Document.created_at.desc()).limit(limit)
        documents = list((await self.session.execute(doc_stmt)).scalars().all())
        for document in documents:
            add_node(
                f"document:{document.id}",
                "document",
                document.title,
                chunks=document.chunk_count,
                status=document.status,
            )
            add_edge(f"kb:{document.knowledge_base_id}", f"document:{document.id}", "contains")
            if document.project_id:
                add_edge(f"project:{document.project_id}", f"document:{document.id}", "contains")

        # ------------------------------------------------------------- concepts
        if include_concepts and documents:
            rows = (
                await self.session.execute(
                    select(Concept.id, Concept.name, Concept.mention_count)
                    .where(Concept.owner_id == owner_id)
                    .order_by(Concept.mention_count.desc())
                    .limit(60)
                )
            ).all()
            concept_ids = [row[0] for row in rows]
            for concept_id, name, mentions in rows:
                add_node(f"concept:{concept_id}", "concept", name, mentions=int(mentions or 0))
            if concept_ids:
                links = (
                    await self.session.execute(
                        select(
                            DocumentConcept.document_id,
                            DocumentConcept.concept_id,
                            DocumentConcept.weight,
                        ).where(DocumentConcept.concept_id.in_(concept_ids))
                    )
                ).all()
                for document_id, concept_id, weight in links:
                    add_edge(
                        f"document:{document_id}",
                        f"concept:{concept_id}",
                        "mentions",
                        float(weight or 1.0),
                    )

        # ---------------------------------------------------------------- tasks
        task_stmt = select(Task).where(Task.owner_id == owner_id)
        if project_id:
            task_stmt = task_stmt.where(Task.project_id == project_id)
        tasks = list((await self.session.execute(task_stmt.limit(limit))).scalars().all())
        for task in tasks:
            add_node(
                f"task:{task.id}",
                "task",
                task.title,
                status=task.status,
                priority=task.priority,
            )
            if task.project_id:
                add_edge(f"project:{task.project_id}", f"task:{task.id}", "contains")
            if task.parent_id:
                add_edge(f"task:{task.parent_id}", f"task:{task.id}", "subtask_of")

        # --------------------------------------------------- knowledge ↔ task
        if tasks and documents:
            links = (
                await self.session.execute(
                    select(TaskDocument.task_id, TaskDocument.document_id, TaskDocument.relevance)
                )
            ).all()
            for task_id, document_id, relevance in links:
                add_edge(
                    f"task:{task_id}",
                    f"document:{document_id}",
                    "uses_knowledge",
                    float(relevance or 1.0),
                )

        # ---------------------------------------------------------------- notes
        notes = list(
            (await self.session.execute(select(Note).where(Note.owner_id == owner_id).limit(60)))
            .scalars()
            .all()
        )
        for note in notes:
            add_node(
                f"note:{note.id}",
                "note",
                note.title,
                ai_generated=note.is_ai_generated,
            )
            if note.project_id:
                add_edge(f"project:{note.project_id}", f"note:{note.id}", "contains")
            if note.source_document_id:
                add_edge(f"document:{note.source_document_id}", f"note:{note.id}", "produced")

        # ------------------------------------------------------------- ai runs
        if include_runs:
            run_stmt = select(AIRun).where(AIRun.owner_id == owner_id)
            if project_id:
                run_stmt = run_stmt.where(AIRun.project_id == project_id)
            runs = list(
                (await self.session.execute(run_stmt.order_by(AIRun.created_at.desc()).limit(40)))
                .scalars()
                .all()
            )
            for run in runs:
                add_node(
                    f"run:{run.run_id}",
                    "ai_run",
                    f"{run.run_type} · {run.model or run.provider}",
                    status=run.status,
                    tokens=run.total_tokens,
                )
                if run.project_id:
                    add_edge(f"project:{run.project_id}", f"run:{run.run_id}", "contains")
            run_ids = [r.id for r in runs]
            if run_ids:
                sources = (
                    await self.session.execute(
                        select(AIRunSource.ai_run_id, AIRunSource.document_id)
                        .where(AIRunSource.ai_run_id.in_(run_ids))
                        .distinct()
                    )
                ).all()
                run_id_map = {r.id: r.run_id for r in runs}
                for ai_run_id, document_id in sources:
                    if document_id:
                        add_edge(
                            f"run:{run_id_map[ai_run_id]}",
                            f"document:{document_id}",
                            "retrieved",
                        )

        # Task ↔ AI run (tasks created by an agent run in the same project)
        node_list = list(nodes.values())
        type_counts: dict[str, int] = {}
        for node in node_list:
            type_counts[node["type"]] = type_counts.get(node["type"], 0) + 1

        return {
            "nodes": node_list,
            "edges": edges,
            "stats": {
                "nodes": len(node_list),
                "edges": len(edges),
                "by_type": type_counts,
                "truncated": len(node_list) >= limit,
            },
        }


__all__ = ["GraphService", "StatsService", "_run_brief", "_task_brief"]

"""Repositories for projects, tasks and notes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, delete, func, or_, select, update

from app.domain.enums import TaskStatus
from app.models.knowledge import Document
from app.models.note import Note
from app.models.project import Project
from app.models.task import Task, TaskDependency, TaskDocument
from app.repositories.base import BaseRepository, apply_search


class ProjectRepository(BaseRepository[Project]):
    model = Project

    def query(
        self,
        owner_id: uuid.UUID,
        *,
        status: str | None = None,
        search: str | None = None,
    ) -> Select[Any]:
        stmt = select(Project).where(Project.owner_id == owner_id)
        if status:
            stmt = stmt.where(Project.status == status)
        stmt = apply_search(stmt, Project.name, search)
        return stmt.order_by(Project.updated_at.desc())

    async def get_owned(
        self, project_id: uuid.UUID | None, owner_id: uuid.UUID
    ) -> Project | None:
        return await self.get_or_none(Project.id == project_id, Project.owner_id == owner_id)

    async def stats(self, project_id: uuid.UUID) -> dict[str, int]:
        from app.models.ai import AIRun
        from app.models.knowledge import Document, KnowledgeBase

        # Conditions below target KnowledgeBase / Document / AIRun while
        # ``self.model`` is Project, so the explicit ``count_of`` helper is used.
        kb_count = await self.count_of(
            KnowledgeBase, KnowledgeBase.project_id == project_id
        )
        doc_row = (
            await self.session.execute(
                select(
                    func.count(Document.id),
                    func.coalesce(func.sum(Document.chunk_count), 0),
                ).where(Document.project_id == project_id)
            )
        ).one()
        task_rows = (
            await self.session.execute(
                select(Task.status, func.count(Task.id))
                .where(Task.project_id == project_id)
                .group_by(Task.status)
            )
        ).all()
        tasks = {str(status): int(count) for status, count in task_rows}
        notes = await self.count_of(Note, Note.project_id == project_id)
        ai_runs = await self.count_of(AIRun, AIRun.project_id == project_id)
        done = tasks.get(TaskStatus.DONE.value, 0)
        total = sum(tasks.values())
        return {
            "knowledge_bases": kb_count,
            "documents": int(doc_row[0] or 0),
            "chunks": int(doc_row[1] or 0),
            "tasks_total": total,
            "tasks_done": done,
            "tasks_open": total - done,
            "notes": notes,
            "ai_runs": ai_runs,
        }


class TaskRepository(BaseRepository[Task]):
    model = Task

    def query(
        self,
        owner_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None = None,
        status: str | None = None,
        priority: str | None = None,
        parent_id: uuid.UUID | None = None,
        roots_only: bool = False,
        search: str | None = None,
        due_before: datetime | None = None,
    ) -> Select[Any]:
        stmt = select(Task).where(Task.owner_id == owner_id)
        if project_id:
            stmt = stmt.where(Task.project_id == project_id)
        if status:
            stmt = stmt.where(Task.status == status)
        if priority:
            stmt = stmt.where(Task.priority == priority)
        if parent_id:
            stmt = stmt.where(Task.parent_id == parent_id)
        elif roots_only:
            stmt = stmt.where(Task.parent_id.is_(None))
        if due_before:
            stmt = stmt.where(Task.due_date.is_not(None), Task.due_date <= due_before)
        if search:
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(Task.title.ilike(pattern), Task.description.ilike(pattern))
            )
        return stmt.order_by(Task.position, Task.created_at)

    async def get_owned(self, task_id: uuid.UUID | None, owner_id: uuid.UUID) -> Task | None:
        return await self.get_or_none(Task.id == task_id, Task.owner_id == owner_id)

    async def children(self, parent_id: uuid.UUID) -> list[Task]:
        return await self.list(Task.parent_id == parent_id, order_by=Task.position)

    async def all_for_project(self, owner_id: uuid.UUID, project_id: uuid.UUID | None) -> list[Task]:
        stmt = select(Task).where(Task.owner_id == owner_id)
        if project_id:
            stmt = stmt.where(Task.project_id == project_id)
        return list((await self.session.execute(stmt.order_by(Task.position))).scalars().all())

    async def status_counts(self, owner_id: uuid.UUID, project_id: uuid.UUID | None = None) -> dict[str, int]:
        stmt = select(Task.status, func.count(Task.id)).where(Task.owner_id == owner_id)
        if project_id:
            stmt = stmt.where(Task.project_id == project_id)
        rows = (await self.session.execute(stmt.group_by(Task.status))).all()
        return {str(status): int(count) for status, count in rows}

    async def next_position(self, owner_id: uuid.UUID, parent_id: uuid.UUID | None) -> int:
        stmt = select(func.coalesce(func.max(Task.position), 0)).where(Task.owner_id == owner_id)
        stmt = stmt.where(
            Task.parent_id == parent_id if parent_id else Task.parent_id.is_(None)
        )
        return int((await self.session.execute(stmt)).scalar_one()) + 1

    async def due_today(self, owner_id: uuid.UUID, *, start: datetime, end: datetime) -> list[Task]:
        return await self.list(
            Task.owner_id == owner_id,
            Task.due_date.is_not(None),
            Task.due_date >= start,
            Task.due_date < end,
            Task.status.notin_([TaskStatus.DONE.value, TaskStatus.CANCELLED.value]),
            order_by=Task.due_date,
        )

    async def recent(self, owner_id: uuid.UUID, limit: int = 6) -> list[Task]:
        return await self.list(
            Task.owner_id == owner_id, order_by=Task.updated_at.desc(), limit=limit
        )

    # --------------------------------------------------------------- relations --
    async def add_dependency(self, task_id: uuid.UUID, depends_on_id: uuid.UUID) -> None:
        if task_id == depends_on_id:
            return
        exists = await self.session.execute(
            select(TaskDependency).where(
                TaskDependency.task_id == task_id,
                TaskDependency.depends_on_task_id == depends_on_id,
            )
        )
        if exists.scalar_one_or_none() is None:
            self.session.add(
                TaskDependency(task_id=task_id, depends_on_task_id=depends_on_id)
            )

    async def remove_dependency(self, task_id: uuid.UUID, dependency_id: uuid.UUID) -> int:
        result = await self.session.execute(
            delete(TaskDependency).where(
                TaskDependency.task_id == task_id,
                TaskDependency.depends_on_task_id == dependency_id,
            )
        )
        return int(result.rowcount or 0)

    async def dependencies_of(self, task_id: uuid.UUID) -> list[Task]:
        stmt = (
            select(Task)
            .join(TaskDependency, TaskDependency.depends_on_task_id == Task.id)
            .where(TaskDependency.task_id == task_id)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def dependents_of(self, task_id: uuid.UUID) -> list[Task]:
        stmt = (
            select(Task)
            .join(TaskDependency, TaskDependency.task_id == Task.id)
            .where(TaskDependency.depends_on_task_id == task_id)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def link_document(
        self, task_id: uuid.UUID, document_id: uuid.UUID, relevance: float = 1.0
    ) -> None:
        exists = await self.session.get(
            TaskDocument, {"task_id": task_id, "document_id": document_id}
        )
        if exists is None:
            self.session.add(
                TaskDocument(task_id=task_id, document_id=document_id, relevance=relevance)
            )

    async def documents_of(self, task_id: uuid.UUID) -> list[Document]:
        stmt = (
            select(Document)
            .join(TaskDocument, TaskDocument.document_id == Document.id)
            .where(TaskDocument.task_id == task_id)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def mark_done(self, task: Task) -> Task:
        task.status = TaskStatus.DONE.value
        task.completed_at = datetime.now(UTC)
        await self.session.flush()
        return task

    async def blocked_ids(self, owner_id: uuid.UUID) -> set[uuid.UUID]:
        """Tasks whose dependencies are not yet done."""
        stmt = (
            select(TaskDependency.task_id)
            .join(Task, Task.id == TaskDependency.depends_on_task_id)
            .where(
                Task.owner_id == owner_id,
                Task.status != TaskStatus.DONE.value,
            )
        )
        return set((await self.session.execute(stmt)).scalars().all())

    async def reassign_parent(self, old_parent: uuid.UUID, new_parent: uuid.UUID | None) -> int:
        result = await self.session.execute(
            update(Task).where(Task.parent_id == old_parent).values(parent_id=new_parent)
        )
        return int(result.rowcount or 0)


class NoteRepository(BaseRepository[Note]):
    model = Note

    def query(
        self,
        owner_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None = None,
        knowledge_base_id: uuid.UUID | None = None,
        search: str | None = None,
        pinned_first: bool = True,
    ) -> Select[Any]:
        stmt = select(Note).where(Note.owner_id == owner_id)
        if project_id:
            stmt = stmt.where(Note.project_id == project_id)
        if knowledge_base_id:
            stmt = stmt.where(Note.knowledge_base_id == knowledge_base_id)
        if search:
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(Note.title.ilike(pattern), Note.content_md.ilike(pattern))
            )
        if pinned_first:
            stmt = stmt.order_by(Note.is_pinned.desc(), Note.updated_at.desc())
        else:
            stmt = stmt.order_by(Note.updated_at.desc())
        return stmt

    async def get_owned(self, note_id: uuid.UUID | None, owner_id: uuid.UUID) -> Note | None:
        return await self.get_or_none(Note.id == note_id, Note.owner_id == owner_id)

    async def recent(self, owner_id: uuid.UUID, limit: int = 5) -> list[Note]:
        return await self.list(
            Note.owner_id == owner_id, order_by=Note.updated_at.desc(), limit=limit
        )

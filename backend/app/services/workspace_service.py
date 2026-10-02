"""Project, knowledge base and note services."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.enums import ProjectStatus
from app.models.knowledge import KnowledgeBase
from app.models.note import Note
from app.models.project import Project
from app.repositories.ai_repo import ActivityRepository
from app.repositories.knowledge_repo import KnowledgeBaseRepository, TagRepository
from app.repositories.work_repo import NoteRepository, ProjectRepository

logger = get_logger(__name__)


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.activity = ActivityRepository(session)

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        name: str,
        description: str = "",
        icon: str = "folder",
        color: str = "#6366f1",
    ) -> Project:
        clean = (name or "").strip()
        if not clean:
            raise ValidationError("Project name must not be empty", details={"field": "name"})
        existing = await self.projects.get_or_none(
            Project.owner_id == owner_id, Project.name == clean
        )
        if existing:
            raise ConflictError("A project with this name already exists")
        project = Project(
            owner_id=owner_id,
            name=clean[:160],
            description=description or "",
            icon=icon or "folder",
            color=color or "#6366f1",
            status=ProjectStatus.ACTIVE.value,
        )
        self.session.add(project)
        await self.session.flush()
        await self.activity.record(
            owner_id=owner_id,
            event_type="project.created",
            title=f"新建项目：{project.name}",
            entity_type="project",
            entity_id=str(project.id),
            project_id=project.id,
        )
        return project

    async def get(self, *, owner_id: uuid.UUID, project_id: uuid.UUID) -> Project:
        project = await self.projects.get_owned(project_id, owner_id)
        if project is None:
            raise NotFoundError("Project not found")
        return project

    async def update(
        self, *, owner_id: uuid.UUID, project_id: uuid.UUID, **changes: Any
    ) -> Project:
        project = await self.get(owner_id=owner_id, project_id=project_id)
        if changes.get("name"):
            clean = str(changes["name"]).strip()[:160]
            duplicate = await self.projects.get_or_none(
                Project.owner_id == owner_id,
                Project.name == clean,
                Project.id != project.id,
            )
            if duplicate:
                raise ConflictError("A project with this name already exists")
            project.name = clean
        for field in ("description", "icon", "color"):
            if changes.get(field) is not None:
                setattr(project, field, changes[field])
        if changes.get("status") is not None:
            status = str(changes["status"]).upper()
            if status not in {s.value for s in ProjectStatus}:
                raise ValidationError(f"Unknown project status '{status}'")
            project.status = status
        await self.session.flush()
        return project

    async def delete(self, *, owner_id: uuid.UUID, project_id: uuid.UUID) -> None:
        project = await self.get(owner_id=owner_id, project_id=project_id)
        name = project.name
        await self.session.delete(project)
        await self.session.flush()
        await self.activity.record(
            owner_id=owner_id,
            event_type="project.deleted",
            title=f"删除项目：{name}",
            entity_type="project",
            entity_id=str(project_id),
        )

    async def stats(self, *, owner_id: uuid.UUID, project_id: uuid.UUID) -> dict[str, int]:
        await self.get(owner_id=owner_id, project_id=project_id)
        return await self.projects.stats(project_id)


class KnowledgeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.bases = KnowledgeBaseRepository(session)
        self.tags = TagRepository(session)
        self.projects = ProjectRepository(session)
        self.activity = ActivityRepository(session)

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        name: str,
        description: str = "",
        icon: str = "book",
        color: str = "#8b5cf6",
        project_id: uuid.UUID | None = None,
    ) -> KnowledgeBase:
        clean = (name or "").strip()
        if not clean:
            raise ValidationError("Knowledge base name must not be empty")
        if await self.bases.name_taken(owner_id, clean):
            raise ConflictError("A knowledge base with this name already exists")
        if project_id and await self.projects.get_owned(project_id, owner_id) is None:
            raise NotFoundError("Project not found")

        from app.ai.providers.factory import build_embedding_provider

        _, info = build_embedding_provider()
        kb = KnowledgeBase(
            owner_id=owner_id,
            project_id=project_id,
            name=clean[:160],
            description=description or "",
            icon=icon or "book",
            color=color or "#8b5cf6",
            embedding_model=info.model,
        )
        self.session.add(kb)
        await self.session.flush()
        await self.activity.record(
            owner_id=owner_id,
            event_type="knowledge_base.created",
            title=f"新建知识库：{kb.name}",
            entity_type="knowledge_base",
            entity_id=str(kb.id),
            project_id=project_id,
        )
        return kb

    async def get(self, *, owner_id: uuid.UUID, kb_id: uuid.UUID) -> KnowledgeBase:
        kb = await self.bases.get_owned(kb_id, owner_id)
        if kb is None:
            raise NotFoundError("Knowledge base not found")
        return kb

    async def update(
        self, *, owner_id: uuid.UUID, kb_id: uuid.UUID, **changes: Any
    ) -> KnowledgeBase:
        kb = await self.get(owner_id=owner_id, kb_id=kb_id)
        if changes.get("name"):
            clean = str(changes["name"]).strip()[:160]
            if await self.bases.name_taken(owner_id, clean, exclude_id=kb.id):
                raise ConflictError("A knowledge base with this name already exists")
            kb.name = clean
        for field in ("description", "icon", "color"):
            if changes.get(field) is not None:
                setattr(kb, field, changes[field])
        if "project_id" in changes:
            project_id = changes["project_id"]
            if project_id and await self.projects.get_owned(project_id, owner_id) is None:
                raise NotFoundError("Project not found")
            kb.project_id = project_id
        if changes.get("is_archived") is not None:
            kb.is_archived = bool(changes["is_archived"])
        await self.session.flush()
        return kb

    async def delete(self, *, owner_id: uuid.UUID, kb_id: uuid.UUID) -> None:
        kb = await self.get(owner_id=owner_id, kb_id=kb_id)
        name = kb.name
        from app.infrastructure.vector_store import get_vector_store

        try:
            await get_vector_store().delete_by_knowledge_base(kb.id)
        except Exception as exc:
            logger.warning(
                "vector_cleanup_failed_on_kb_delete",
                extra={"kb_id": str(kb.id), "error": str(exc)},
            )
        await self.session.delete(kb)
        await self.session.flush()
        await self.activity.record(
            owner_id=owner_id,
            event_type="knowledge_base.deleted",
            title=f"删除知识库：{name}",
            entity_type="knowledge_base",
            entity_id=str(kb_id),
        )

    async def stats(self, *, owner_id: uuid.UUID, kb_id: uuid.UUID) -> dict[str, Any]:
        await self.get(owner_id=owner_id, kb_id=kb_id)
        return await self.bases.stats(kb_id)

    async def list_tags(self, owner_id: uuid.UUID) -> list[Any]:
        return await self.tags.for_owner(owner_id)


class NoteService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.notes = NoteRepository(session)
        self.projects = ProjectRepository(session)
        self.activity = ActivityRepository(session)

    async def create_note(
        self,
        *,
        owner_id: uuid.UUID,
        title: str,
        content_md: str = "",
        project_id: uuid.UUID | None = None,
        knowledge_base_id: uuid.UUID | None = None,
        source_document_id: uuid.UUID | None = None,
        source_run_id: str = "",
        is_ai_generated: bool = False,
        is_pinned: bool = False,
    ) -> Note:
        clean = (title or "").strip() or "未命名笔记"
        if project_id and await self.projects.get_owned(project_id, owner_id) is None:
            raise NotFoundError("Project not found")
        note = Note(
            owner_id=owner_id,
            project_id=project_id,
            knowledge_base_id=knowledge_base_id,
            source_document_id=source_document_id,
            source_run_id=source_run_id,
            title=clean[:300],
            content_md=content_md or "",
            is_ai_generated=is_ai_generated,
            is_pinned=is_pinned,
        )
        self.session.add(note)
        await self.session.flush()
        await self.activity.record(
            owner_id=owner_id,
            event_type="note.created",
            title=f"新建笔记：{note.title}",
            entity_type="note",
            entity_id=str(note.id),
            project_id=project_id,
            metadata={"ai_generated": is_ai_generated},
        )
        return note

    async def get(self, *, owner_id: uuid.UUID, note_id: uuid.UUID) -> Note:
        note = await self.notes.get_owned(note_id, owner_id)
        if note is None:
            raise NotFoundError("Note not found")
        return note

    async def update(
        self, *, owner_id: uuid.UUID, note_id: uuid.UUID, **changes: Any
    ) -> Note:
        note = await self.get(owner_id=owner_id, note_id=note_id)
        if changes.get("title"):
            note.title = str(changes["title"]).strip()[:300]
        if changes.get("content_md") is not None:
            note.content_md = str(changes["content_md"])
        if changes.get("is_pinned") is not None:
            note.is_pinned = bool(changes["is_pinned"])
        if "project_id" in changes:
            project_id = changes["project_id"]
            if project_id and await self.projects.get_owned(project_id, owner_id) is None:
                raise NotFoundError("Project not found")
            note.project_id = project_id
        if "knowledge_base_id" in changes:
            note.knowledge_base_id = changes["knowledge_base_id"]
        await self.session.flush()
        return note

    async def delete(self, *, owner_id: uuid.UUID, note_id: uuid.UUID) -> None:
        note = await self.get(owner_id=owner_id, note_id=note_id)
        title = note.title
        await self.session.delete(note)
        await self.session.flush()
        await self.activity.record(
            owner_id=owner_id,
            event_type="note.deleted",
            title=f"删除笔记：{title}",
            entity_type="note",
            entity_id=str(note_id),
        )

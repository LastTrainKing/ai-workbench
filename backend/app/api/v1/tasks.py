"""Task and note endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import CurrentUser, DbSession, Pagination
from app.core.response import build_page, ok
from app.domain.enums import TaskPriority, TaskSource, TaskStatus
from app.repositories.work_repo import NoteRepository, TaskRepository
from app.schemas.project import ProjectStats  # noqa: F401  (kept for symmetry)
from app.services.task_service import TaskService
from app.services.workspace_service import NoteService

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ---------------------------------------------------------------- schemas ----
class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20_000)
    project_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.TODO
    estimated_minutes: int = Field(default=0, ge=0, le=100_000)
    actual_minutes: int = Field(default=0, ge=0, le=100_000)
    due_date: str | None = Field(default=None, description="ISO date or relative like +7d")
    completion_criteria: str = Field(default="", max_length=4000)
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    depends_on_ids: list[uuid.UUID] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20_000)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    estimated_minutes: int | None = Field(default=None, ge=0, le=100_000)
    actual_minutes: int | None = Field(default=None, ge=0, le=100_000)
    due_date: str | None = None
    completion_criteria: str | None = Field(default=None, max_length=4000)
    project_id: uuid.UUID | None = None
    is_pinned: bool | None = None
    position: int | None = Field(default=None, ge=0)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    parent_id: uuid.UUID | None
    title: str
    description: str
    completion_criteria: str
    status: str
    priority: str
    source: str
    ai_suggestion: str
    due_date: object | None = None
    estimated_minutes: int
    actual_minutes: int
    depth: int
    is_pinned: bool
    completed_at: object | None = None
    created_at: object | None = None
    updated_at: object | None = None


class DependencyIn(BaseModel):
    depends_on_task_id: uuid.UUID


class KnowledgeLinkIn(BaseModel):
    document_id: uuid.UUID
    relevance: float = Field(default=1.0, ge=0.0, le=1.0)


class CompleteIn(BaseModel):
    actual_minutes: int | None = Field(default=None, ge=0, le=100_000)


class NoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content_md: str = Field(default="", max_length=400_000)
    project_id: uuid.UUID | None = None
    knowledge_base_id: uuid.UUID | None = None
    is_pinned: bool = False


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content_md: str | None = Field(default=None, max_length=400_000)
    project_id: uuid.UUID | None = None
    knowledge_base_id: uuid.UUID | None = None
    is_pinned: bool | None = None


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    knowledge_base_id: uuid.UUID | None
    source_document_id: uuid.UUID | None
    source_run_id: str
    title: str
    content_md: str
    is_pinned: bool
    is_ai_generated: bool
    created_at: object | None = None
    updated_at: object | None = None


# ------------------------------------------------------------------ routes ---
@router.get("", summary="List tasks")
async def list_tasks(
    user: CurrentUser,
    session: DbSession,
    pagination: Pagination,
    project_id: uuid.UUID | None = None,
    status_filter: TaskStatus | None = Query(None, alias="status"),
    priority: TaskPriority | None = None,
    parent_id: uuid.UUID | None = None,
    roots_only: bool = False,
    q: str | None = Query(None, max_length=200),
) -> dict:
    repo = TaskRepository(session)
    stmt = repo.query(
        user.id,
        project_id=project_id,
        status=status_filter.value if status_filter else None,
        priority=priority.value if priority else None,
        parent_id=parent_id,
        roots_only=roots_only,
        search=q,
    )
    rows, total = await repo.paginate(
        stmt, page=pagination.page, page_size=pagination.page_size
    )
    return ok(
        build_page(
            [TaskOut.model_validate(row).model_dump(mode="json") for row in rows],
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
        ).model_dump(mode="json")
    )


@router.get("/tree", summary="Full task tree (goal → phase → task → subtask)")
async def task_tree(
    user: CurrentUser, session: DbSession, project_id: uuid.UUID | None = None
) -> dict:
    tree = await TaskService(session).tree(owner_id=user.id, project_id=project_id)
    return ok({"tree": tree, "count": _count_nodes(tree)})


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a task")
async def create_task(payload: TaskCreate, user: CurrentUser, session: DbSession) -> dict:
    task = await TaskService(session).create_task(
        owner_id=user.id,
        title=payload.title,
        description=payload.description,
        project_id=payload.project_id,
        parent_id=payload.parent_id,
        priority=payload.priority.value,
        status=payload.status.value,
        estimated_minutes=payload.estimated_minutes,
        actual_minutes=payload.actual_minutes,
        due_date_raw=payload.due_date,
        completion_criteria=payload.completion_criteria,
        source=TaskSource.MANUAL,
        document_ids=payload.document_ids,
        depends_on_ids=payload.depends_on_ids,
    )
    return ok(TaskOut.model_validate(task).model_dump(mode="json"), message="任务已创建")


@router.get("/{task_id}", summary="Task detail with children, dependencies and knowledge links")
async def get_task(task_id: uuid.UUID, user: CurrentUser, session: DbSession) -> dict:
    detail = await TaskService(session).detail(owner_id=user.id, task_id=task_id)
    return ok(
        {
            "task": TaskOut.model_validate(detail["task"]).model_dump(mode="json"),
            "children": [
                TaskOut.model_validate(child).model_dump(mode="json")
                for child in detail["children"]
            ],
            "dependencies": [
                TaskOut.model_validate(dep).model_dump(mode="json")
                for dep in detail["dependencies"]
            ],
            "dependents": [
                TaskOut.model_validate(dep).model_dump(mode="json")
                for dep in detail["dependents"]
            ],
            "documents": [
                {"id": str(doc.id), "title": doc.title, "status": doc.status}
                for doc in detail["documents"]
            ],
            "is_blocked": detail["is_blocked"],
        }
    )


@router.patch("/{task_id}", summary="Update a task")
async def update_task(
    task_id: uuid.UUID, payload: TaskUpdate, user: CurrentUser, session: DbSession
) -> dict:
    data = payload.model_dump(exclude_unset=True)
    for key in ("status", "priority"):
        if key in data and data[key] is not None:
            data[key] = data[key].value if hasattr(data[key], "value") else data[key]
    task = await TaskService(session).update_task(owner_id=user.id, task_id=task_id, **data)
    return ok(TaskOut.model_validate(task).model_dump(mode="json"))


@router.post("/{task_id}/complete", summary="Mark a task as done")
async def complete_task(
    task_id: uuid.UUID, payload: CompleteIn, user: CurrentUser, session: DbSession
) -> dict:
    task = await TaskService(session).complete_task(
        owner_id=user.id, task_id=task_id, actual_minutes=payload.actual_minutes
    )
    return ok(TaskOut.model_validate(task).model_dump(mode="json"), message="任务已完成")


@router.delete("/{task_id}", summary="Delete a task and its subtasks")
async def delete_task(task_id: uuid.UUID, user: CurrentUser, session: DbSession) -> dict:
    await TaskService(session).delete_task(owner_id=user.id, task_id=task_id)
    return ok({"deleted": True}, message="任务已删除")


@router.post("/{task_id}/dependencies", summary="Add a dependency (cycle-safe)")
async def add_dependency(
    task_id: uuid.UUID, payload: DependencyIn, user: CurrentUser, session: DbSession
) -> dict:
    await TaskService(session).add_dependency(
        owner_id=user.id, task_id=task_id, depends_on_id=payload.depends_on_task_id
    )
    return ok({"added": True}, message="依赖已建立")


@router.delete("/{task_id}/dependencies/{dependency_id}", summary="Remove a dependency")
async def remove_dependency(
    task_id: uuid.UUID, dependency_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict:
    removed = await TaskService(session).remove_dependency(
        owner_id=user.id, task_id=task_id, dependency_id=dependency_id
    )
    return ok({"removed": removed})


@router.post("/{task_id}/knowledge", summary="Link a knowledge document to a task")
async def link_knowledge(
    task_id: uuid.UUID, payload: KnowledgeLinkIn, user: CurrentUser, session: DbSession
) -> dict:
    await TaskService(session).link_document(
        owner_id=user.id,
        task_id=task_id,
        document_id=payload.document_id,
        relevance=payload.relevance,
    )
    return ok({"linked": True}, message="知识关联已建立")


@router.delete("/{task_id}/knowledge/{document_id}", summary="Remove a knowledge link")
async def unlink_knowledge(
    task_id: uuid.UUID, document_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict:
    removed = await TaskService(session).unlink_document(
        owner_id=user.id, task_id=task_id, document_id=document_id
    )
    return ok({"removed": removed})


# ===========================================================================
# Notes
# ===========================================================================
notes_router = APIRouter(prefix="/notes", tags=["notes"])


@notes_router.get("", summary="List notes")
async def list_notes(
    user: CurrentUser,
    session: DbSession,
    pagination: Pagination,
    project_id: uuid.UUID | None = None,
    knowledge_base_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=200),
) -> dict:
    repo = NoteRepository(session)
    stmt = repo.query(
        user.id, project_id=project_id, knowledge_base_id=knowledge_base_id, search=q
    )
    rows, total = await repo.paginate(
        stmt, page=pagination.page, page_size=pagination.page_size
    )
    return ok(
        build_page(
            [NoteOut.model_validate(row).model_dump(mode="json") for row in rows],
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
        ).model_dump(mode="json")
    )


@notes_router.post("", status_code=status.HTTP_201_CREATED, summary="Create a note")
async def create_note(payload: NoteCreate, user: CurrentUser, session: DbSession) -> dict:
    note = await NoteService(session).create_note(
        owner_id=user.id,
        title=payload.title,
        content_md=payload.content_md,
        project_id=payload.project_id,
        knowledge_base_id=payload.knowledge_base_id,
        is_pinned=payload.is_pinned,
    )
    return ok(NoteOut.model_validate(note).model_dump(mode="json"), message="笔记已创建")


@notes_router.get("/{note_id}", summary="Read a note")
async def get_note(note_id: uuid.UUID, user: CurrentUser, session: DbSession) -> dict:
    note = await NoteService(session).get(owner_id=user.id, note_id=note_id)
    return ok(NoteOut.model_validate(note).model_dump(mode="json"))


@notes_router.patch("/{note_id}", summary="Update a note")
async def update_note(
    note_id: uuid.UUID, payload: NoteUpdate, user: CurrentUser, session: DbSession
) -> dict:
    note = await NoteService(session).update(
        owner_id=user.id, note_id=note_id, **payload.model_dump(exclude_unset=True)
    )
    return ok(NoteOut.model_validate(note).model_dump(mode="json"))


@notes_router.delete("/{note_id}", summary="Delete a note")
async def delete_note(note_id: uuid.UUID, user: CurrentUser, session: DbSession) -> dict:
    await NoteService(session).delete(owner_id=user.id, note_id=note_id)
    return ok({"deleted": True})


def _count_nodes(tree: list[dict]) -> int:
    total = 0
    stack = list(tree)
    while stack:
        node = stack.pop()
        total += 1
        stack.extend(node.get("children", []))
    return total

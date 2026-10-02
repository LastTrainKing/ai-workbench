"""Project endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession, Pagination
from app.core.response import build_page, ok
from app.domain.enums import ProjectStatus
from app.repositories.work_repo import ProjectRepository
from app.schemas.project import (
    ProjectCreate,
    ProjectDetail,
    ProjectOut,
    ProjectStats,
    ProjectUpdate,
)
from app.services.workspace_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", summary="List projects")
async def list_projects(
    user: CurrentUser,
    session: DbSession,
    pagination: Pagination,
    status_filter: ProjectStatus | None = Query(None, alias="status"),
    q: str | None = Query(None, max_length=120),
) -> dict:
    repo = ProjectRepository(session)
    stmt = repo.query(user.id, status=status_filter.value if status_filter else None, search=q)
    rows, total = await repo.paginate(
        stmt, page=pagination.page, page_size=pagination.page_size
    )
    page = build_page(
        [ProjectOut.model_validate(row).model_dump(mode="json") for row in rows],
        page=pagination.page,
        page_size=pagination.page_size,
        total=total,
    )
    return ok(page.model_dump(mode="json"))


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a project")
async def create_project(payload: ProjectCreate, user: CurrentUser, session: DbSession) -> dict:
    project = await ProjectService(session).create(
        owner_id=user.id,
        name=payload.name,
        description=payload.description,
        icon=payload.icon,
        color=payload.color,
    )
    return ok(ProjectOut.model_validate(project).model_dump(mode="json"), message="项目已创建")


@router.get("/{project_id}", summary="Project detail with aggregate statistics")
async def get_project(project_id: uuid.UUID, user: CurrentUser, session: DbSession) -> dict:
    service = ProjectService(session)
    project = await service.get(owner_id=user.id, project_id=project_id)
    stats = await service.stats(owner_id=user.id, project_id=project_id)
    detail = ProjectDetail(
        **ProjectOut.model_validate(project).model_dump(),
        stats=ProjectStats(**stats),
    )
    return ok(detail.model_dump(mode="json"))


@router.patch("/{project_id}", summary="Update a project")
async def update_project(
    project_id: uuid.UUID, payload: ProjectUpdate, user: CurrentUser, session: DbSession
) -> dict:
    project = await ProjectService(session).update(
        owner_id=user.id,
        project_id=project_id,
        **payload.model_dump(exclude_unset=True),
    )
    return ok(ProjectOut.model_validate(project).model_dump(mode="json"))


@router.delete("/{project_id}", status_code=status.HTTP_200_OK, summary="Delete a project")
async def delete_project(project_id: uuid.UUID, user: CurrentUser, session: DbSession) -> dict:
    await ProjectService(session).delete(owner_id=user.id, project_id=project_id)
    return ok({"deleted": True}, message="项目已删除")

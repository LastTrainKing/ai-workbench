"""Employee endpoints: the roster, the role catalogue, and what each employee may do."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import CurrentUser, DbSession
from app.core.response import ok
from app.services.employee_service import EmployeeService

router = APIRouter(prefix="/employees", tags=["employees"])


# ---------------------------------------------------------------- schemas ----
class EmployeeCreate(BaseModel):
    role_id: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    overrides: dict[str, Any] = Field(default_factory=dict)


class EmployeeUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    role_id: str | None = Field(default=None, max_length=64)
    avatar: str | None = Field(default=None, max_length=64)
    mission: str | None = Field(default=None, max_length=4000)
    persona: str | None = Field(default=None, max_length=4000)
    responsibilities: list[str] | None = None
    out_of_scope: list[str] | None = None
    service_scope: str | None = Field(default=None, max_length=4000)
    knowledge_scope: dict[str, Any] | None = None
    memory_scope: dict[str, Any] | None = None
    permission_profile: dict[str, Any] | None = None
    skills: list[str] | None = None
    model_policy: dict[str, Any] | None = None
    status: str | None = None


class PersonaReviewRequest(BaseModel):
    text: str = Field(default="", max_length=8000)


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    role_id: str
    avatar: str
    mission: str
    persona: str
    responsibilities: list[str]
    out_of_scope: list[str]
    service_scope: str
    knowledge_scope: dict[str, Any]
    memory_scope: dict[str, Any]
    permission_profile: dict[str, Any]
    skills: list[str]
    model_policy: dict[str, Any]
    status: str
    version: int
    created_from_template: bool
    archived: bool


def _view(service: EmployeeService, employee: Any) -> dict[str, Any]:
    """One employee as the API returns it, with the derived fields the window needs.

    ``unknown_skills`` and ``effective_policy`` are computed rather than stored: the first
    because a tool can be removed in a later version, the second because the user's own
    policy can change and the employee's answer has to move with it.
    """
    _, narrowing = service.effective_policy(employee)
    return {
        **EmployeeOut.model_validate(employee).model_dump(mode="json"),
        "unknown_skills": service.unknown_skills(employee),
        "effective_narrowing": narrowing,
    }


# ----------------------------------------------------------------- routes ----
@router.get("", summary="The roster")
async def list_employees(
    user: CurrentUser,
    session: DbSession,
    status_filter: str | None = Query(default=None, alias="status"),
    include_archived: bool = Query(default=False),
    search: str | None = Query(default=None),
) -> dict[str, Any]:
    service = EmployeeService(session)
    employees = await service.list_employees(
        user.id, status=status_filter, include_archived=include_archived, search=search
    )
    return ok([_view(service, e) for e in employees])


@router.get("/templates", summary="The role catalogue, with each role's full contract")
async def list_templates(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    return ok(EmployeeService(session).template_catalogue())


@router.get("/hall", summary="Counts for the employee hall")
async def hall(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    return ok(await EmployeeService(session).hall_summary(user.id))


@router.post("/persona/review", summary="Check a persona before saving it")
async def review_persona_text(
    payload: PersonaReviewRequest, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    """Findings, not a refusal.

    The persona cannot grant a permission — permissions are checked at execution time and
    never read this text — but a persona that *tells the employee it has* permission
    produces an employee that argues instead of asking. The caller shows the findings and
    the user decides, which is the same shape as every other risky write in this product.
    """
    return ok(EmployeeService(session).review_persona_text(payload.text))


@router.post("", status_code=status.HTTP_201_CREATED, summary="Hire an employee")
async def create_employee(
    payload: EmployeeCreate, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    service = EmployeeService(session)
    employee = await service.create_from_template(
        user.id, role_id=payload.role_id, name=payload.name, overrides=payload.overrides
    )
    return ok(_view(service, employee))


@router.get("/{employee_id}", summary="One employee, with its contract and effective policy")
async def get_employee(
    employee_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    service = EmployeeService(session)
    employee = await service.get_employee(user.id, employee_id)
    return ok(_view(service, employee))


@router.patch("/{employee_id}", summary="Edit an employee")
async def update_employee(
    employee_id: uuid.UUID, payload: EmployeeUpdate, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    service = EmployeeService(session)
    changes = payload.model_dump(exclude_unset=True)
    employee = await service.update_employee(user.id, employee_id, changes)
    return ok(_view(service, employee))


@router.post("/{employee_id}/archive", summary="Retire an employee, keeping its history")
async def archive_employee(
    employee_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    service = EmployeeService(session)
    employee = await service.archive_employee(user.id, employee_id, archived=True)
    return ok(_view(service, employee))


@router.post("/{employee_id}/restore", summary="Bring a retired employee back")
async def restore_employee(
    employee_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    service = EmployeeService(session)
    employee = await service.archive_employee(user.id, employee_id, archived=False)
    return ok(_view(service, employee))


@router.delete("/{employee_id}", summary="Delete an employee that never ran anything")
async def delete_employee(
    employee_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    await EmployeeService(session).delete_employee(user.id, employee_id)
    return ok({"deleted": str(employee_id)})

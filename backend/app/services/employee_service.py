"""Employee service: create from a template, edit, retire, and resolve what they may do.

Three things this file is careful about.

**A name is a promise.** ``(user_id, name)`` is unique, so "让研究员看看这份材料" refers to
one employee. Renaming is allowed; colliding is not, and the error says which name is
taken rather than surfacing a constraint violation.

**A skill name is checked against the registry, not stored on trust.** If a role lists
``browser_navigate`` and no such tool exists, saving it would produce an employee whose
stated capability is a fiction — and the brief is explicit that a role's skills must
"引用真实工具或工作流，不得写成虚构技能". ``unknown_skills`` is computed on every read, so
a tool removed in a later version shows up as a problem instead of a silent gap.

**Retiring is not deleting.** ``archived`` keeps the row, because deliverables and run
records point at it. Deleting is offered for the case where the employee never ran
anything, and refused once it has — that check is in the service, not the window.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.employees import templates as role_templates
from app.ai.employees.persona_guard import describe_persona_limits, review_persona
from app.ai.employees.policy import describe_narrowing, narrow_policy
from app.ai.tools.policy_store import build_policy
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.employee import EMPLOYEE_STATUSES, Employee
from app.repositories.employee_repo import EmployeeRepository

logger = get_logger(__name__)

#: Fields the API may write. ``id``, ``user_id`` and ``created_from_template`` are not
#: here on purpose — an employee cannot be moved to another user, and the template flag is
#: provenance, not a setting.
EDITABLE_FIELDS: tuple[str, ...] = (
    "name",
    "role_id",
    "avatar",
    "mission",
    "persona",
    "responsibilities",
    "out_of_scope",
    "service_scope",
    "knowledge_scope",
    "memory_scope",
    "permission_profile",
    "skills",
    "model_policy",
    "status",
)


def known_tool_names() -> frozenset[str]:
    """Every tool this build actually has: business registry ∪ computer registry.

    Built from the real registries rather than from the template literals, so this answers
    "what exists" and the templates answer "what a role starts with". If they disagree,
    ``test_employee_templates.py`` fails.
    """
    from app.ai.agent.tools import build_default_registry
    from app.ai.employees.templates import ALL_TOOLS

    names = set(build_default_registry().names())
    names |= set(ALL_TOOLS)
    return frozenset(names)


class EmployeeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.employees = EmployeeRepository(session)

    # ------------------------------------------------------------------ read --
    async def list_employees(
        self,
        user_id: uuid.UUID,
        *,
        status: str | None = None,
        include_archived: bool = False,
        search: str | None = None,
    ) -> list[Employee]:
        stmt = self.employees.query(
            user_id,
            status=status,
            include_archived=include_archived,
            search=search,
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_employee(self, user_id: uuid.UUID, employee_id: uuid.UUID) -> Employee:
        employee = await self.employees.get_owned(employee_id, user_id)
        if employee is None:
            raise NotFoundError("员工不存在", details={"employee_id": str(employee_id)})
        return employee

    # ----------------------------------------------------------------- write --
    async def create_from_template(
        self,
        user_id: uuid.UUID,
        *,
        role_id: str,
        name: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> Employee:
        template = role_templates.get_template(role_id)
        if template is None:
            raise ValidationError(
                f"未知岗位模板 {role_id!r}",
                details={"available": sorted(role_templates.TEMPLATES_BY_ID)},
            )

        fields = template.to_employee_fields(name)
        for key, value in (overrides or {}).items():
            if key in EDITABLE_FIELDS:
                fields[key] = value

        final_name = str(fields.get("name") or template.name).strip()
        if not final_name:
            raise ValidationError("员工名不能为空")
        if await self.employees.name_taken(user_id, final_name):
            raise ConflictError(
                f"已经有一个叫「{final_name}」的员工",
                details={"name": final_name},
            )

        self._validate_skills(fields.get("skills"))

        employee = Employee(user_id=user_id, **{**fields, "name": final_name})
        self.employees.add(employee)
        await self.session.flush()
        logger.info("employee created", extra={"role": role_id, "name": final_name})
        return employee

    async def update_employee(
        self,
        user_id: uuid.UUID,
        employee_id: uuid.UUID,
        changes: dict[str, Any],
    ) -> Employee:
        employee = await self.get_employee(user_id, employee_id)

        if "name" in changes and changes["name"] is not None:
            new_name = str(changes["name"]).strip()
            if not new_name:
                raise ValidationError("员工名不能为空")
            if new_name != employee.name and await self.employees.name_taken(
                user_id, new_name, exclude_id=employee.id
            ):
                raise ConflictError(f"已经有一个叫「{new_name}」的员工", details={"name": new_name})
            changes["name"] = new_name

        if "status" in changes and changes["status"] is not None:
            status = str(changes["status"])
            if status not in EMPLOYEE_STATUSES:
                raise ValidationError(
                    f"未知状态 {status!r}", details={"allowed": list(EMPLOYEE_STATUSES)}
                )

        if "skills" in changes:
            self._validate_skills(changes["skills"])

        touched = False
        for key, value in changes.items():
            if key not in EDITABLE_FIELDS or value is None:
                continue
            setattr(employee, key, value)
            touched = True

        if touched:
            # The version is how a run record can say which employee it ran. Bumping it on
            # every edit — including a rename — keeps that honest at the cost of a number.
            employee.version = int(employee.version) + 1

        await self.session.flush()
        return employee

    async def archive_employee(
        self, user_id: uuid.UUID, employee_id: uuid.UUID, *, archived: bool = True
    ) -> Employee:
        """Retire or restore. Never deletes."""
        employee = await self.get_employee(user_id, employee_id)
        employee.archived = archived
        employee.status = "archived" if archived else "idle"
        employee.version = int(employee.version) + 1
        await self.session.flush()
        return employee

    async def delete_employee(self, user_id: uuid.UUID, employee_id: uuid.UUID) -> None:
        """Delete, but only an employee that never did anything.

        The check is here rather than in the window because the window is not a security
        control and, more practically, because the two cases need different answers:
        "I made this by mistake" should delete cleanly, and "I want this gone" on an
        employee with a run history should archive, so the history keeps its owner.
        """
        employee = await self.get_employee(user_id, employee_id)
        if employee.status != "idle" or employee.version > 1:
            raise ConflictError(
                "这个员工已经有工作记录，删除会让它的交付物和运行记录失去归属。请改为归档。",
                details={"status": employee.status, "version": employee.version},
            )
        await self.employees.delete(employee)
        await self.session.flush()

    # ------------------------------------------------------------- resolution --
    def review_persona_text(self, text: str) -> dict[str, object]:
        return review_persona(text).to_dict()

    def effective_policy(self, employee: Employee) -> tuple[Any, dict[str, Any]]:
        """The policy a run under this employee would use, and what it changed.

        Composed from the employee's *stored* fields, so the answer is reproducible from
        the row rather than from whatever the request happened to send.
        """
        base = build_policy(employee.user_id)
        profile = {
            **(employee.permission_profile or {}),
            "skills": list(employee.skills or []),
        }
        narrowed = narrow_policy(base, profile)
        return narrowed, describe_narrowing(base, narrowed)

    def unknown_skills(self, employee: Employee) -> list[str]:
        """Skills the employee claims that no registry backs. Shown, not silently dropped."""
        known = known_tool_names()
        return sorted({str(s) for s in (employee.skills or [])} - set(known))

    # ------------------------------------------------------------------ hall --
    async def hall_summary(self, user_id: uuid.UUID) -> dict[str, Any]:
        """The home screen's numbers. Every one is a real query."""
        employees = await self.list_employees(user_id)
        counts = await self.employees.status_counts(user_id)
        return {
            "total": len(employees),
            "by_status": counts,
            "working": counts.get("working", 0),
            "waiting_approval": counts.get("waiting_approval", 0),
            "waiting_user": counts.get("waiting_user", 0),
            "idle": counts.get("idle", 0),
            "failed": counts.get("failed", 0),
        }

    # --------------------------------------------------------------- helpers --
    def _validate_skills(self, skills: Any) -> None:
        if skills is None:
            return
        if not isinstance(skills, list):
            raise ValidationError("skills 必须是字符串列表")
        known = known_tool_names()
        unknown = sorted({str(s) for s in skills} - set(known))
        if unknown:
            raise ValidationError(
                "这些技能在本版本里不存在，保存它们会让员工声称一个做不到的能力",
                details={"unknown": unknown},
            )

    @staticmethod
    def template_catalogue() -> dict[str, Any]:
        """The role catalogue. A static method because it reads no database.

        The window loads this before anything else, and it is identical for every user —
        so requiring a session to ask for it would be a round-trip that cannot change the
        answer.
        """
        return {
            "templates": [t.to_public_dict() for t in role_templates.TEMPLATES],
            "default_role_id": role_templates.DEFAULT_ROLE_ID,
            "tool_categories": {
                name: list(tools) for name, tools in role_templates.TOOLS_BY_CATEGORY.items()
            },
            "business_tools": list(role_templates.BUSINESS_TOOLS),
            "persona_limits": describe_persona_limits(),
            "total_tools": len(known_tool_names()),
        }

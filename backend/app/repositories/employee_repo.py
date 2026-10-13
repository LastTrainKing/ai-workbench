"""Employee storage. The only place that issues SQL against ``employees``."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, select

from app.models.employee import Employee
from app.repositories.base import BaseRepository, apply_search


class EmployeeRepository(BaseRepository[Employee]):
    model = Employee

    def query(
        self,
        owner_id: uuid.UUID,
        *,
        status: str | None = None,
        include_archived: bool = False,
        search: str | None = None,
    ) -> Select[Any]:
        stmt = select(Employee).where(Employee.user_id == owner_id)
        if not include_archived:
            stmt = stmt.where(Employee.archived.is_(False))
        if status:
            stmt = stmt.where(Employee.status == status)
        stmt = apply_search(stmt, Employee.name, search)
        # Working first, then waiting, then idle, then the rest. Ordering by name alone
        # would put an archived employee above one that is waiting for the user.
        return stmt.order_by(Employee.archived.asc(), Employee.created_at.asc())

    async def get_owned(
        self, employee_id: uuid.UUID | None, owner_id: uuid.UUID
    ) -> Employee | None:
        """Fetch by id **and** owner. Every read path goes through this.

        There is deliberately no `get(id)` used by the API: an employee id is a UUID and
        guessing one is impractical, but "impractical to guess" is not an authorisation
        model, and the brief requires object-level checks on every read.
        """
        return await self.get_or_none(Employee.id == employee_id, Employee.user_id == owner_id)

    async def name_taken(
        self, owner_id: uuid.UUID, name: str, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        conditions = [Employee.user_id == owner_id, Employee.name == name]
        if exclude_id is not None:
            conditions.append(Employee.id != exclude_id)
        return await self.count(*conditions) > 0

    async def status_counts(self, owner_id: uuid.UUID) -> dict[str, int]:
        """How many employees are in each state. Drives the hall's header."""
        stmt = (
            select(Employee.status, func.count())
            .where(Employee.user_id == owner_id, Employee.archived.is_(False))
            .group_by(Employee.status)
        )
        rows = (await self.session.execute(stmt)).all()
        return {str(status): int(count) for status, count in rows}

    async def count_active(self, owner_id: uuid.UUID) -> int:
        return await self.count(Employee.user_id == owner_id, Employee.archived.is_(False))

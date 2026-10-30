"""Generic async repository helpers.

Repositories are the only place that issues SQL.  Services orchestrate
repositories, the API layer only talks to services, and the agent only talks to
tools → services.  See docs/architecture.md for the layer diagram.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ read --
    async def get(self, entity_id: uuid.UUID | None) -> ModelT | None:
        if entity_id is None:
            return None
        return await self.session.get(self.model, entity_id)

    async def get_or_none(self, *conditions: Any) -> ModelT | None:
        stmt = select(self.model)
        if conditions:
            stmt = stmt.where(*conditions)
        return (await self.session.execute(stmt.limit(1))).scalar_one_or_none()

    async def list(
        self,
        *conditions: Any,
        order_by: Any = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ModelT]:
        stmt = select(self.model)
        if conditions:
            stmt = stmt.where(*conditions)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def count(self, *conditions: Any) -> int:
        stmt = select(func.count()).select_from(self.model)
        if conditions:
            stmt = stmt.where(*conditions)
        return int((await self.session.execute(stmt)).scalar_one())

    # ------------------------------------------------- cross-entity queries --
    # A repository sometimes needs to read a *child* entity (e.g. AIRunRepository
    # reading ToolCall rows). ``list``/``get_or_none`` always select ``self.model``,
    # so using them with another table's conditions silently selects the wrong
    # entity. These two helpers make the target entity explicit and are the only
    # sanctioned way to query a foreign model from a repository.
    async def list_of(
        self,
        model: type[Any],
        *conditions: Any,
        order_by: Any = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[Any]:
        stmt = select(model)
        if conditions:
            stmt = stmt.where(*conditions)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        if offset:
            stmt = stmt.offset(offset)
        if limit:
            stmt = stmt.limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_one_of(self, model: type[Any], *conditions: Any) -> Any | None:
        stmt = select(model)
        if conditions:
            stmt = stmt.where(*conditions)
        return (await self.session.execute(stmt.limit(1))).scalar_one_or_none()

    async def count_of(self, model: type[Any], *conditions: Any) -> int:
        stmt = select(func.count()).select_from(model)
        if conditions:
            stmt = stmt.where(*conditions)
        return int((await self.session.execute(stmt)).scalar_one())

    async def paginate(
        self,
        stmt: Select[Any],
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[Any], int]:
        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(stmt.order_by(None).subquery())
                )
            ).scalar_one()
        )
        rows = (
            (await self.session.execute(stmt.limit(page_size).offset((page - 1) * page_size)))
            .scalars()
            .all()
        )
        return list(rows), total

    # ----------------------------------------------------------------- write --
    def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        return entity

    async def add_and_flush(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def delete(self, entity: ModelT) -> None:
        await self.session.delete(entity)

    async def delete_by(self, *conditions: Any) -> int:
        result = await self.session.execute(delete(self.model).where(*conditions))
        return int(result.rowcount or 0)

    async def flush(self) -> None:
        await self.session.flush()

    async def commit(self) -> None:
        await self.session.commit()

    async def refresh(self, entity: ModelT) -> ModelT:
        await self.session.refresh(entity)
        return entity


def apply_search(stmt: Select[Any], column: Any, query: str | None) -> Select[Any]:
    """Case-insensitive partial match helper (safe for arbitrary user input).

    ``%`` and ``_`` are LIKE wildcards, so user input must be escaped before it
    is embedded in a pattern. The escaping is performed in separate statements
    because a backslash inside an f-string *expression* is a SyntaxError before
    Python 3.12 (this project supports 3.11+).
    """
    if not query or not query.strip():
        return stmt
    escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = "%" + escaped + "%"
    return stmt.where(column.ilike(pattern, escape="\\"))


def order_desc(column: Any) -> Any:
    return column.desc()


__all__ = ["BaseRepository", "ModelT", "Sequence", "apply_search", "order_desc"]

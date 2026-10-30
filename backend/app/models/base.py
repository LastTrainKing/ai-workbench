"""Declarative base, shared mixins and common column types."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar

from sqlalchemy import DateTime, MetaData, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit naming convention keeps Alembic autogenerate deterministic.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    # SQLAlchemy manages this as a real class attribute; it is not a mutable
    # dataclass-style default, hence the explicit ClassVar annotation.
    metadata: ClassVar[MetaData] = MetaData(naming_convention=NAMING_CONVENTION)

    # Server-generated columns (``created_at`` / ``updated_at`` with
    # ``server_default`` / ``onupdate``) are normally *expired* after a flush and
    # reloaded lazily on next access.  In an async engine that lazy reload raises
    # ``MissingGreenlet`` if the attribute is touched from sync code — for
    # example while Pydantic serialises the object in the API layer.
    #
    # ``eager_defaults`` makes SQLAlchemy fetch those values in the same
    # INSERT/UPDATE via ``RETURNING`` (native on PostgreSQL), so a freshly
    # flushed row is always fully populated and safe to serialise.
    __mapper_args__: ClassVar[dict[str, bool]] = {"eager_defaults": True}

    def to_dict(self, exclude: set[str] | None = None) -> dict:
        exclude = exclude or set()
        out: dict = {}
        for column in self.__table__.columns:
            if column.name in exclude:
                continue
            out[column.name] = getattr(self, column.name)
        return out

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


def utcnow() -> datetime:
    return datetime.now(UTC)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

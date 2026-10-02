"""Project model — the top-level container that closes the
Knowledge → Task → Execution loop."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import ProjectStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.knowledge import Document, KnowledgeBase
    from app.models.note import Note
    from app.models.task import Task
    from app.models.user import User


class Project(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_owner_status", "owner_id", "status"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    icon: Mapped[str] = mapped_column(String(64), nullable=False, default="folder")
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#6366f1")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ProjectStatus.ACTIVE.value
    )

    owner: Mapped[User] = relationship(back_populates="projects")
    knowledge_bases: Mapped[list[KnowledgeBase]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[Task]] = relationship(back_populates="project")
    documents: Mapped[list[Document]] = relationship(back_populates="project")
    notes: Mapped[list[Note]] = relationship(back_populates="project")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project {self.name}>"

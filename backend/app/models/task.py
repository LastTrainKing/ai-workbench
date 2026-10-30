"""Task domain: task tree, dependencies, tags and knowledge links."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import TaskPriority, TaskSource, TaskStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.knowledge import Document
    from app.models.project import Project


class Task(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_owner_status", "owner_id", "status"),
        Index("ix_tasks_project_status", "project_id", "status"),
        Index("ix_tasks_due_date", "due_date"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Self-referencing tree: goal → phase → task → subtask
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    completion_criteria: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TaskStatus.TODO.value, index=True
    )
    priority: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TaskPriority.MEDIUM.value
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=TaskSource.MANUAL.value)
    ai_suggestion: Mapped[str] = mapped_column(Text, nullable=False, default="")

    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actual_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    project: Mapped[Project | None] = relationship(back_populates="tasks")
    children: Mapped[list[Task]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped[Task | None] = relationship(back_populates="children", remote_side="Task.id")

    dependencies: Mapped[list[TaskDependency]] = relationship(
        foreign_keys="TaskDependency.task_id",
        back_populates="task",
        cascade="all, delete-orphan",
    )
    dependents: Mapped[list[TaskDependency]] = relationship(
        foreign_keys="TaskDependency.depends_on_task_id",
        back_populates="depends_on_task",
        cascade="all, delete-orphan",
    )
    knowledge_links: Mapped[list[TaskDocument]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (
        UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependencies_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )

    task: Mapped[Task] = relationship(foreign_keys=[task_id], back_populates="dependencies")
    depends_on_task: Mapped[Task] = relationship(
        foreign_keys=[depends_on_task_id], back_populates="dependents"
    )


class TaskTag(Base):
    __tablename__ = "task_tags"

    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class TaskDocument(Base):
    """Knowledge ↔ Task edge: which document supports which task."""

    __tablename__ = "task_documents"

    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    relevance: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    task: Mapped[Task] = relationship(back_populates="knowledge_links")
    document: Mapped[Document] = relationship()

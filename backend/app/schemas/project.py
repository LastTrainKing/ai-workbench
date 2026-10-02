"""Project schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import ProjectStatus


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    icon: str = Field(default="folder", max_length=64)
    color: str = Field(default="#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    icon: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    status: ProjectStatus | None = None


class ProjectStats(BaseModel):
    knowledge_bases: int = 0
    documents: int = 0
    chunks: int = 0
    tasks_total: int = 0
    tasks_done: int = 0
    tasks_open: int = 0
    notes: int = 0
    ai_runs: int = 0


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    icon: str
    color: str
    status: str
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectOut):
    stats: ProjectStats = ProjectStats()

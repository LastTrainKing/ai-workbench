"""Knowledge base / document / chunk / tag schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import DocumentStatus, EmbeddingStatus


# --------------------------------------------------------------------- tags --
class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    color: str


# ----------------------------------------------------------- knowledge base --
class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    icon: str = Field(default="book", max_length=64)
    color: str = Field(default="#8b5cf6", pattern=r"^#[0-9a-fA-F]{6}$")
    project_id: uuid.UUID | None = None


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    icon: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    project_id: uuid.UUID | None = None
    is_archived: bool | None = None


class KnowledgeBaseStats(BaseModel):
    document_count: int = 0
    chunk_count: int = 0
    embedded_chunk_count: int = 0
    total_chars: int = 0
    total_size_bytes: int = 0
    failed_documents: int = 0
    processing_documents: int = 0
    last_updated: datetime | None = None


class KnowledgeBaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    icon: str
    color: str
    project_id: uuid.UUID | None
    embedding_model: str
    vector_size: int
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseDetail(KnowledgeBaseOut):
    stats: KnowledgeBaseStats = KnowledgeBaseStats()


# ----------------------------------------------------------------- document --
class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    project_id: uuid.UUID | None
    title: str
    original_filename: str
    mime_type: str
    file_ext: str
    size_bytes: int
    status: str
    progress: int
    error_message: str
    char_count: int
    chunk_count: int
    embedded_chunk_count: int
    page_count: int
    language: str
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None


class DocumentDetail(DocumentOut):
    summary: str = ""
    doc_metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[TagOut] = Field(default_factory=list)
    job_status: str | None = None
    job_stage: str | None = None
    job_error: str | None = None


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    project_id: uuid.UUID | None = None
    tag_names: list[str] | None = None


class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    content: str
    heading: str
    char_count: int
    token_estimate: int
    embedding_status: str
    embedding_model: str
    vector_id: str


class DocumentContentOut(BaseModel):
    document_id: uuid.UUID
    title: str
    content: str
    char_count: int
    truncated: bool


class DocumentAnalysisRequest(BaseModel):
    """Which analysis workflows to run against a document."""

    actions: list[str] = Field(
        default_factory=lambda: ["summary"],
        description=(
            "One or more of: summary, concepts, todos, terms, study_plan, "
            "interview, note, cards"
        ),
    )
    knowledge_base_id: uuid.UUID | None = None
    save_note: bool = True
    project_id: uuid.UUID | None = None

    @property
    def normalized_actions(self) -> list[str]:
        allowed = {
            "summary",
            "concepts",
            "todos",
            "terms",
            "study_plan",
            "interview",
            "note",
            "cards",
        }
        seen: list[str] = []
        for a in self.actions:
            key = a.strip().lower()
            if key in allowed and key not in seen:
                seen.append(key)
        return seen or ["summary"]


class DocumentUploadAccepted(BaseModel):
    document: DocumentOut
    job_id: uuid.UUID | None = None
    duplicate: bool = False
    message: str = ""


class EmbeddingStatusOut(BaseModel):
    document_id: uuid.UUID
    status: str
    chunks_total: int
    chunks_embedded: int
    chunks_failed: int
    embedding_model: str


__all__ = [
    "ChunkOut",
    "DocumentAnalysisRequest",
    "DocumentContentOut",
    "DocumentDetail",
    "DocumentOut",
    "DocumentStatus",
    "DocumentUpdate",
    "DocumentUploadAccepted",
    "EmbeddingStatus",
    "EmbeddingStatusOut",
    "KnowledgeBaseCreate",
    "KnowledgeBaseDetail",
    "KnowledgeBaseOut",
    "KnowledgeBaseStats",
    "KnowledgeBaseUpdate",
    "TagOut",
]

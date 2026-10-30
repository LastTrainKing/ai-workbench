"""Knowledge domain: knowledge bases, documents, chunks, tags and concepts.

Relationship chain (see docs/database.md for the ER diagram)::

    KnowledgeBase 1─n Document 1─n DocumentChunk 1─1 Qdrant point
         │                │
         │                └─n DocumentConcept n─1 Concept
         └─n DocumentTag n─1 Tag
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import ConceptSource, DocumentStatus, EmbeddingStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.project import Project


class KnowledgeBase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_knowledge_bases_owner_id_name"),
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
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    icon: Mapped[str] = mapped_column(String(64), nullable=False, default="book")
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#8b5cf6")
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    vector_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    project: Mapped[Project | None] = relationship(back_populates="knowledge_bases")
    documents: Mapped[list[Document]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )


class Document(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        # Same file re-uploaded into the same KB is detected by content hash.
        UniqueConstraint("knowledge_base_id", "content_hash", name="uq_documents_kb_content_hash"),
        Index("ix_documents_kb_status", "knowledge_base_id", "status"),
        Index("ix_documents_owner_created", "owner_id", "created_at"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    stored_path: Mapped[str] = mapped_column(String(600), nullable=False, default="")
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    file_ext: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DocumentStatus.PENDING.value, index=True
    )
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedded_chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    project: Mapped[Project | None] = relationship(back_populates="documents")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    tags: Mapped[list[Tag]] = relationship(
        secondary="document_tags", back_populates="documents", lazy="selectin"
    )


class DocumentChunk(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_index"),
        Index("ix_document_chunks_kb", "knowledge_base_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    heading: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 1:1 with the Qdrant point id; kept here so we can delete points precisely.
    vector_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    embedding_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=EmbeddingStatus.PENDING.value, index=True
    )
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    embedding_error: Mapped[str] = mapped_column(Text, nullable=False, default="")

    document: Mapped[Document] = relationship(back_populates="chunks")
    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="chunks")


class Tag(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_tags_owner_id_name"),)

    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str] = mapped_column(String(16), nullable=False, default="#64748b")

    documents: Mapped[list[Document]] = relationship(
        secondary="document_tags", back_populates="tags"
    )


class DocumentTag(Base):
    __tablename__ = "document_tags"

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class Concept(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A named idea extracted from documents (feeds the knowledge graph)."""

    __tablename__ = "concepts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "normalized_name", name="uq_concepts_owner_id_normalized_name"
        ),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=ConceptSource.AI.value)
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DocumentConcept(Base):
    __tablename__ = "document_concepts"

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    concept_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), primary_key=True
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

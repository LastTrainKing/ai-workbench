"""Repositories for the knowledge domain."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, or_, select

from app.domain.enums import DocumentStatus
from app.models.knowledge import (
    Concept,
    Document,
    DocumentChunk,
    DocumentConcept,
    DocumentTag,
    KnowledgeBase,
    Tag,
)
from app.repositories.base import BaseRepository, apply_search


class KnowledgeBaseRepository(BaseRepository[KnowledgeBase]):
    model = KnowledgeBase

    def query(
        self,
        owner_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None = None,
        search: str | None = None,
        include_archived: bool = False,
    ) -> Select[Any]:
        stmt = select(KnowledgeBase).where(KnowledgeBase.owner_id == owner_id)
        if project_id:
            stmt = stmt.where(KnowledgeBase.project_id == project_id)
        if not include_archived:
            stmt = stmt.where(KnowledgeBase.is_archived.is_(False))
        stmt = apply_search(stmt, KnowledgeBase.name, search)
        return stmt.order_by(KnowledgeBase.updated_at.desc())

    async def get_owned(
        self, kb_id: uuid.UUID | None, owner_id: uuid.UUID
    ) -> KnowledgeBase | None:
        return await self.get_or_none(
            KnowledgeBase.id == kb_id, KnowledgeBase.owner_id == owner_id
        )

    async def name_taken(
        self, owner_id: uuid.UUID, name: str, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        stmt = select(KnowledgeBase.id).where(
            KnowledgeBase.owner_id == owner_id, KnowledgeBase.name == name
        )
        if exclude_id:
            stmt = stmt.where(KnowledgeBase.id != exclude_id)
        return (await self.session.execute(stmt.limit(1))).scalar_one_or_none() is not None

    async def stats(self, kb_id: uuid.UUID) -> dict[str, Any]:
        row = (
            await self.session.execute(
                select(
                    func.count(Document.id).label("documents"),
                    func.coalesce(func.sum(Document.chunk_count), 0).label("chunks"),
                    func.coalesce(func.sum(Document.embedded_chunk_count), 0).label("embedded"),
                    func.coalesce(func.sum(Document.char_count), 0).label("chars"),
                    func.coalesce(func.sum(Document.size_bytes), 0).label("bytes"),
                    func.max(Document.updated_at).label("last_updated"),
                ).where(Document.knowledge_base_id == kb_id)
            )
        ).one()
        # NOTE: these conditions are on ``Document`` while ``self.model`` is
        # ``KnowledgeBase``, so the explicit ``*_of`` helpers must be used —
        # otherwise the generated SQL would select from the wrong table.
        failed = await self.count_of(
            Document,
            Document.knowledge_base_id == kb_id,
            Document.status == DocumentStatus.FAILED.value,
        )
        processing = await self.count_of(
            Document,
            Document.knowledge_base_id == kb_id,
            Document.status.in_(
                [DocumentStatus.PENDING.value, DocumentStatus.PROCESSING.value]
            ),
        )
        return {
            "document_count": int(row.documents or 0),
            "chunk_count": int(row.chunks or 0),
            "embedded_chunk_count": int(row.embedded or 0),
            "total_chars": int(row.chars or 0),
            "total_size_bytes": int(row.bytes or 0),
            "failed_documents": failed,
            "processing_documents": processing,
            "last_updated": row.last_updated,
        }


class DocumentRepository(BaseRepository[Document]):
    model = Document

    def query(
        self,
        owner_id: uuid.UUID,
        *,
        knowledge_base_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> Select[Any]:
        stmt = select(Document).where(Document.owner_id == owner_id)
        if knowledge_base_id:
            stmt = stmt.where(Document.knowledge_base_id == knowledge_base_id)
        if project_id:
            stmt = stmt.where(Document.project_id == project_id)
        if status:
            stmt = stmt.where(Document.status == status)
        if search:
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Document.title.ilike(pattern),
                    Document.original_filename.ilike(pattern),
                )
            )
        return stmt.order_by(Document.created_at.desc())

    async def get_owned(
        self, document_id: uuid.UUID | None, owner_id: uuid.UUID
    ) -> Document | None:
        return await self.get_or_none(Document.id == document_id, Document.owner_id == owner_id)

    async def find_duplicate(
        self, knowledge_base_id: uuid.UUID, content_hash: str
    ) -> Document | None:
        return await self.get_or_none(
            Document.knowledge_base_id == knowledge_base_id,
            Document.content_hash == content_hash,
        )

    async def recent(self, owner_id: uuid.UUID, limit: int = 5) -> list[Document]:
        return await self.list(
            Document.owner_id == owner_id,
            order_by=Document.created_at.desc(),
            limit=limit,
        )

    async def count_by_status(self, owner_id: uuid.UUID) -> dict[str, int]:
        rows = (
            await self.session.execute(
                select(Document.status, func.count(Document.id))
                .where(Document.owner_id == owner_id)
                .group_by(Document.status)
            )
        ).all()
        return {str(status): int(count) for status, count in rows}


class DocumentChunkRepository(BaseRepository[DocumentChunk]):
    model = DocumentChunk

    async def for_document(self, document_id: uuid.UUID) -> list[DocumentChunk]:
        return await self.list(
            DocumentChunk.document_id == document_id,
            order_by=DocumentChunk.chunk_index,
        )

    async def page_for_document(
        self, document_id: uuid.UUID, *, page: int, page_size: int
    ) -> tuple[list[DocumentChunk], int]:
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
        )
        return await self.paginate(stmt, page=page, page_size=page_size)

    async def pending_embedding(
        self, knowledge_base_id: uuid.UUID | None = None, limit: int = 200
    ) -> list[DocumentChunk]:
        stmt = select(DocumentChunk).where(
            DocumentChunk.embedding_status.in_(["PENDING", "FAILED"])
        )
        if knowledge_base_id:
            stmt = stmt.where(DocumentChunk.knowledge_base_id == knowledge_base_id)
        return list(
            (await self.session.execute(stmt.order_by(DocumentChunk.chunk_index).limit(limit)))
            .scalars()
            .all()
        )

    async def embedding_progress(self, document_id: uuid.UUID) -> dict[str, int]:
        rows = (
            await self.session.execute(
                select(DocumentChunk.embedding_status, func.count(DocumentChunk.id))
                .where(DocumentChunk.document_id == document_id)
                .group_by(DocumentChunk.embedding_status)
            )
        ).all()
        counts = {str(status): int(count) for status, count in rows}
        return {
            "total": sum(counts.values()),
            "embedded": counts.get("EMBEDDED", 0),
            "pending": counts.get("PENDING", 0),
            "failed": counts.get("FAILED", 0),
            "skipped": counts.get("SKIPPED", 0),
        }


class TagRepository(BaseRepository[Tag]):
    model = Tag

    async def get_or_create(
        self, owner_id: uuid.UUID, name: str, color: str = "#64748b"
    ) -> Tag:
        clean = name.strip()[:64]
        existing = await self.get_or_none(Tag.owner_id == owner_id, Tag.name == clean)
        if existing:
            return existing
        tag = Tag(owner_id=owner_id, name=clean, color=color)
        self.session.add(tag)
        await self.session.flush()
        return tag

    async def for_owner(self, owner_id: uuid.UUID) -> list[Tag]:
        return await self.list(Tag.owner_id == owner_id, order_by=Tag.name)

    async def attach_to_document(self, document_id: uuid.UUID, tag_id: uuid.UUID) -> None:
        exists = await self.session.get(DocumentTag, {"document_id": document_id, "tag_id": tag_id})
        if exists is None:
            self.session.add(DocumentTag(document_id=document_id, tag_id=tag_id))


class ConceptRepository(BaseRepository[Concept]):
    model = Concept

    async def get_or_create(self, owner_id: uuid.UUID, name: str) -> Concept:
        normalized = name.strip().lower()[:200]
        existing = await self.get_or_none(
            Concept.owner_id == owner_id, Concept.normalized_name == normalized
        )
        if existing:
            return existing
        concept = Concept(
            owner_id=owner_id, name=name.strip()[:200], normalized_name=normalized
        )
        self.session.add(concept)
        await self.session.flush()
        return concept

    async def link_document(
        self, document_id: uuid.UUID, concept_id: uuid.UUID, weight: float = 1.0
    ) -> None:
        exists = await self.session.get(
            DocumentConcept, {"document_id": document_id, "concept_id": concept_id}
        )
        if exists is None:
            self.session.add(
                DocumentConcept(document_id=document_id, concept_id=concept_id, weight=weight)
            )

    async def top_for_owner(self, owner_id: uuid.UUID, limit: int = 20) -> list[Concept]:
        return await self.list(
            Concept.owner_id == owner_id,
            order_by=Concept.mention_count.desc(),
            limit=limit,
        )

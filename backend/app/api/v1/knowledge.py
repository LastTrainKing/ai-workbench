"""Knowledge base, document, chunk and search endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, UploadFile, status

from app.api.deps import CurrentUser, DbSession, Pagination
from app.core.errors import ValidationError
from app.core.response import build_page, ok
from app.infrastructure.files import save_upload
from app.repositories.knowledge_repo import (
    DocumentChunkRepository,
    DocumentRepository,
    KnowledgeBaseRepository,
)
from app.schemas.knowledge import (
    ChunkOut,
    DocumentAnalysisRequest,
    DocumentContentOut,
    DocumentDetail,
    DocumentOut,
    DocumentUpdate,
    DocumentUploadAccepted,
    EmbeddingStatusOut,
    KnowledgeBaseCreate,
    KnowledgeBaseDetail,
    KnowledgeBaseOut,
    KnowledgeBaseStats,
    KnowledgeBaseUpdate,
    TagOut,
)
from app.services.ai_services import SearchService
from app.services.document_service import DocumentService, normalize_actions
from app.services.workspace_service import KnowledgeService

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge"])


@router.get("", summary="List knowledge bases")
async def list_bases(
    user: CurrentUser,
    session: DbSession,
    pagination: Pagination,
    project_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=120),
    include_archived: bool = False,
) -> dict:
    repo = KnowledgeBaseRepository(session)
    stmt = repo.query(
        user.id, project_id=project_id, search=q, include_archived=include_archived
    )
    rows, total = await repo.paginate(
        stmt, page=pagination.page, page_size=pagination.page_size
    )
    return ok(
        build_page(
            [KnowledgeBaseOut.model_validate(row).model_dump(mode="json") for row in rows],
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
        ).model_dump(mode="json")
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a knowledge base")
async def create_base(payload: KnowledgeBaseCreate, user: CurrentUser, session: DbSession) -> dict:
    kb = await KnowledgeService(session).create(
        owner_id=user.id,
        name=payload.name,
        description=payload.description,
        icon=payload.icon,
        color=payload.color,
        project_id=payload.project_id,
    )
    return ok(KnowledgeBaseOut.model_validate(kb).model_dump(mode="json"), message="知识库已创建")


@router.get("/tags", summary="List all tags used by the current user")
async def list_tags(user: CurrentUser, session: DbSession) -> dict:
    tags = await KnowledgeService(session).list_tags(user.id)
    return ok([TagOut.model_validate(tag).model_dump(mode="json") for tag in tags])


@router.get("/{kb_id}", summary="Knowledge base detail with statistics")
async def get_base(kb_id: uuid.UUID, user: CurrentUser, session: DbSession) -> dict:
    service = KnowledgeService(session)
    kb = await service.get(owner_id=user.id, kb_id=kb_id)
    stats = await service.stats(owner_id=user.id, kb_id=kb_id)
    detail = KnowledgeBaseDetail(
        **KnowledgeBaseOut.model_validate(kb).model_dump(),
        stats=KnowledgeBaseStats(**stats),
    )
    return ok(detail.model_dump(mode="json"))


@router.patch("/{kb_id}", summary="Update a knowledge base")
async def update_base(
    kb_id: uuid.UUID, payload: KnowledgeBaseUpdate, user: CurrentUser, session: DbSession
) -> dict:
    kb = await KnowledgeService(session).update(
        owner_id=user.id, kb_id=kb_id, **payload.model_dump(exclude_unset=True)
    )
    return ok(KnowledgeBaseOut.model_validate(kb).model_dump(mode="json"))


@router.delete("/{kb_id}", summary="Delete a knowledge base and its vectors")
async def delete_base(kb_id: uuid.UUID, user: CurrentUser, session: DbSession) -> dict:
    await KnowledgeService(session).delete(owner_id=user.id, kb_id=kb_id)
    return ok({"deleted": True}, message="知识库已删除")


# ===========================================================================
# Documents
# ===========================================================================
documents_router = APIRouter(prefix="/documents", tags=["documents"])


@documents_router.post(
    "/upload",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a document (async ingestion)",
    response_model=None,
)
async def upload_document(
    user: CurrentUser,
    session: DbSession,
    file: Annotated[UploadFile, File(description="Markdown, TXT or PDF")],
    knowledge_base_id: Annotated[uuid.UUID, Form()],
    title: Annotated[str | None, Form()] = None,
    project_id: Annotated[uuid.UUID | None, Form()] = None,
) -> dict:
    stored = await save_upload(file, file.filename or "untitled", content_type=file.content_type)
    try:
        document, job, duplicate = await DocumentService(session).register_upload(
            owner_id=user.id,
            knowledge_base_id=knowledge_base_id,
            stored=stored,
            title=title,
            project_id=project_id,
        )
    except Exception:
        # The row was never created: do not leave an orphan file on disk.
        from app.infrastructure.files import delete_file

        delete_file(stored.path)
        raise

    payload = DocumentUploadAccepted(
        document=DocumentOut.model_validate(document),
        job_id=job.id if job else None,
        duplicate=duplicate,
        message=(
            "该文件已存在于本知识库，已跳过重复上传"
            if duplicate
            else "文件已接收，正在后台解析与向量化"
        ),
    )
    return ok(payload.model_dump(mode="json"), message=payload.message)


@documents_router.get("", summary="List documents")
async def list_documents(
    user: CurrentUser,
    session: DbSession,
    pagination: Pagination,
    knowledge_base_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    status_filter: str | None = Query(None, alias="status"),
    q: str | None = Query(None, max_length=200),
) -> dict:
    repo = DocumentRepository(session)
    stmt = repo.query(
        user.id,
        knowledge_base_id=knowledge_base_id,
        project_id=project_id,
        status=status_filter,
        search=q,
    )
    rows, total = await repo.paginate(
        stmt, page=pagination.page, page_size=pagination.page_size
    )
    return ok(
        build_page(
            [DocumentOut.model_validate(row).model_dump(mode="json") for row in rows],
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
        ).model_dump(mode="json")
    )


@documents_router.get("/{document_id}", summary="Document detail incl. ingestion progress")
async def get_document(document_id: uuid.UUID, user: CurrentUser, session: DbSession) -> dict:
    detail = await DocumentService(session).detail(owner_id=user.id, document_id=document_id)
    document = detail["document"]
    payload = DocumentDetail(
        **DocumentOut.model_validate(document).model_dump(),
        summary=document.summary,
        doc_metadata=document.doc_metadata or {},
        tags=[TagOut.model_validate(tag) for tag in detail["tags"]],
        job_status=detail["job_status"],
        job_stage=detail["job_stage"],
        job_error=detail["job_error"],
    )
    body = payload.model_dump(mode="json")
    body["embedding_progress"] = detail["embedding_progress"]
    return ok(body)


@documents_router.get("/{document_id}/content", summary="Extracted plain text")
async def get_document_content(
    document_id: uuid.UUID, user: CurrentUser, session: DbSession, limit: int = Query(40_000, ge=500, le=200_000)
) -> dict:
    content = await DocumentService(session).content(
        owner_id=user.id, document_id=document_id, limit=limit
    )
    return ok(DocumentContentOut(**content).model_dump(mode="json"))


@documents_router.get("/{document_id}/chunks", summary="Paginated chunk listing")
async def get_document_chunks(
    document_id: uuid.UUID, user: CurrentUser, session: DbSession, pagination: Pagination
) -> dict:
    await DocumentService(session).get(owner_id=user.id, document_id=document_id)
    repo = DocumentChunkRepository(session)
    rows, total = await repo.page_for_document(
        document_id, page=pagination.page, page_size=pagination.page_size
    )
    return ok(
        build_page(
            [ChunkOut.model_validate(row).model_dump(mode="json") for row in rows],
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
        ).model_dump(mode="json")
    )


@documents_router.get(
    "/{document_id}/embedding-status", summary="Per-document embedding counters"
)
async def embedding_status(document_id: uuid.UUID, user: CurrentUser, session: DbSession) -> dict:
    document = await DocumentService(session).get(owner_id=user.id, document_id=document_id)
    chunk_repo = DocumentChunkRepository(session)
    counts = await chunk_repo.embedding_progress(document_id)
    sample = await chunk_repo.list(
        chunk_repo.model.document_id == document_id,
        order_by=chunk_repo.model.chunk_index,
        limit=1,
    )
    return ok(
        EmbeddingStatusOut(
            document_id=document.id,
            status=document.status,
            chunks_total=counts["total"],
            chunks_embedded=counts["embedded"],
            chunks_failed=counts["failed"],
            embedding_model=sample[0].embedding_model if sample else "",
        ).model_dump(mode="json")
    )


@documents_router.patch("/{document_id}", summary="Update title, project or tags")
async def update_document(
    document_id: uuid.UUID, payload: DocumentUpdate, user: CurrentUser, session: DbSession
) -> dict:
    document = await DocumentService(session).update_metadata(
        owner_id=user.id,
        document_id=document_id,
        **payload.model_dump(exclude_unset=True),
    )
    return ok(DocumentOut.model_validate(document).model_dump(mode="json"))


@documents_router.post("/{document_id}/reprocess", summary="Re-run the ingestion pipeline")
async def reprocess_document(document_id: uuid.UUID, user: CurrentUser, session: DbSession) -> dict:
    job = await DocumentService(session).reprocess(owner_id=user.id, document_id=document_id)
    return ok({"job_id": str(job.id)}, message="已重新排队处理")


@documents_router.post(
    "/{document_id}/analyze",
    summary="Run AI analysis workflows (summary / concepts / todos / cards / interview …)",
)
async def analyze_document(
    document_id: uuid.UUID, payload: DocumentAnalysisRequest, user: CurrentUser, session: DbSession
) -> dict:
    actions = normalize_actions(payload.actions)
    result = await DocumentService(session).analyze(
        owner_id=user.id,
        document_id=document_id,
        actions=actions,
        save_note=payload.save_note,
        project_id=payload.project_id,
    )
    return ok(result, message=f"已完成 {len(actions)} 项分析")


@documents_router.delete("/{document_id}", summary="Delete a document, its chunks and vectors")
async def delete_document(document_id: uuid.UUID, user: CurrentUser, session: DbSession) -> dict:
    await DocumentService(session).delete(owner_id=user.id, document_id=document_id)
    return ok({"deleted": True}, message="文档已删除")


# ===========================================================================
# Search
# ===========================================================================
search_router = APIRouter(prefix="/search", tags=["search"])


@search_router.get("", summary="Keyword / semantic / hybrid search")
async def search(
    user: CurrentUser,
    session: DbSession,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    mode: Annotated[str, Query(pattern="^(keyword|semantic|hybrid)$")] = "hybrid",
    knowledge_base_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    limit: int = Query(20, ge=1, le=60),
) -> dict:
    if not q.strip():
        raise ValidationError("Search query must not be empty")
    result = await SearchService(session).search(
        owner_id=user.id,
        query=q,
        mode=mode,
        knowledge_base_ids=[knowledge_base_id] if knowledge_base_id else None,
        project_id=project_id,
        limit=limit,
    )
    return ok(result)

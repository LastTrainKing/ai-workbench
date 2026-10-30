"""Integration tests: ingestion pipeline, hybrid search and RAG."""

from __future__ import annotations

import io
import uuid

import httpx
import pytest

from app.domain.enums import DocumentStatus
from app.infrastructure.database import session_scope
from app.services.document_service import DocumentService

pytestmark = pytest.mark.integration


async def _upload(client: httpx.AsyncClient, headers: dict, kb_id: str, name: str, content: bytes):
    return await client.post(
        "/documents/upload",
        headers=headers,
        files={"file": (name, io.BytesIO(content), "application/octet-stream")},
        data={"knowledge_base_id": kb_id},
    )


async def _ingest(document_id: str) -> dict:
    """Run the ingestion pipeline inline (the worker is disabled in tests)."""
    async with session_scope() as session:
        return await DocumentService(session).ingest(uuid.UUID(document_id))


# --------------------------------------------------------------------------- #
# ingestion
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_markdown_ingestion_end_to_end(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    response = await _upload(
        client, auth_headers, knowledge_base["id"], "notes.md", sample_markdown
    )
    assert response.status_code == 202
    payload = response.json()["data"]
    assert payload["duplicate"] is False
    document_id = payload["document"]["id"]

    result = await _ingest(document_id)
    assert result["chunks"] > 0
    assert result["embedded"] == result["chunks"]
    assert result["failed"] == 0
    assert result["dimension"] == 384

    detail = (await client.get(f"/documents/{document_id}", headers=auth_headers)).json()["data"]
    assert detail["status"] == DocumentStatus.COMPLETED.value
    assert detail["progress"] == 100
    assert detail["chunk_count"] == result["chunks"]
    assert detail["embedded_chunk_count"] == result["chunks"]
    assert detail["embedding_progress"]["failed"] == 0


@pytest.mark.asyncio
async def test_pdf_ingestion(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict
) -> None:
    from pathlib import Path

    sample = Path(__file__).resolve().parents[3] / "samples" / "distributed-systems.pdf"
    if not sample.exists():
        pytest.skip("sample PDF fixture not generated")

    response = await _upload(
        client, auth_headers, knowledge_base["id"], "distributed.pdf", sample.read_bytes()
    )
    assert response.status_code == 202
    document_id = response.json()["data"]["document"]["id"]

    result = await _ingest(document_id)
    assert result["chunks"] >= 2
    detail = (await client.get(f"/documents/{document_id}", headers=auth_headers)).json()["data"]
    assert detail["status"] == DocumentStatus.COMPLETED.value
    assert detail["page_count"] == 3


@pytest.mark.asyncio
async def test_duplicate_upload_is_skipped(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    first = await _upload(client, auth_headers, knowledge_base["id"], "a.md", sample_markdown)
    second = await _upload(client, auth_headers, knowledge_base["id"], "b.md", sample_markdown)

    assert first.json()["data"]["duplicate"] is False
    assert second.json()["data"]["duplicate"] is True
    assert first.json()["data"]["document"]["id"] == second.json()["data"]["document"]["id"]

    listing = (await client.get("/documents", headers=auth_headers)).json()["data"]
    assert listing["meta"]["total"] == 1


@pytest.mark.asyncio
async def test_reingest_is_idempotent(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    response = await _upload(
        client, auth_headers, knowledge_base["id"], "notes.md", sample_markdown
    )
    document_id = response.json()["data"]["document"]["id"]

    first = await _ingest(document_id)
    second = await _ingest(document_id)

    assert first["chunks"] == second["chunks"], "re-ingesting must not duplicate chunks"
    detail = (await client.get(f"/documents/{document_id}", headers=auth_headers)).json()["data"]
    assert detail["chunk_count"] == first["chunks"]

    chunks = (await client.get(f"/documents/{document_id}/chunks", headers=auth_headers)).json()[
        "data"
    ]
    assert chunks["meta"]["total"] == first["chunks"]


@pytest.mark.asyncio
async def test_chunk_listing_and_content(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    response = await _upload(
        client, auth_headers, knowledge_base["id"], "notes.md", sample_markdown
    )
    document_id = response.json()["data"]["document"]["id"]
    await _ingest(document_id)

    chunks = (await client.get(f"/documents/{document_id}/chunks", headers=auth_headers)).json()[
        "data"
    ]
    assert chunks["items"]
    assert chunks["items"][0]["chunk_index"] == 0
    assert chunks["items"][0]["embedding_status"] == "EMBEDDED"
    assert chunks["items"][0]["vector_id"]

    content = (await client.get(f"/documents/{document_id}/content", headers=auth_headers)).json()[
        "data"
    ]
    assert "垃圾回收" in content["content"]
    assert content["char_count"] > 0


@pytest.mark.asyncio
async def test_delete_document_removes_chunks_and_vectors(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    from app.infrastructure.vector_store import get_vector_store

    response = await _upload(
        client, auth_headers, knowledge_base["id"], "notes.md", sample_markdown
    )
    document_id = response.json()["data"]["document"]["id"]
    await _ingest(document_id)

    before = await get_vector_store().count(knowledge_base["id"])
    assert before > 0

    assert (
        await client.delete(f"/documents/{document_id}", headers=auth_headers)
    ).status_code == 200
    after = await get_vector_store().count(knowledge_base["id"])
    assert after < before, "deleting a document must delete its vectors"


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_all_search_modes_return_results(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    response = await _upload(
        client, auth_headers, knowledge_base["id"], "notes.md", sample_markdown
    )
    await _ingest(response.json()["data"]["document"]["id"])

    for mode in ("keyword", "semantic", "hybrid"):
        result = (
            await client.get(
                "/search",
                headers=auth_headers,
                params={
                    "q": "垃圾回收 收集器",
                    "mode": mode,
                    "knowledge_base_id": knowledge_base["id"],
                },
            )
        ).json()["data"]
        assert result["results"], f"{mode} search returned nothing"
        assert result["mode"] == mode
        assert result["took_ms"] >= 0


@pytest.mark.asyncio
async def test_hybrid_results_expose_both_score_legs(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    response = await _upload(
        client, auth_headers, knowledge_base["id"], "notes.md", sample_markdown
    )
    await _ingest(response.json()["data"]["document"]["id"])

    result = (
        await client.get(
            "/search",
            headers=auth_headers,
            params={
                "q": "HashMap 红黑树",
                "mode": "hybrid",
                "knowledge_base_id": knowledge_base["id"],
            },
        )
    ).json()["data"]

    assert result["results"]
    top = result["results"][0]
    assert "vector_score" in top
    assert "keyword_score" in top
    assert result["dense_hits"] > 0


@pytest.mark.asyncio
async def test_search_is_cached_and_namespace_invalidated(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    from app.core.cache import bump_namespace

    response = await _upload(
        client, auth_headers, knowledge_base["id"], "notes.md", sample_markdown
    )
    await _ingest(response.json()["data"]["document"]["id"])
    params = {"q": "线程池", "mode": "hybrid", "knowledge_base_id": knowledge_base["id"]}

    first = (await client.get("/search", headers=auth_headers, params=params)).json()["data"]
    assert first["cached"] is False
    second = (await client.get("/search", headers=auth_headers, params=params)).json()["data"]
    assert second["cached"] is True

    await bump_namespace("search")
    third = (await client.get("/search", headers=auth_headers, params=params)).json()["data"]
    assert third["cached"] is False, "bumping the namespace must invalidate the cache"


@pytest.mark.asyncio
async def test_search_cannot_read_another_users_knowledge_base(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict
) -> None:
    other = await client.post(
        "/auth/register",
        json={
            "email": f"other_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
            "username": f"other_{uuid.uuid4().hex[:8]}",
            "password": "test1234",
            "display_name": "Other",
        },
    )
    other_headers = {"Authorization": f"Bearer {other.json()['data']['access_token']}"}

    response = await client.get(
        "/search",
        headers=other_headers,
        params={"q": "任何内容", "knowledge_base_id": knowledge_base["id"]},
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# RAG
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_rag_returns_citations_from_real_chunks(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    response = await _upload(
        client, auth_headers, knowledge_base["id"], "notes.md", sample_markdown
    )
    await _ingest(response.json()["data"]["document"]["id"])

    rag = (
        await client.post(
            "/chat/rag",
            headers=auth_headers,
            json={
                "question": "G1 收集器有什么特点？",
                "knowledge_base_ids": [knowledge_base["id"]],
                "mode": "hybrid",
            },
        )
    ).json()["data"]

    assert rag["answer"]
    assert rag["citations"], "a grounded answer must cite its sources"
    for citation in rag["citations"]:
        assert citation["chunk_id"]
        assert citation["document_title"] == "notes.md"
        assert citation["chunk_index"] >= 0
        assert citation["snippet"]
    assert rag["retrieval"]["used_chunks"] == len(rag["citations"])
    assert rag["queries"]
    assert rag["run_id"].startswith("run_")


@pytest.mark.asyncio
async def test_rag_trace_records_prompt_version_and_sources(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    response = await _upload(
        client, auth_headers, knowledge_base["id"], "notes.md", sample_markdown
    )
    await _ingest(response.json()["data"]["document"]["id"])

    rag = (
        await client.post(
            "/chat/rag",
            headers=auth_headers,
            json={
                "question": "HashMap 的结构是什么？",
                "knowledge_base_ids": [knowledge_base["id"]],
            },
        )
    ).json()["data"]

    trace = (await client.get(f"/ai-runs/{rag['run_id']}", headers=auth_headers)).json()["data"]
    assert trace["run"]["run_type"] == "RAG_ANSWER"
    assert trace["run"]["status"] == "SUCCEEDED"
    assert trace["run"]["prompt_name"] == "rag_answer"
    assert trace["run"]["prompt_version"] >= 1
    assert trace["run"]["retrieval_count"] > 0
    assert trace["retrieval"]["mode"] == "hybrid"
    assert trace["sources"], "the trace must record which documents were used"
    assert trace["sources"][0]["document_title"] == "notes.md"


@pytest.mark.asyncio
async def test_rag_with_empty_knowledge_base_is_honest(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict
) -> None:
    rag = (
        await client.post(
            "/chat/rag",
            headers=auth_headers,
            json={"question": "任何问题", "knowledge_base_ids": [knowledge_base["id"]]},
        )
    ).json()["data"]

    assert rag["grounded"] is False
    assert rag["citations"] == []
    assert "没有检索到" in rag["answer"]


@pytest.mark.asyncio
async def test_rag_rejects_unknown_knowledge_base(
    client: httpx.AsyncClient, auth_headers: dict
) -> None:
    response = await client.post(
        "/chat/rag",
        headers=auth_headers,
        json={"question": "hi", "knowledge_base_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_document_analysis_workflows(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    response = await _upload(
        client, auth_headers, knowledge_base["id"], "notes.md", sample_markdown
    )
    document_id = response.json()["data"]["document"]["id"]
    await _ingest(document_id)

    result = (
        await client.post(
            f"/documents/{document_id}/analyze",
            headers=auth_headers,
            json={"actions": ["summary", "concepts", "todos", "terms", "cards"], "save_note": True},
        )
    ).json()["data"]

    assert result["summary"]
    assert isinstance(result["concepts"], list)
    assert isinstance(result["terms"], list)
    assert result["cards"]
    assert result["note_ids"], "save_note=true must persist a note"
    assert result["run_id"]

    notes = (await client.get("/notes", headers=auth_headers)).json()["data"]
    assert notes["meta"]["total"] >= 1
    assert notes["items"][0]["is_ai_generated"] is True
    assert notes["items"][0]["source_document_id"] == document_id


@pytest.mark.asyncio
async def test_analysis_requires_completed_document(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    response = await _upload(
        client, auth_headers, knowledge_base["id"], "notes.md", sample_markdown
    )
    document_id = response.json()["data"]["document"]["id"]  # still PENDING

    result = await client.post(
        f"/documents/{document_id}/analyze", headers=auth_headers, json={"actions": ["summary"]}
    )
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_document_parse_failure_marks_document_failed(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict
) -> None:
    response = await _upload(
        client, auth_headers, knowledge_base["id"], "broken.pdf", b"%PDF-1.4 broken content"
    )
    assert response.status_code == 202
    document_id = response.json()["data"]["document"]["id"]

    with pytest.raises(Exception):  # noqa: B017 - the pipeline must surface the failure
        await _ingest(document_id)

    detail = (await client.get(f"/documents/{document_id}", headers=auth_headers)).json()["data"]
    assert detail["status"] == DocumentStatus.FAILED.value
    assert "document_parse_error" in detail["error_message"]


@pytest.mark.asyncio
async def test_reprocess_requeues_a_failed_document(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict
) -> None:
    response = await _upload(
        client, auth_headers, knowledge_base["id"], "broken.pdf", b"%PDF-1.4 broken"
    )
    document_id = response.json()["data"]["document"]["id"]
    with pytest.raises(Exception):  # noqa: B017
        await _ingest(document_id)

    job = await client.post(f"/documents/{document_id}/reprocess", headers=auth_headers)
    assert job.status_code == 200
    assert job.json()["data"]["job_id"]

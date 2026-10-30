"""Vectors from two different models must not end up in one collection.

Retrieval compares distances. Two embedding models do not share a space, so a collection
holding vectors from both returns neighbours that mean nothing — and nothing in the system
reports a problem, because every row still claims a single `embedding_model`.

Three things are checked here:

1. **The offline fallback says so.** With no embedding key the factory returns the
   hash/n-gram provider and marks it `degraded`. That matters because the search page must
   not call character-overlap matching "semantic search", and the only honest source for
   that claim is the factory.
2. **A model change is refused.** A knowledge base whose chunks were embedded by model A
   refuses a write from model B, and the refusal names both models and the way out.
3. **A dimension change is refused.** Two models can share a dimension, but when they do not,
   the collection cannot be resized in place — `ensure_collection` raises rather than
   truncating or padding.

The guard is not "the setting is validated". It is that a write does not happen.
"""

from __future__ import annotations

import uuid

import pytest

from app.ai.providers.factory import build_embedding_provider
from app.core.errors import ValidationError
from app.infrastructure.database import session_scope
from app.models.knowledge import Document, DocumentChunk, KnowledgeBase
from app.models.user import User


# --------------------------------------------------------------------------- #
# 1. The fallback is honest about what it is
# --------------------------------------------------------------------------- #
def test_the_offline_fallback_is_reported_as_degraded() -> None:
    """No embedding key means hash/n-gram vectors, and the factory says so.

    A caller that ignored `degraded` would present n-gram matching as semantic search. The
    flag exists so that cannot happen by accident.
    """
    _provider, info = build_embedding_provider()
    if info.kind == "offline":
        assert info.degraded is True
        assert info.reason, "a degraded provider must say why"
        assert info.model and "heuristic" in info.model
    else:
        # A real provider is configured on this machine; then it must not claim to be degraded.
        assert not getattr(info, "degraded", False)
        assert info.api_key_set or info.kind == "ollama"


# --------------------------------------------------------------------------- #
# 2. Two models, one knowledge base
# --------------------------------------------------------------------------- #
class _StubEmbedder:
    """An embedder that claims to be a different model from the one on record."""

    model = "model-b"
    dimension = 1536

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def embed_texts(self, texts: list[str]) -> object:
        raise AssertionError("the guard must refuse before anything is embedded")


@pytest.fixture
async def knowledge_base_with_vectors(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[uuid.UUID, uuid.UUID]:
    """A knowledge base built by `model-a`, with one chunk already embedded.

    The document has a real file behind it: `ingest` parses before it embeds, so a row with
    no file would fail in the parser and never reach the guard this test is about.
    """
    source = tmp_path_factory.mktemp("embed-guard") / "已有向量化的文档.txt"
    source.write_text("这段内容是用 model-a 向量化的。", encoding="utf-8")

    async with session_scope() as session:
        name = f"emb_{uuid.uuid4().hex[:8]}"
        user = User(
            email=f"{name}@aiworkbench.test",
            username=name,
            display_name="Embedding Guard",
            hashed_password="x",
        )
        session.add(user)
        await session.flush()

        base = KnowledgeBase(
            owner_id=user.id,
            name="用 model-a 建的知识库",
            description="",
            embedding_model="model-a",
            vector_size=1536,
        )
        session.add(base)
        await session.flush()

        document = Document(
            owner_id=user.id,
            knowledge_base_id=base.id,
            title="已有向量化的文档",
            content_hash="1" * 64,
            status="COMPLETED",
            file_ext=".txt",
            mime_type="text/plain",
            stored_path=str(source),
            size_bytes=source.stat().st_size,
        )
        session.add(document)
        await session.flush()

        session.add(
            DocumentChunk(
                document_id=document.id,
                knowledge_base_id=base.id,
                chunk_index=0,
                content="这段内容是用 model-a 向量化的。",
                embedding_status="EMBEDDED",
                vector_id="v1",
            )
        )
        return user.id, document.id


async def test_a_second_model_cannot_write_into_the_same_base(
    knowledge_base_with_vectors: tuple[uuid.UUID, uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal, and that it happens before any embedding call is made."""
    _owner, document_id = knowledge_base_with_vectors

    import app.services.document_service as document_service

    monkeypatch.setattr(document_service, "EmbeddingService", _StubEmbedder)

    async with session_scope() as session:
        with pytest.raises(ValidationError) as raised:
            await document_service.DocumentService(session).ingest(document_id)

    message = str(raised.value)
    details = getattr(raised.value, "details", {}) or {}
    assert "model-a" in str(details) or "model-a" in message, details
    assert "model-b" in str(details) or "model-b" in message, details
    assert details.get("hint"), "a refusal without a way out is a wall"


# --------------------------------------------------------------------------- #
# 3. The collection cannot be resized
# --------------------------------------------------------------------------- #
async def test_a_dimension_change_is_refused_by_the_collection() -> None:
    """`ensure_collection` raises rather than resizing a collection that holds vectors.

    Run against the **existing** collection with a deliberately wrong dimension: the guard is
    about refusing, so the check needs no new collection and writes nothing. The first
    version created a throwaway collection first, and this machine's Qdrant answers
    `500 ... os error 5` to that — an environment problem that would have hidden the guard
    behind a skip.
    """
    from app.core.config import settings
    from app.infrastructure.vector_store import VectorStore, VectorStoreError

    store = VectorStore()
    try:
        info = await store.client().get_collection(store.collection)
    except Exception as exc:
        pytest.skip(f"Qdrant is not reachable: {type(exc).__name__}: {exc}")

    current = info.config.params.vectors.size  # type: ignore[union-attr]
    wrong = current + 64
    try:
        with pytest.raises(VectorStoreError) as raised:
            await store.ensure_collection(wrong)
        details = raised.value.details or {}
        assert details.get("collection_dim") == current, details
        assert details.get("embedding_dim") == wrong, details
    finally:
        await store.close()


async def test_the_configured_dimension_matches_the_collection() -> None:
    """The other half: the collection really is at the size the configuration claims.

    Without this, a `QDRANT_VECTOR_SIZE` that disagreed with the live collection would be
    discovered by a user whose first upload failed.
    """
    from app.core.config import settings
    from app.infrastructure.vector_store import VectorStore

    store = VectorStore()
    try:
        info = await store.client().get_collection(store.collection)
    except Exception as exc:
        pytest.skip(f"Qdrant is not reachable: {type(exc).__name__}: {exc}")
    finally:
        await store.close()

    live = info.config.params.vectors.size  # type: ignore[union-attr]
    configured = int(getattr(settings, "qdrant_vector_size", 0) or 0)
    if configured:
        assert configured == live, (
            f"QDRANT_VECTOR_SIZE={configured} but the collection is {live}. "
            "The next write would be refused."
        )
    else:
        assert live > 0

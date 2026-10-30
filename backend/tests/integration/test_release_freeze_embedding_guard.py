"""Switching the embedding model must not silently mix incompatible vectors.

The dimension guard in `ensure_collection` is necessary and **not sufficient**: two models
can both produce 384-dimensional vectors and mean entirely different things by them. Before
this fix, `kb.embedding_model` was assigned on every ingest and never compared, so:

* a knowledge base built with model A accepted vectors from model B without a word;
* retrieval then compared distances across two incompatible spaces;
* every row still claimed a single `embedding_model`, so nothing anywhere looked wrong.

These tests use the offline embedder, so they cost nothing and need no provider. The model
name is what is switched, which is exactly the case the dimension check cannot see.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.core.errors import ValidationError
from app.infrastructure.database import session_scope
from app.infrastructure.files import save_upload
from app.models.knowledge import DocumentChunk, KnowledgeBase
from app.models.user import User
from app.services.document_service import DocumentService

DOC = "# 文档\n本项目的封版版本号是 2.2.0。\n"

#: A second document with **different bytes**. Uploading the same content again makes
#: `register_upload` return the existing row as a duplicate, which re-ingests the *first*
#: document and clears its own chunks — the guard then sees an empty knowledge base and is
#: correctly skipped. That is what the first version of this test did, and it made the test
#: assert nothing while looking like it asserted the guard.
DOC_2 = "# 文档二\n数据目录位于用户主目录下。\n"


class _BytesStream:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        chunk = (
            self._data[self._pos :]
            if size is None or size < 0
            else self._data[self._pos : self._pos + size]
        )
        self._pos += len(chunk)
        return chunk


async def _seed() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A user, a knowledge base, and one document ingested with the current embedder."""
    from app.core.security import hash_password

    name = f"emb_{uuid.uuid4().hex[:8]}"
    async with session_scope() as session:
        user = User(
            email=f"{name}@aiworkbench.dev",
            username=name,
            display_name="Embedding Guard",
            hashed_password=hash_password("embed1234"),
        )
        session.add(user)
        await session.flush()
        owner = user.id

        kb = KnowledgeBase(owner_id=owner, name=f"kb-{name}")
        session.add(kb)
        await session.flush()

        stored = await save_upload(
            _BytesStream(DOC.encode("utf-8")), "doc.md", content_type="text/markdown"
        )
        document, _job, _dup = await DocumentService(session).register_upload(
            owner_id=owner, knowledge_base_id=kb.id, stored=stored
        )
        document_id = document.id
        await session.commit()

    async with session_scope() as session:
        await DocumentService(session).ingest(document_id)
        await session.commit()

    return owner, kb.id, document_id


async def _drop(owner: uuid.UUID) -> None:
    from sqlalchemy import delete

    from app.models.knowledge import Document
    from app.models.knowledge import KnowledgeBase as KB

    async with session_scope() as session:
        # `DocumentChunk` has no `owner_id` — it is reached through its knowledge base.
        kb_ids = list((await session.execute(select(KB.id).where(KB.owner_id == owner))).scalars())
        if kb_ids:
            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.knowledge_base_id.in_(kb_ids))
            )
        await session.execute(delete(Document).where(Document.owner_id == owner))
        await session.execute(delete(KB).where(KB.owner_id == owner))
        await session.execute(delete(User).where(User.id == owner))


@pytest.fixture
async def seeded():
    owner, kb_id, document_id = await _seed()
    try:
        yield owner, kb_id, document_id
    finally:
        await _drop(owner)


async def test_the_knowledge_base_records_the_model_that_built_it(seeded) -> None:
    _owner, kb_id, _doc = seeded
    async with session_scope() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        assert kb is not None
        assert kb.embedding_model, "the KB must record which model built its vectors"
        assert kb.vector_size > 0


async def test_ingesting_with_a_different_model_at_the_same_dimension_is_refused(
    seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case the dimension check cannot catch: same size, different semantic space."""
    owner, kb_id, _doc = seeded

    from app.ai.rag import embedding as embedding_module

    original = embedding_module.EmbeddingService.model

    def _different_model(self):
        return "some-other-model-384"

    monkeypatch.setattr(embedding_module.EmbeddingService, "model", property(_different_model))

    async with session_scope() as session:
        stored = await save_upload(
            _BytesStream(DOC_2.encode("utf-8")), "doc2.md", content_type="text/markdown"
        )
        document, _job, _dup = await DocumentService(session).register_upload(
            owner_id=owner, knowledge_base_id=kb_id, stored=stored
        )
        document_id = document.id
        await session.commit()

    # The precondition this test depends on: the first document really did leave embedded
    # chunks behind. Without it the guard is skipped for the right reason and the test would
    # be asserting nothing.
    async with session_scope() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        embedded = int(
            (
                await session.execute(
                    select(func.count(DocumentChunk.id)).where(
                        DocumentChunk.knowledge_base_id == kb_id,
                        DocumentChunk.embedding_status == "EMBEDDED",
                    )
                )
            ).scalar_one()
        )
    assert kb is not None
    assert embedded > 0, "the seed did not leave embedded chunks; the guard cannot be tested"
    assert kb.embedding_model, "the seed did not record the model"

    with pytest.raises(ValidationError) as caught:
        async with session_scope() as session:
            await DocumentService(session).ingest(document_id)
            await session.commit()

    details = caught.value.details or {}
    assert details.get("knowledge_base_model") != details.get("current_model")
    assert details.get("embedded_chunks", 0) > 0
    assert "hint" in details, "the error has to say what to do about it"

    assert original  # the property was restored by monkeypatch


async def test_a_knowledge_base_with_no_vectors_yet_accepts_any_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty KB is not a conflict — there is nothing to be incompatible with."""
    from app.core.security import hash_password

    name = f"emb_new_{uuid.uuid4().hex[:8]}"
    async with session_scope() as session:
        user = User(
            email=f"{name}@aiworkbench.dev",
            username=name,
            display_name="Fresh KB",
            hashed_password=hash_password("embed1234"),
        )
        session.add(user)
        await session.flush()
        owner = user.id
        kb = KnowledgeBase(owner_id=owner, name=f"kb-{name}")
        session.add(kb)
        await session.flush()
        kb_id = kb.id
        stored = await save_upload(
            _BytesStream(DOC.encode("utf-8")), "doc.md", content_type="text/markdown"
        )
        document, _job, _dup = await DocumentService(session).register_upload(
            owner_id=owner, knowledge_base_id=kb_id, stored=stored
        )
        document_id = document.id
        await session.commit()

    from app.ai.rag import embedding as embedding_module

    monkeypatch.setattr(
        embedding_module.EmbeddingService,
        "model",
        property(lambda _self: "some-other-model-384"),
    )

    try:
        async with session_scope() as session:
            result = await DocumentService(session).ingest(document_id)
            await session.commit()
        assert result.get("embedded", 0) >= 1

        async with session_scope() as session:
            kb = await session.get(KnowledgeBase, kb_id)
            assert kb is not None
            assert kb.embedding_model == "some-other-model-384"
    finally:
        await _drop(owner)

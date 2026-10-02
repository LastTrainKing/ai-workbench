"""Retrieval: dense (Qdrant) + sparse (PostgreSQL) + hybrid fusion + rerank.

Hybrid design
-------------
* **Dense leg** — cosine similarity over Qdrant vectors, filtered by knowledge
  base so a search can never leak across tenants.
* **Sparse leg** — PostgreSQL ``ILIKE`` candidate generation followed by an
  in-process BM25-style scorer.  We deliberately do not rely on
  ``to_tsvector`` because PostgreSQL ships no Chinese tokeniser by default and
  this project must work out of the box on a stock install.
* **Fusion** — min-max normalised weighted sum:
  ``score = alpha * dense_norm + (1 - alpha) * sparse_norm`` (``HYBRID_ALPHA``).
  Weights are configurable, and the per-leg scores are kept on every result so
  the UI can explain *why* a chunk was retrieved.
* **Rerank** — a lexical reranker re-scores the fused candidates using features
  a bag-of-words index misses (query-term coverage, heading match, phrase hits,
  length prior).  It is honest about being lexical: no cross-encoder model is
  shipped, because that would require a large model download.
"""

from __future__ import annotations

import math
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.heuristic import tokenize
from app.ai.rag.embedding import EmbeddingService
from app.ai.types import RetrievedChunk
from app.core.config import settings
from app.core.errors import VectorStoreError
from app.core.logging import get_logger
from app.domain.enums import DocumentStatus, EmbeddingStatus
from app.infrastructure.vector_store import get_vector_store
from app.models.knowledge import Document, DocumentChunk

logger = get_logger(__name__)

_TOKEN_FOR_PATTERN = re.compile(r"[^\w\u4e00-\u9fff]+")


@dataclass(slots=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    mode: str
    dense_hits: int
    sparse_hits: int
    took_ms: int
    reranked: bool
    queries: list[str]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Retriever:
    def __init__(self, embedding_service: EmbeddingService | None = None) -> None:
        self.embeddings = embedding_service or EmbeddingService()
        self.store = get_vector_store()

    # ------------------------------------------------------------------ query --
    async def retrieve(
        self,
        session: AsyncSession,
        *,
        query: str,
        knowledge_base_ids: list[uuid.UUID] | None = None,
        document_ids: list[uuid.UUID] | None = None,
        mode: str = "hybrid",
        top_k: int | None = None,
        candidate_k: int | None = None,
        rerank: bool | None = None,
    ) -> RetrievalResult:
        started = time.perf_counter()
        top_k = top_k or settings.retrieval_top_k
        candidate_k = candidate_k or settings.retrieval_candidate_k
        do_rerank = settings.rerank_enabled if rerank is None else rerank
        query = (query or "").strip()

        if not query:
            return RetrievalResult([], mode, 0, 0, 0, False, [])

        dense_hits: dict[str, float] = {}
        sparse_hits: dict[str, float] = {}
        dense_count = sparse_count = 0

        if mode in ("semantic", "hybrid"):
            try:
                vector = await self.embeddings.embed_query(query)
                if vector:
                    await self.store.ensure_collection(len(vector))
                    hits = await self.store.search(
                        vector,
                        limit=candidate_k,
                        knowledge_base_ids=knowledge_base_ids,
                        document_ids=document_ids,
                    )
                    dense_count = len(hits)
                    for hit in hits:
                        chunk_id = str(hit.payload.get("chunk_id") or hit.id)
                        dense_hits[chunk_id] = float(hit.score)
            except VectorStoreError as exc:
                # A vector-store outage must not kill keyword search: degrade and log.
                logger.warning(
                    "dense_retrieval_unavailable",
                    extra={"error": str(exc), "fallback": "keyword-only"},
                )
                if mode == "semantic":
                    raise

        if mode in ("keyword", "hybrid"):
            sparse_hits = await self._sparse_search(
                session,
                query=query,
                knowledge_base_ids=knowledge_base_ids,
                document_ids=document_ids,
                limit=candidate_k,
            )
            sparse_count = len(sparse_hits)

        if mode == "semantic":
            fused = dense_hits
        elif mode == "keyword":
            fused = sparse_hits
        else:
            fused = self._fuse(dense_hits, sparse_hits, settings.hybrid_alpha)

        if not fused:
            return RetrievalResult(
                [],
                mode,
                dense_count,
                sparse_count,
                int((time.perf_counter() - started) * 1000),
                False,
                [query],
            )

        ranked = sorted(fused.items(), key=lambda item: -item[1])[: max(top_k * 3, top_k)]
        chunks = await self._hydrate(
            session,
            ranked=ranked,
            dense_hits=dense_hits,
            sparse_hits=sparse_hits,
            mode=mode,
        )

        reranked = False
        if do_rerank and chunks:
            chunks = self._rerank(query, chunks)
            reranked = True

        chunks = chunks[:top_k]
        took_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "retrieval_completed",
            extra={
                "mode": mode,
                "dense": dense_count,
                "sparse": sparse_count,
                "returned": len(chunks),
                "reranked": reranked,
                "took_ms": took_ms,
            },
        )
        return RetrievalResult(
            chunks, mode, dense_count, sparse_count, took_ms, reranked, [query]
        )

    # --------------------------------------------------------------- sparse leg --
    async def _sparse_search(
        self,
        session: AsyncSession,
        *,
        query: str,
        knowledge_base_ids: list[uuid.UUID] | None,
        document_ids: list[uuid.UUID] | None,
        limit: int,
    ) -> dict[str, float]:
        terms = self._salient_terms(query)
        if not terms:
            return {}

        conditions = [
            DocumentChunk.content.ilike(f"%{_escape_like(term)}%", escape="\\")
            for term in terms[:8]
        ]
        stmt = (
            select(DocumentChunk.id, DocumentChunk.content, DocumentChunk.document_id)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                Document.status == DocumentStatus.COMPLETED.value,
                DocumentChunk.embedding_status != EmbeddingStatus.SKIPPED.value,
                or_(*conditions),
            )
            .limit(limit * 4)
        )
        if knowledge_base_ids:
            stmt = stmt.where(DocumentChunk.knowledge_base_id.in_(knowledge_base_ids))
        if document_ids:
            stmt = stmt.where(DocumentChunk.document_id.in_(document_ids))

        rows = (await session.execute(stmt)).all()
        if not rows:
            return {}

        # BM25-style scoring over the candidate window.
        docs_tokens = [tokenize(row.content) for row in rows]
        total_docs = len(rows)
        avg_len = sum(len(t) for t in docs_tokens) / total_docs or 1.0
        df = Counter()
        for tokens in docs_tokens:
            df.update(set(tokens))
        query_terms = set(terms)
        k1, b = 1.5, 0.75

        scores: dict[str, float] = {}
        for row, tokens in zip(rows, docs_tokens, strict=True):
            tf = Counter(tokens)
            length = len(tokens) or 1
            score = 0.0
            for term in query_terms:
                freq = tf.get(term, 0)
                if not freq:
                    continue
                idf = math.log(1 + (total_docs - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
                denom = freq + k1 * (1 - b + b * length / avg_len)
                score += idf * (freq * (k1 + 1)) / (denom or 1)
            if score > 0:
                scores[str(row.id)] = score
        return scores

    @staticmethod
    def _salient_terms(query: str, limit: int = 8) -> list[str]:
        raw = [t for t in tokenize(query) if len(t) > 1]
        # Longest-first: more specific terms produce far better SQL candidates.
        return sorted(set(raw), key=len, reverse=True)[:limit]

    # ------------------------------------------------------------------ fusion --
    @staticmethod
    def _fuse(
        dense: dict[str, float], sparse: dict[str, float], alpha: float
    ) -> dict[str, float]:
        if not dense:
            return dict(sparse)
        if not sparse:
            return dict(dense)

        def _norm(values: dict[str, float]) -> dict[str, float]:
            if not values:
                return {}
            lo, hi = min(values.values()), max(values.values())
            if hi - lo < 1e-9:
                return dict.fromkeys(values, 1.0)
            return {k: (v - lo) / (hi - lo) for k, v in values.items()}

        dn, sn = _norm(dense), _norm(sparse)
        fused: dict[str, float] = {}
        for key in set(dn) | set(sn):
            fused[key] = alpha * dn.get(key, 0.0) + (1 - alpha) * sn.get(key, 0.0)
        return fused

    # ---------------------------------------------------------------- hydrate --
    async def _hydrate(
        self,
        session: AsyncSession,
        *,
        ranked: list[tuple[str, float]],
        dense_hits: dict[str, float],
        sparse_hits: dict[str, float],
        mode: str,
    ) -> list[RetrievedChunk]:
        try:
            ids = [uuid.UUID(chunk_id) for chunk_id, _ in ranked]
        except ValueError:
            ids = []
        if not ids:
            return []

        rows = (
            await session.execute(
                select(DocumentChunk, Document)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(DocumentChunk.id.in_(ids))
            )
        ).all()
        by_id = {str(chunk.id): (chunk, document) for chunk, document in rows}

        out: list[RetrievedChunk] = []
        for chunk_id, score in ranked:
            pair = by_id.get(chunk_id)
            if pair is None:
                # Vector payload without a matching DB row: stale point. Skip it
                # rather than surfacing content we cannot cite.
                logger.warning("stale_vector_point", extra={"chunk_id": chunk_id})
                continue
            chunk, document = pair
            out.append(
                RetrievedChunk(
                    chunk_id=str(chunk.id),
                    document_id=str(document.id),
                    document_title=document.title,
                    knowledge_base_id=str(chunk.knowledge_base_id),
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    heading=chunk.heading,
                    score=float(score),
                    vector_score=float(dense_hits.get(chunk_id, 0.0)),
                    keyword_score=float(sparse_hits.get(chunk_id, 0.0)),
                    source=mode,
                )
            )
        return out

    # ---------------------------------------------------------------- rerank ---
    @staticmethod
    def _rerank(query: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Lexical reranker.

        Features (all computed against the *original* query):
          * query-term coverage in the chunk body (recall of the question);
          * exact phrase hit (bigram match) — a strong relevance signal;
          * heading match — topical proximity;
          * length prior — very short or very long chunks are less useful.
        The fused retrieval score is blended in so we never discard a strong
        semantic match that shares no literal terms with the query.
        """
        query_terms = {t for t in tokenize(query) if len(t) > 1}
        if not query_terms:
            return chunks
        lowered_query = query.lower()
        bigrams = {
            lowered_query[i : i + 2]
            for i in range(len(lowered_query) - 1)
            if not lowered_query[i].isspace()
        }

        for chunk in chunks:
            body = chunk.content.lower()
            body_terms = set(tokenize(chunk.content))
            coverage = len(query_terms & body_terms) / len(query_terms)
            phrase = 1.0 if any(bg in body for bg in bigrams) else 0.0
            heading_terms = set(tokenize(chunk.heading))
            heading_match = (
                len(query_terms & heading_terms) / len(query_terms) if heading_terms else 0.0
            )
            length = len(chunk.content)
            length_prior = 1.0 if 200 <= length <= 1600 else 0.6
            lexical = (
                0.45 * coverage
                + 0.25 * phrase
                + 0.2 * heading_match
                + 0.1 * length_prior
            )
            # Keep the retrieval signal dominant but let lexical features reorder
            # near-ties (this is what makes the top result feel "on topic").
            chunk.score = round(0.65 * chunk.score + 0.35 * lexical, 6)

        return sorted(chunks, key=lambda c: -c.score)

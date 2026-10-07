"""Qdrant vector store adapter.

The collection is created lazily with the **actual** dimension reported by the
configured embedding provider, so switching between a 1536-dim cloud model and a
768-dim local model does not require any code change.

Payload schema (one point per ``document_chunks`` row)::

    {
      "chunk_id": "<uuid>",
      "document_id": "<uuid>",
      "knowledge_base_id": "<uuid>",
      "owner_id": "<uuid>",
      "chunk_index": 12,
      "heading": "JVM > 垃圾回收",
      "document_title": "Java 学习笔记",
      "content": "…first 1200 chars…",
      "embedding_model": "text-embedding-3-small"
    }
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core.config import settings
from app.core.errors import VectorStoreError
from app.core.logging import get_logger

logger = get_logger(__name__)

PAYLOAD_INDEXES = ("knowledge_base_id", "document_id", "owner_id")


@dataclass(slots=True)
class VectorPoint:
    id: str
    vector: list[float]
    payload: dict[str, Any]


@dataclass(slots=True)
class VectorHit:
    id: str
    score: float
    payload: dict[str, Any]


class VectorStore:
    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        collection: str | None = None,
    ) -> None:
        self.url = url or settings.qdrant_url
        self.collection = collection or settings.qdrant_collection
        self._client: AsyncQdrantClient | None = None
        self._api_key = api_key if api_key is not None else settings.qdrant_api_key
        self._known_dim = 0
        self._ensured = False

    # ------------------------------------------------------------- lifecycle --
    def client(self) -> AsyncQdrantClient:
        if self._client is None:
            self._client = AsyncQdrantClient(
                url=self.url,
                api_key=self._api_key or None,
                timeout=20.0,
                prefer_grpc=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._ensured = False

    async def ping(self) -> tuple[bool, str]:
        try:
            info = await self.client().get_collections()
            names = [c.name for c in info.collections]
            return True, f"collections={names}"
        except (UnexpectedResponse, OSError, ValueError) as exc:
            logger.warning("qdrant_ping_failed", extra={"error": str(exc)})
            return False, f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------- collection --
    async def ensure_collection(self, dimension: int) -> int:
        """Create the collection for ``dimension`` if needed; return the dim."""
        if dimension <= 0:
            raise VectorStoreError("Cannot create a collection with dimension <= 0")
        if self._ensured and self._known_dim == dimension:
            return dimension

        client = self.client()
        try:
            exists = await client.collection_exists(self.collection)
        except (UnexpectedResponse, OSError) as exc:
            raise VectorStoreError(
                f"Qdrant is not reachable at {self.url}. Is it running?",
                details={"error": str(exc)},
            ) from exc

        if exists:
            info = await client.get_collection(self.collection)
            current = info.config.params.vectors.size  # type: ignore[union-attr]
            if current != dimension:
                raise VectorStoreError(
                    "Embedding dimension changed but the Qdrant collection already exists. "
                    "Stop the stack, remove .runtime/qdrant/storage, start it again so the "
                    "collection is recreated at the new size, then re-queue the vectors with "
                    "`python -m app.cli reindex --knowledge-base <id>`.",
                    details={"collection_dim": current, "embedding_dim": dimension},
                )
        else:
            try:
                await client.create_collection(
                    collection_name=self.collection,
                    vectors_config=models.VectorParams(
                        size=dimension,
                        distance=models.Distance[settings.qdrant_distance.upper()],
                        on_disk=True,
                    ),
                    hnsw_config=models.HnswConfigDiff(m=16, ef_construct=128),
                    optimizers_config=models.OptimizersConfigDiff(default_segment_number=2),
                )
            except UnexpectedResponse as exc:
                # Qdrant can report "collection does not exist" while a stale
                # directory is still on disk (for example after a hard kill or a
                # partially applied delete). In that window CREATE fails with
                # "Collection data already exists". Re-check instead of failing:
                # if the collection is actually usable we simply continue.
                still_missing = not await client.collection_exists(self.collection)
                if still_missing:
                    raise VectorStoreError(
                        "Failed to create the Qdrant collection. A stale collection "
                        "directory is likely present: stop Qdrant, delete "
                        "`.runtime/qdrant/storage/collections/<collection>`, restart, then retry.",
                        details={"collection": self.collection, "error": str(exc)[:400]},
                    ) from exc
                logger.warning(
                    "qdrant_collection_create_raced",
                    extra={"collection": self.collection, "detail": str(exc)[:200]},
                )
                info = await client.get_collection(self.collection)
                current = info.config.params.vectors.size  # type: ignore[union-attr]
                if current != dimension:
                    raise VectorStoreError(
                        "Existing collection dimension does not match the embedding provider",
                        details={"collection_dim": current, "embedding_dim": dimension},
                    ) from exc
            else:
                for field in PAYLOAD_INDEXES:
                    try:
                        await client.create_payload_index(
                            collection_name=self.collection,
                            field_name=field,
                            field_schema=models.PayloadSchemaType.KEYWORD,
                        )
                    except UnexpectedResponse as exc:
                        logger.warning(
                            "qdrant_payload_index_failed",
                            extra={"field": field, "error": str(exc)},
                        )
                logger.info(
                    "qdrant_collection_created",
                    extra={"collection": self.collection, "dim": dimension},
                )

        self._known_dim = dimension
        self._ensured = True
        return dimension

    # ------------------------------------------------------------------ write --
    async def upsert(self, points: Sequence[VectorPoint], *, wait: bool = True) -> int:
        if not points:
            return 0
        if not self._ensured:
            await self.ensure_collection(len(points[0].vector))
        try:
            await self.client().upsert(
                collection_name=self.collection,
                points=[
                    models.PointStruct(id=p.id, vector=p.vector, payload=p.payload)
                    for p in points
                ],
                wait=wait,
            )
        except (UnexpectedResponse, ValueError) as exc:
            raise VectorStoreError(
                f"Failed to upsert {len(points)} vectors", details={"error": str(exc)}
            ) from exc
        return len(points)

    async def delete_points(self, point_ids: Iterable[str]) -> int:
        ids = [pid for pid in point_ids if pid]
        if not ids:
            return 0
        try:
            await self.client().delete(
                collection_name=self.collection,
                points_selector=models.PointIdsList(points=ids),
                wait=True,
            )
        except (UnexpectedResponse, ValueError) as exc:
            raise VectorStoreError(
                "Failed to delete vectors", details={"error": str(exc), "count": len(ids)}
            ) from exc
        return len(ids)

    async def delete_by_document(self, document_id: uuid.UUID | str) -> int:
        return await self._delete_by_field("document_id", str(document_id))

    async def delete_by_knowledge_base(self, knowledge_base_id: uuid.UUID | str) -> int:
        return await self._delete_by_field("knowledge_base_id", str(knowledge_base_id))

    async def _delete_by_field(self, field: str, value: str) -> int:
        try:
            await self.client().delete(
                collection_name=self.collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key=field, match=models.MatchValue(value=value)
                            )
                        ]
                    )
                ),
                wait=True,
            )
        except (UnexpectedResponse, ValueError) as exc:
            raise VectorStoreError(
                "Failed to delete vectors by filter",
                details={"field": field, "value": value, "error": str(exc)},
            ) from exc
        # Qdrant's delete does not report the affected count; report -1 ("unknown").
        return -1

    # ------------------------------------------------------------------ read --
    async def search(
        self,
        vector: list[float],
        *,
        limit: int = 20,
        knowledge_base_ids: Sequence[uuid.UUID | str] | None = None,
        document_ids: Sequence[uuid.UUID | str] | None = None,
        score_threshold: float | None = None,
    ) -> list[VectorHit]:
        if not vector:
            return []
        must: list[models.Condition] = []
        if knowledge_base_ids:
            must.append(
                models.FieldCondition(
                    key="knowledge_base_id",
                    match=models.MatchAny(any=[str(k) for k in knowledge_base_ids]),
                )
            )
        if document_ids:
            must.append(
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchAny(any=[str(d) for d in document_ids]),
                )
            )
        query_filter = models.Filter(must=must) if must else None

        try:
            if not self._ensured:
                await self.ensure_collection(len(vector))
            response = await self.client().query_points(
                collection_name=self.collection,
                query=vector,
                limit=limit,
                query_filter=query_filter,
                score_threshold=score_threshold,
                with_payload=True,
                with_vectors=False,
            )
        except (UnexpectedResponse, ValueError) as exc:
            raise VectorStoreError(
                "Vector search failed", details={"error": str(exc)}
            ) from exc

        return [
            VectorHit(
                id=str(point.id),
                score=float(point.score or 0.0),
                payload=dict(point.payload or {}),
            )
            for point in response.points
        ]

    async def count(self, knowledge_base_id: uuid.UUID | str | None = None) -> int:
        try:
            result = await self.client().count(
                collection_name=self.collection,
                count_filter=(
                    models.Filter(
                        must=[
                            models.FieldCondition(
                                key="knowledge_base_id",
                                match=models.MatchValue(value=str(knowledge_base_id)),
                            )
                        ]
                    )
                    if knowledge_base_id
                    else None
                ),
                exact=True,
            )
            return int(result.count)
        except (UnexpectedResponse, ValueError) as exc:
            logger.warning("qdrant_count_failed", extra={"error": str(exc)})
            return -1


_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store


async def close_vector_store() -> None:
    global _store
    if _store is not None:
        await _store.close()
        _store = None

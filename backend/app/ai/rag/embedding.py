"""Embedding service.

Responsibilities
----------------
* batch embedding with provider-agnostic calls;
* **content-hash caching** so the same text is never embedded twice (the single
  biggest cost saver when a document is re-processed);
* retry / timeout / error classification delegated to the provider;
* dimension discovery so the Qdrant collection can be created on demand.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from app.ai.providers.factory import build_embedding_provider
from app.core.cache import cache_get, cache_set, make_key
from app.core.errors import AIError
from app.core.logging import get_logger
from app.core.retry import chunked

logger = get_logger(__name__)

NS = "embed"
EMBED_CACHE_TTL = 60 * 60 * 24  # 24h: embeddings are deterministic per (model, text)


def content_hash(text: str, model: str) -> str:
    digest = hashlib.sha256()
    digest.update(model.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(text.encode("utf-8"))
    return digest.hexdigest()[:32]


@dataclass(slots=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    model: str
    provider: str
    dimension: int
    cache_hits: int
    embedded: int


class EmbeddingService:
    def __init__(self) -> None:
        self._provider, self.info = build_embedding_provider()

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def dimension(self) -> int:
        return self._provider.dimension

    async def embed_texts(self, texts: Sequence[str]) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch([], self.model, self.provider_name, self.dimension, 0, 0)

        vectors: list[list[float] | None] = [None] * len(texts)
        pending: list[tuple[int, str]] = []
        cache_hits = 0

        for index, text in enumerate(texts):
            key = make_key(NS, self.model, content_hash(text, self.model))
            cached = await cache_get(NS, key)
            if isinstance(cached, list) and cached:
                vectors[index] = [float(v) for v in cached]
                cache_hits += 1
            else:
                pending.append((index, text))

        embedded = 0
        if pending:
            batch_size = max(1, min(64, len(pending)))
            for batch in chunked(pending, batch_size):
                result = await self._provider.embed([text for _, text in batch])
                if len(result.vectors) != len(batch):
                    raise AIError(
                        "Embedding provider returned a mismatched number of vectors",
                        details={"requested": len(batch), "received": len(result.vectors)},
                    )
                for (index, text), vector in zip(batch, result.vectors, strict=True):
                    vectors[index] = vector
                    embedded += 1
                    await cache_set(
                        NS,
                        make_key(NS, self.model, content_hash(text, self.model)),
                        vector,
                        EMBED_CACHE_TTL,
                    )

        if any(v is None for v in vectors):
            raise AIError("Some texts could not be embedded")

        final = [v for v in vectors if v is not None]
        dimension = len(final[0]) if final else 0
        if self._provider.dimension == 0 and dimension:
            setter = getattr(self._provider, "set_dimension", None)
            if callable(setter):
                setter(dimension)

        logger.info(
            "embedding_completed",
            extra={
                "texts": len(texts),
                "cache_hits": cache_hits,
                "embedded": embedded,
                "dimension": dimension,
                "model": self.model,
            },
        )
        return EmbeddingBatch(
            vectors=final,
            model=self.model,
            provider=self.provider_name,
            dimension=dimension,
            cache_hits=cache_hits,
            embedded=embedded,
        )

    async def embed_query(self, query: str) -> list[float]:
        batch = await self.embed_texts([query])
        return batch.vectors[0] if batch.vectors else []

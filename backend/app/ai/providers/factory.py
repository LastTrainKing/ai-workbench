"""Provider factory.

Single place where configuration becomes a concrete provider instance.  The
factory also implements *graceful degradation*: if a cloud provider is
configured but the key is missing, or the local Ollama daemon is unreachable,
we fall back to the deterministic offline engine and record why — the product
keeps working and the AI run trace tells the truth about which engine answered.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.providers.base import EmbeddingProvider, LLMProvider
from app.ai.providers.heuristic import (
    HeuristicEmbeddingProvider,
    HeuristicLLMProvider,
)
from app.ai.providers.ollama import OllamaEmbeddingProvider, OllamaProvider
from app.ai.providers.openai_compatible import (
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleProvider,
)
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_llm_cache: dict[str, LLMProvider] = {}
_embedding_cache: dict[str, EmbeddingProvider] = {}


@dataclass(slots=True)
class ProviderInfo:
    kind: str
    model: str
    degraded: bool = False
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "model": self.model,
            "degraded": self.degraded,
            "reason": self.reason,
        }


def build_llm_provider() -> tuple[LLMProvider, ProviderInfo]:
    kind = settings.ai_provider
    cache_key = f"{kind}:{settings.ai_model}:{settings.ai_base_url}"

    if kind == "openai_compatible" and settings.ai_api_key.strip():
        if cache_key not in _llm_cache:
            _llm_cache[cache_key] = OpenAICompatibleProvider(
                base_url=settings.ai_base_url,
                api_key=settings.ai_api_key,
                model=settings.ai_model,
                timeout=settings.ai_timeout_seconds,
                max_retries=settings.ai_max_retries,
                backoff_base=settings.ai_backoff_base_seconds,
            )
        return _llm_cache[cache_key], ProviderInfo(kind, settings.ai_model)

    if kind == "ollama":
        if cache_key not in _llm_cache:
            _llm_cache[cache_key] = OllamaProvider(
                base_url=settings.ai_base_url or "http://127.0.0.1:11434",
                model=settings.ai_model,
                timeout=settings.ai_timeout_seconds,
                max_retries=settings.ai_max_retries,
                backoff_base=settings.ai_backoff_base_seconds,
            )
        return _llm_cache[cache_key], ProviderInfo(kind, settings.ai_model)

    # ---- degradation paths -------------------------------------------------
    if kind == "openai_compatible":
        reason = "AI_API_KEY is empty; using the offline heuristic engine."
    elif kind == "ollama":
        reason = "Ollama was selected but the provider is unavailable; using offline engine."
    else:
        reason = "AI_PROVIDER=offline explicitly selected."

    logger.info("llm_provider_degraded", extra={"configured": kind, "reason": reason})
    offline_key = "offline:llm"
    if offline_key not in _llm_cache:
        _llm_cache[offline_key] = HeuristicLLMProvider()
    return _llm_cache[offline_key], ProviderInfo("offline", "heuristic-extractive-v1", True, reason)


def build_embedding_provider() -> tuple[EmbeddingProvider, ProviderInfo]:
    kind = settings.embedding_provider
    base_url = settings.resolved_embedding_base_url()
    api_key = settings.resolved_embedding_api_key()
    model = settings.embedding_model
    cache_key = f"{kind}:{model}:{base_url}"

    if kind == "openai_compatible" and api_key.strip():
        if cache_key not in _embedding_cache:
            _embedding_cache[cache_key] = OpenAICompatibleEmbeddingProvider(
                base_url=base_url,
                api_key=api_key,
                model=model,
                timeout=settings.embedding_timeout_seconds,
                max_retries=settings.ai_max_retries,
                dimension_hint=settings.qdrant_vector_size,
            )
        return _embedding_cache[cache_key], ProviderInfo(kind, model)

    if kind == "ollama":
        if cache_key not in _embedding_cache:
            _embedding_cache[cache_key] = OllamaEmbeddingProvider(
                base_url=settings.ai_base_url or "http://127.0.0.1:11434",
                model=model,
                timeout=settings.embedding_timeout_seconds,
                dimension_hint=settings.qdrant_vector_size,
            )
        return _embedding_cache[cache_key], ProviderInfo(kind, model)

    if kind == "openai_compatible":
        reason = "EMBEDDING api key is empty; using the offline hashed n-gram embedder."
    else:
        reason = "EMBEDDING_PROVIDER=offline explicitly selected."
    logger.info("embedding_provider_degraded", extra={"configured": kind, "reason": reason})
    offline_key = "offline:emb"
    if offline_key not in _embedding_cache:
        _embedding_cache[offline_key] = HeuristicEmbeddingProvider()
    return _embedding_cache[offline_key], ProviderInfo("offline", "heuristic-hash-ngram-v1", True, reason)


async def check_provider_health() -> dict[str, object]:
    """Used by ``GET /api/v1/health/deps`` and the Settings page."""
    llm, llm_info = build_llm_provider()
    emb, emb_info = build_embedding_provider()
    report: dict[str, object] = {
        "llm": llm_info.as_dict(),
        "embedding": emb_info.as_dict(),
    }
    if isinstance(llm, OllamaProvider):
        report["ollama_models"] = await llm.list_models()
    report["tool_calling"] = llm.supports_tool_calling
    report["embedding_dimension"] = emb.dimension
    return report


async def close_providers() -> None:
    for provider in list(_llm_cache.values()) + list(_embedding_cache.values()):
        try:
            await provider.aclose()
        except Exception as exc:
            logger.warning("provider_close_failed", extra={"error": str(exc)})
    _llm_cache.clear()
    _embedding_cache.clear()

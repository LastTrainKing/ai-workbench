"""Provider factory.

Single place where the *resolved* runtime configuration becomes a concrete
provider instance. Resolution order is defined in
:mod:`app.ai.runtime_config`: runtime overrides → environment → defaults, so a
change made in the Settings UI takes effect on the next AI call.

The factory also implements **honest degradation**: when a cloud provider is
selected but no key is available, it falls back to the deterministic offline
engine, records *why*, and reports it through ``GET /api/v1/health/deps`` and on
every ``ai_runs`` row. The product never pretends a hosted model answered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.providers.base import EmbeddingProvider, LLMProvider
from app.ai.providers.budget import reasoning_budget
from app.ai.providers.deepseek import DeepSeekProvider
from app.ai.providers.guard import guard
from app.ai.providers.heuristic import (
    HeuristicEmbeddingProvider,
    HeuristicLLMProvider,
)
from app.ai.providers.ollama import OllamaEmbeddingProvider, OllamaProvider
from app.ai.providers.openai_compatible import (
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleProvider,
)
from app.ai.providers.registry import catalog as _provider_catalog
from app.ai.runtime_config import ResolvedAIConfig, resolve
from app.core.errors import AIAuthError, AIError, AIProviderError
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
    base_url: str = ""
    api_key_set: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "model": self.model,
            "degraded": self.degraded,
            "reason": self.reason,
            "base_url": self.base_url,
            "api_key_set": self.api_key_set,
        }


def _llm_cache_key(cfg: ResolvedAIConfig) -> str:
    # The key must change whenever anything that affects behaviour changes.
    # The API key is hashed rather than embedded so a cache key can never leak
    # a secret into a log line or a traceback.
    import hashlib

    key_hash = hashlib.sha256(cfg.api_key.encode("utf-8")).hexdigest()[:12] if cfg.api_key else "none"
    return f"{cfg.provider}:{cfg.model}:{cfg.base_url}:{key_hash}:{cfg.timeout_seconds}"


def build_llm_provider() -> tuple[LLMProvider, ProviderInfo]:
    """Return the configured chat provider, with the model-call guard applied.

    **This is the public entry point, and the guard is here rather than at the call sites
    on purpose.** The factory has a dozen returns — offline because there is no key,
    offline because the provider is disabled, the real thing when it is configured — and a
    guard applied at each one would be a guard that is missing from whichever branch
    somebody adds next. Wrapping the single public function means every caller gets the
    same behaviour without having to remember anything.
    """
    provider, info = _build_llm_provider()
    return guard(provider, kind=info.kind), info


def _build_llm_provider() -> tuple[LLMProvider, ProviderInfo]:
    """Return the configured chat provider, degrading honestly when unusable."""
    cfg = resolve()
    cache_key = _llm_cache_key(cfg)

    enabled = cfg.enabled_providers or {}
    if cfg.provider != "offline" and not enabled.get(cfg.provider, True):
        return _offline_llm(
            reason=f"Provider '{cfg.provider}' is disabled in Settings; using the offline engine.",
            configured=cfg.provider,
        )

    if cfg.provider == "deepseek":
        if not cfg.api_key_set:
            return _offline_llm(
                reason=(
                    "未配置 DeepSeek API Key。请在 .env 设置 DEEPSEEK_API_KEY，"
                    "或在「设置 → AI Provider」中填写。当前使用离线确定性引擎。"
                ),
                configured="deepseek",
            )
        if cache_key not in _llm_cache:
            try:
                _llm_cache[cache_key] = DeepSeekProvider(
                    api_key=cfg.api_key,
                    model=cfg.model,
                    base_url=cfg.base_url,
                    timeout=cfg.timeout_seconds,
                    max_retries=max(1, min(5, _retries())),
                )
            except AIAuthError as exc:
                return _offline_llm(reason=str(exc), configured="deepseek")
        return _llm_cache[cache_key], ProviderInfo(
            kind="deepseek",
            model=cfg.model,
            base_url=cfg.base_url,
            api_key_set=True,
        )

    if cfg.provider == "openai_compatible":
        if not cfg.api_key_set:
            return _offline_llm(
                reason=(
                    "AI_API_KEY is empty for the openai_compatible provider; "
                    "using the offline heuristic engine."
                ),
                configured="openai_compatible",
            )
        if cache_key not in _llm_cache:
            try:
                _llm_cache[cache_key] = OpenAICompatibleProvider(
                    base_url=cfg.base_url,
                    api_key=cfg.api_key,
                    model=cfg.model,
                    timeout=cfg.timeout_seconds,
                    max_retries=max(1, min(5, _retries())),
                )
            except AIAuthError as exc:
                # The deepseek branch above has always done this; this one raised, so the
                # same unusable key behaved differently depending on which of the two
                # names the account had selected.
                return _offline_llm(reason=str(exc), configured="openai_compatible")
        return _llm_cache[cache_key], ProviderInfo(
            kind="openai_compatible",
            model=cfg.model,
            base_url=cfg.base_url,
            api_key_set=True,
        )

    if cfg.provider == "ollama":
        if cache_key not in _llm_cache:
            _llm_cache[cache_key] = OllamaProvider(
                base_url=cfg.base_url or "http://127.0.0.1:11434",
                model=cfg.model,
                timeout=cfg.timeout_seconds,
                max_retries=max(1, min(5, _retries())),
            )
        return _llm_cache[cache_key], ProviderInfo(
            kind="ollama",
            model=cfg.model,
            base_url=cfg.base_url,
            api_key_set=False,
        )

    return _offline_llm(reason="AI_PROVIDER=offline was selected explicitly.")


def _retries() -> int:
    from app.core.config import settings

    return settings.ai_max_retries


def _offline_llm(*, reason: str, configured: str = "offline") -> tuple[LLMProvider, ProviderInfo]:
    logger.info("llm_provider_degraded", extra={"configured": configured, "reason": reason})
    key = "offline:llm"
    if key not in _llm_cache:
        _llm_cache[key] = HeuristicLLMProvider()
    return _llm_cache[key], ProviderInfo(
        kind="offline",
        model="heuristic-extractive-v1",
        degraded=True,
        reason=reason,
    )


def build_embedding_provider() -> tuple[EmbeddingProvider, ProviderInfo]:
    """Embeddings follow their own configuration (they often stay local)."""
    cfg = resolve()
    kind = cfg.embedding_provider
    model = cfg.embedding_model
    base_url = cfg.embedding_base_url or cfg.base_url
    api_key = cfg.embedding_api_key or (cfg.api_key if kind == "deepseek" else "")
    cache_key = f"{kind}:{model}:{base_url}"

    if kind == "openai_compatible" and api_key.strip():
        if cache_key not in _embedding_cache:
            _embedding_cache[cache_key] = OpenAICompatibleEmbeddingProvider(
                base_url=base_url,
                api_key=api_key,
                model=model,
                timeout=_embedding_timeout(),
                max_retries=_retries(),
                dimension_hint=_vector_size(),
            )
        return _embedding_cache[cache_key], ProviderInfo(
            kind=kind, model=model, base_url=base_url, api_key_set=True
        )

    if kind == "ollama":
        if cache_key not in _embedding_cache:
            _embedding_cache[cache_key] = OllamaEmbeddingProvider(
                base_url=cfg.base_url or "http://127.0.0.1:11434",
                model=model,
                timeout=_embedding_timeout(),
                dimension_hint=_vector_size(),
            )
        return _embedding_cache[cache_key], ProviderInfo(kind=kind, model=model, base_url=base_url)

    reason = (
        "Embedding 未配置可用的云端 Key；使用离线哈希 n-gram 向量化。"
        if kind != "offline"
        else "EMBEDDING_PROVIDER=offline was selected explicitly."
    )
    logger.info("embedding_provider_degraded", extra={"configured": kind, "reason": reason})
    key = "offline:emb"
    if key not in _embedding_cache:
        _embedding_cache[key] = HeuristicEmbeddingProvider()
    return _embedding_cache[key], ProviderInfo(
        kind="offline",
        model="heuristic-hash-ngram-v1",
        degraded=True,
        reason=reason,
    )


def _embedding_timeout() -> float:
    from app.core.config import settings

    return settings.embedding_timeout_seconds


def _vector_size() -> int:
    from app.core.config import settings

    return settings.qdrant_vector_size


# --------------------------------------------------------------------------- #
# Health / introspection
# --------------------------------------------------------------------------- #
async def check_provider_health() -> dict[str, Any]:
    """Used by ``GET /api/v1/health/deps`` and the Settings page."""
    cfg = resolve()
    llm, llm_info = build_llm_provider()
    emb, emb_info = build_embedding_provider()

    report: dict[str, Any] = {
        "llm": llm_info.as_dict(),
        "embedding": emb_info.as_dict(),
        "configured": {
            "provider": cfg.provider,
            "model": cfg.model,
            "base_url": cfg.base_url,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
            "timeout_seconds": cfg.timeout_seconds,
            "api_key_set": cfg.api_key_set,
        },
        "sources": cfg.sources,
        "tool_calling": llm.supports_tool_calling,
        "embedding_dimension": emb.dimension,
    }

    if isinstance(llm, DeepSeekProvider):
        report["deepseek_models"] = await llm.list_models()
    if isinstance(llm, OllamaProvider):
        report["ollama_models"] = await llm.list_models()

    return report


def provider_catalog() -> list[dict[str, Any]]:
    """Re-exported so callers do not need to know about the registry module."""
    return _provider_catalog()


async def test_connection(*, provider: str | None = None, model: str | None = None) -> dict[str, Any]:
    """Send one minimal request to prove the current configuration works.

    The budget is small but not minimal: a reasoning model spends the allowance
    on its chain of thought before writing anything, so a 1-token (or even
    32-token) probe would report a failure for a perfectly healthy endpoint.
    256 tokens costs a negligible fraction of a cent and always answers.
    """
    from app.ai.types import ChatMessage

    llm, info = build_llm_provider()
    if provider and provider != info.kind:
        raise AIError(
            f"The active provider is '{info.kind}', not '{provider}'. "
            "Apply the configuration before testing.",
            code="ai_provider_mismatch",
            status_code=409,
        )

    started = __import__("time").perf_counter()
    try:
        result = await llm.chat(
            [ChatMessage(role="user", content="ping")],
            temperature=0.0,
            # A reasoning model spends thinking tokens before it emits anything, so a
            # 256-token cap can fail a provider that is perfectly healthy — reporting a
            # working model as degraded. `max_tokens` is a cap, not a target: a ping that
            # thinks for 300 tokens costs the same either way.
            max_tokens=reasoning_budget(),
        )
    except AIError as exc:
        return {
            "ok": False,
            "provider": info.kind,
            "model": info.model,
            "error_code": exc.code,
            "error_message": exc.message,
            "latency_ms": int((__import__("time").perf_counter() - started) * 1000),
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": info.kind,
            "model": info.model,
            "error_code": "unexpected_error",
            "error_message": f"{type(exc).__name__}: {exc}",
            "latency_ms": int((__import__("time").perf_counter() - started) * 1000),
        }

    return {
        "ok": True,
        "provider": result.provider or info.kind,
        "model": result.model or info.model,
        "degraded": info.degraded,
        "latency_ms": result.latency_ms,
        "usage": {
            "prompt_tokens": result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
            "total_tokens": result.usage.total_tokens,
        },
    }


async def close_providers() -> None:
    for provider in list(_llm_cache.values()) + list(_embedding_cache.values()):
        try:
            await provider.aclose()
        except Exception as exc:
            logger.warning("provider_close_failed", extra={"error": str(exc)})
    _llm_cache.clear()
    _embedding_cache.clear()


def reset_provider_cache() -> None:
    """Drop cached instances so a configuration change is picked up immediately."""
    _llm_cache.clear()
    _embedding_cache.clear()


__all__ = [
    "AIProviderError",
    "ProviderInfo",
    "build_embedding_provider",
    "build_llm_provider",
    "check_provider_health",
    "close_providers",
    "provider_catalog",
    "reset_provider_cache",
    "test_connection",
]

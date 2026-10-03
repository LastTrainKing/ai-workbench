"""AI provider configuration endpoints.

These back the Settings → AI Provider screen. Everything here is a *real*
control surface: changing the provider changes which implementation answers the
next RAG query, document analysis, plan or agent run.

Secrets
-------
The API key is write-only. It is never returned by any endpoint — reads expose
``api_key_set`` and a masked hint (``sk-1***cd34``) only. Values are persisted to
``.runtime/ai-config.json`` (git-ignored, excluded from the delivery archive),
with ``.env`` as the fallback baseline.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.ai.providers import registry as provider_registry
from app.ai.providers.factory import (
    check_provider_health,
    provider_catalog,
    reset_provider_cache,
    test_connection,
)
from app.ai.runtime_config import OVERRIDABLE, resolve
from app.ai.runtime_config import clear as clear_runtime_config
from app.ai.runtime_config import load as load_runtime_config
from app.ai.runtime_config import save as save_runtime_config
from app.api.deps import CurrentUser
from app.core.errors import ValidationError
from app.core.response import ok

router = APIRouter(prefix="/ai-config", tags=["ai-config"])


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class AIConfigUpdate(BaseModel):
    """Partial update. Omitted fields are left untouched; ``null`` clears one."""

    provider: str | None = Field(
        default=None, description="deepseek | openai_compatible | ollama | offline"
    )
    model: str | None = Field(default=None, max_length=128)
    base_url: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(
        default=None,
        max_length=300,
        description="Write-only. Never returned by any endpoint.",
    )
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=32_000)
    timeout_seconds: float | None = Field(default=None, ge=5.0, le=600.0)
    embedding_provider: str | None = None
    embedding_model: str | None = Field(default=None, max_length=128)
    embedding_base_url: str | None = Field(default=None, max_length=300)
    embedding_api_key: str | None = Field(default=None, max_length=300)
    enabled_providers: dict[str, bool] | None = None


class ProviderToggle(BaseModel):
    provider: str
    enabled: bool


class ConnectionTestRequest(BaseModel):
    provider: str | None = Field(
        default=None, description="Assert that this provider is the active one before testing"
    )


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #
@router.get("", summary="Effective AI configuration, provider catalogue and overrides")
async def get_ai_config(user: CurrentUser) -> dict[str, Any]:
    cfg = resolve()
    return ok(
        {
            "effective": cfg.safe(),
            "overrides": _masked_overrides(),
            "catalog": provider_catalog(),
            "runtime_config_path": ".runtime/ai-config.json (git-ignored, local only)",
            "editable_fields": list(OVERRIDABLE),
        }
    )


@router.get("/providers", summary="Provider catalogue")
async def list_providers(user: CurrentUser) -> dict[str, Any]:
    return ok({"providers": provider_catalog(), "count": len(provider_registry.PROVIDERS)})


@router.get("/models", summary="Models offered by a provider")
async def list_models(
    user: CurrentUser,
    provider: str = Query(..., max_length=64),
    live: bool = Query(False, description="Query the vendor for its real model list"),
) -> dict[str, Any]:
    descriptor = provider_registry.get(provider)
    if descriptor is None:
        raise ValidationError(
            f"Unknown provider '{provider}'", details={"allowed": provider_registry.ids()}
        )

    models = [{"id": m.id, "label": m.label, "description": m.description} for m in descriptor.models]

    # Optionally ask the vendor. Only attempted when the provider is active and
    # has a key, so this cannot accidentally bill an unconfigured account.
    if live and descriptor.id == "deepseek":
        from app.ai.providers.deepseek import DeepSeekProvider
        from app.ai.providers.factory import build_llm_provider

        llm, _ = build_llm_provider()
        if isinstance(llm, DeepSeekProvider):
            models = await llm.list_models()

    return ok({"provider": descriptor.id, "models": models, "live": bool(live)})


@router.get("/health", summary="Which provider is actually answering, and why")
async def ai_health(user: CurrentUser) -> dict[str, Any]:
    return ok(await check_provider_health())


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #
@router.patch("", summary="Change the active AI provider / model / key")
async def update_ai_config(payload: AIConfigUpdate, user: CurrentUser) -> dict[str, Any]:
    patch = payload.model_dump(exclude_unset=True)

    if patch.get("provider") is not None:
        provider = str(patch["provider"]).strip().lower()
        if provider_registry.get(provider) is None:
            raise ValidationError(
                f"Unknown provider '{provider}'", details={"allowed": provider_registry.ids()}
            )
        patch["provider"] = provider

    if patch.get("embedding_provider") is not None:
        embedding_provider = str(patch["embedding_provider"]).strip().lower()
        if embedding_provider not in {"openai_compatible", "ollama", "offline"}:
            raise ValidationError(
                "embedding_provider must be one of: openai_compatible, ollama, offline",
                details={"got": embedding_provider},
            )
        patch["embedding_provider"] = embedding_provider

    if patch.get("enabled_providers") is not None:
        unknown = set(patch["enabled_providers"]) - set(provider_registry.ids())
        if unknown:
            raise ValidationError(
                "Unknown provider(s) in enabled_providers", details={"unknown": sorted(unknown)}
            )

    save_runtime_config(patch)
    # Cached provider instances keyed on the old configuration must not survive.
    reset_provider_cache()

    cfg = resolve()
    return ok(
        {
            "effective": cfg.safe(),
            "overrides": _masked_overrides(),
            "catalog": provider_catalog(),
        },
        message="AI 配置已更新，下一次 AI 调用即生效",
    )


@router.post("/providers/toggle", summary="Enable or disable a provider in the UI")
async def toggle_provider(payload: ProviderToggle, user: CurrentUser) -> dict[str, Any]:
    if provider_registry.get(payload.provider) is None:
        raise ValidationError(
            f"Unknown provider '{payload.provider}'",
            details={"allowed": provider_registry.ids()},
        )
    current = dict(load_runtime_config().get("enabled_providers") or {})
    current[payload.provider] = bool(payload.enabled)
    save_runtime_config({"enabled_providers": current})
    reset_provider_cache()
    return ok({"enabled_providers": current}, message="Provider 状态已更新")


@router.delete("", summary="Clear all overrides and fall back to .env")
async def reset_ai_config(user: CurrentUser) -> dict[str, Any]:
    clear_runtime_config()
    reset_provider_cache()
    cfg = resolve()
    return ok(
        {"effective": cfg.safe(), "catalog": provider_catalog()},
        message="已恢复为 .env 中的配置",
    )


@router.post("/test", summary="Send one minimal request to prove the configuration works")
async def run_connection_test(payload: ConnectionTestRequest, user: CurrentUser) -> dict[str, Any]:
    result = await test_connection(provider=payload.provider)
    return ok(result, message="连接成功" if result["ok"] else "连接失败")


def _masked_overrides() -> dict[str, Any]:
    """Runtime overrides with every secret replaced by a mask."""
    from app.ai.runtime_config import mask_api_key

    raw = dict(load_runtime_config())
    if "api_key" in raw:
        raw["api_key"] = mask_api_key(str(raw["api_key"]))
        raw["api_key_set"] = True
    if "embedding_api_key" in raw:
        raw["embedding_api_key"] = mask_api_key(str(raw["embedding_api_key"]))
        raw["embedding_api_key_set"] = True
    return raw

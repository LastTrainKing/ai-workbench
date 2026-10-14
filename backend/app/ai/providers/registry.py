"""Provider registry — the catalogue the Settings UI reads.

Adding a new provider is a two-step change:

1. implement it (or reuse ``OpenAICompatibleProvider`` when the vendor speaks the
   OpenAI contract, as DeepSeek and most others do);
2. add one :class:`ProviderDescriptor` entry to :data:`PROVIDERS`.

Nothing else in the codebase needs to change: the factory, the health endpoint
and the UI all read this catalogue.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.ai.runtime_config import resolve
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class ModelDescriptor:
    id: str
    label: str
    description: str = ""


@dataclass(slots=True)
class ProviderDescriptor:
    id: str
    label: str
    kind: str  # "cloud" | "local" | "offline"
    requires_api_key: bool
    default_base_url: str = ""
    default_model: str = ""
    models: list[ModelDescriptor] = field(default_factory=list)
    description: str = ""
    docs_url: str = ""
    #: True when the provider needs a locally running daemon.
    requires_daemon: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["models"] = [asdict(m) for m in self.models]
        return payload


PROVIDERS: list[ProviderDescriptor] = [
    ProviderDescriptor(
        id="deepseek",
        label="DeepSeek",
        kind="cloud",
        requires_api_key=True,
        default_base_url="https://api.deepseek.com",
        default_model="deepseek-flash",
        description="DeepSeek 官方 API，OpenAI 兼容。当前默认 Provider。",
        docs_url="https://platform.deepseek.com/api-docs/",
        models=[
            ModelDescriptor(
                id="deepseek-flash",
                label="DeepSeek V4.1 Flash",
                description="快速、低成本；适合 RAG 问答、抽取与任务规划（默认）",
            ),
            ModelDescriptor(
                id="deepseek-v4-pro",
                label="DeepSeek V4 Pro",
                description="更强推理；适合复杂规划与多步 Agent，成本更高",
            ),
        ],
    ),
    ProviderDescriptor(
        id="openai_compatible",
        label="OpenAI Compatible",
        kind="cloud",
        requires_api_key=True,
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        description=(
            "任何兼容 OpenAI 协议的端点：OpenAI、通义千问、Moonshot、智谱、"
            "SiliconFlow、vLLM、LM Studio 等。填写 Base URL 与模型名即可。"
        ),
        models=[
            ModelDescriptor(id="gpt-4o-mini", label="OpenAI gpt-4o-mini"),
            ModelDescriptor(id="qwen-plus", label="通义千问 qwen-plus"),
            ModelDescriptor(id="Qwen/Qwen2.5-7B-Instruct", label="SiliconFlow Qwen2.5-7B"),
        ],
    ),
    ProviderDescriptor(
        id="ollama",
        label="Ollama (本地)",
        kind="local",
        requires_api_key=False,
        requires_daemon=True,
        default_base_url="http://127.0.0.1:11434",
        default_model="qwen2.5:7b",
        description="完全本地推理，数据不出本机。需要先运行 `ollama serve` 并拉取模型。",
        docs_url="https://ollama.com/library",
        models=[
            ModelDescriptor(id="qwen2.5:7b", label="Qwen2.5 7B"),
            ModelDescriptor(id="llama3.1:8b", label="Llama 3.1 8B"),
            ModelDescriptor(id="deepseek-r1:7b", label="DeepSeek-R1 7B"),
        ],
    ),
    ProviderDescriptor(
        id="offline",
        label="Offline (确定性引擎)",
        kind="offline",
        requires_api_key=False,
        default_model="heuristic-extractive-v1",
        description=(
            "内置确定性抽取式引擎，不调用任何外部服务。答案完全来自检索片段，"
            "不会编造，但生成能力有限。未配置 Key 时自动降级到它。"
        ),
        models=[
            ModelDescriptor(id="heuristic-extractive-v1", label="heuristic-extractive-v1"),
        ],
    ),
]

_BY_ID = {p.id: p for p in PROVIDERS}


def catalog(*, configured: dict[str, bool] | None = None) -> list[dict[str, Any]]:
    """Return every provider with its enabled state and whether it is usable."""
    resolved = None
    try:
        resolved = resolve()
    except Exception as exc:
        logger.warning("provider_catalog_resolve_failed", extra={"error": str(exc)})

    enabled = (resolved.enabled_providers if resolved else {}) or {}
    out: list[dict[str, Any]] = []
    for provider in PROVIDERS:
        entry = provider.as_dict()
        entry["enabled"] = bool(enabled.get(provider.id, True))
        entry["configured"] = _is_configured(provider, resolved)
        out.append(entry)
    return out


def _is_configured(provider: ProviderDescriptor, resolved: Any) -> bool:
    if provider.kind == "offline":
        return True
    if resolved is None:
        return False
    if provider.id == resolved.provider and resolved.api_key_set:
        return True
    # A key supplied for a provider that is not currently selected still counts
    # as configured if the DeepSeek namespace holds one.
    if provider.id == "deepseek":
        from app.core.config import settings

        return bool((resolved.api_key or settings.deepseek_api_key).strip())
    if provider.id == "openai_compatible":
        from app.core.config import settings

        return bool((resolved.api_key or settings.ai_api_key).strip())
    # Ollama usability is verified by the health probe, not by an API key.
    return provider.id == "ollama"


def get(provider_id: str) -> ProviderDescriptor | None:
    return _BY_ID.get((provider_id or "").strip().lower())


def ids() -> list[str]:
    return [p.id for p in PROVIDERS]


__all__ = [
    "PROVIDERS",
    "ModelDescriptor",
    "ProviderDescriptor",
    "catalog",
    "get",
    "ids",
]

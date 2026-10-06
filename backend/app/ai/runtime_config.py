"""Runtime AI configuration — the single source of truth for "which model answers".

Precedence (highest first):

1. **Runtime overrides** written by the Settings UI into
   ``.runtime/ai-config.json`` (git-ignored, local machine only).
2. **Environment variables** from ``.env`` (``DEEPSEEK_API_KEY``, ``AI_PROVIDER``, …).
3. **Built-in defaults** in ``app.core.config.Settings``.

Why a file and not a database table
-----------------------------------
An API key is a machine-local secret, not application data. Keeping it out of
PostgreSQL means it is not in the database dump, not in a backup, and not visible
to anything that can read the database. ``.runtime/`` is already git-ignored and
already excluded from the delivery archive, so the key cannot leak into Git, the
ZIP, the logs or the screenshots.

The file is re-read when its modification time changes, so a change made through
the UI (or by hand) takes effect on the very next AI call without a restart.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import RUNTIME_ROOT, settings
from app.core.errors import ValidationError
from app.core.logging import get_logger, mask_secret

logger = get_logger(__name__)

CONFIG_PATH: Path = RUNTIME_ROOT / "ai-config.json"

# Fields the UI is allowed to override.
OVERRIDABLE = (
    "provider",
    "model",
    "base_url",
    "api_key",
    "temperature",
    "max_tokens",
    "timeout_seconds",
    "embedding_provider",
    "embedding_model",
    "embedding_base_url",
    "embedding_api_key",
    "enabled_providers",
)

_PROVIDER_VALUES = {"deepseek", "openai_compatible", "ollama", "offline"}

_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_cache_mtime: float = -1.0


# --------------------------------------------------------------------------- #
# Raw store
# --------------------------------------------------------------------------- #
def _read_file() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        # A corrupt override file must not take the product down: fall back to
        # environment configuration and say so loudly.
        logger.error(
            "ai_config_unreadable",
            extra={"path": str(CONFIG_PATH), "error": f"{type(exc).__name__}: {exc}"},
        )
        return {}


def load(*, force: bool = False) -> dict[str, Any]:
    """Return the runtime overrides, re-reading the file when it changes."""
    global _cache, _cache_mtime
    with _lock:
        try:
            mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else -1.0
        except OSError:
            mtime = -1.0
        if force or _cache is None or mtime != _cache_mtime:
            _cache = _read_file()
            _cache_mtime = mtime
        return dict(_cache or {})


def save(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge ``patch`` into the override file. ``None`` values clear a key."""
    unknown = set(patch) - set(OVERRIDABLE)
    if unknown:
        raise ValidationError(
            "Unknown AI configuration field(s)",
            details={"unknown": sorted(unknown), "allowed": list(OVERRIDABLE)},
        )

    with _lock:
        current = _read_file()
        for key, value in patch.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        current["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_PATH)

        # Restrict the file to the current user where the OS supports it
        # (Windows does not always honour POSIX modes).
        with contextlib.suppress(OSError):
            CONFIG_PATH.chmod(0o600)

    global _cache, _cache_mtime
    _cache = None
    _cache_mtime = -1.0
    logger.info(
        "ai_config_updated",
        extra={
            "fields": sorted(patch),
            "api_key": mask_secret(patch.get("api_key")) if "api_key" in patch else None,
        },
    )
    return load(force=True)


def clear() -> None:
    """Remove all overrides so configuration falls back to ``.env``."""
    with _lock:
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
    global _cache, _cache_mtime
    _cache = None
    _cache_mtime = -1.0
    logger.info("ai_config_cleared")


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ResolvedAIConfig:
    """The effective configuration an AI call will actually use."""

    provider: str
    model: str
    base_url: str
    api_key: str
    temperature: float
    max_tokens: int
    timeout_seconds: float
    embedding_provider: str
    embedding_model: str
    embedding_base_url: str
    embedding_api_key: str
    enabled_providers: dict[str, bool] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def api_key_set(self) -> bool:
        return bool(self.api_key.strip())

    def safe(self) -> dict[str, Any]:
        """Serialisable form with the key masked — safe to return over HTTP.

        Only the vendor prefix and the length are exposed. The tail is *not*
        included: this value ends up in screenshots and bug reports, and eight
        characters of a real key are eight characters an attacker does not have
        to guess.
        """
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_set": self.api_key_set,
            "api_key_hint": mask_api_key(self.api_key),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_base_url": self.embedding_base_url,
            "embedding_api_key_set": bool(self.embedding_api_key.strip()),
            "enabled_providers": self.enabled_providers,
            "sources": self.sources,
        }


def mask_api_key(value: str | None) -> str:
    """Return ``sk-****(len=35)`` — enough to identify a key, not to use one."""
    if not value or not value.strip():
        return ""
    raw = value.strip()
    prefix = raw.split("-")[0] + "-" if "-" in raw[:6] else ""
    return f"{prefix}****(len={len(raw)})"


def _first(*candidates: tuple[Any, str]) -> tuple[Any, str]:
    """Return the first non-empty value together with where it came from."""
    for value, origin in candidates:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value, origin
    return None, "default"


def resolve() -> ResolvedAIConfig:
    """Merge runtime overrides over environment configuration over defaults."""
    runtime = load()
    sources: dict[str, str] = {}

    def pick(field_name: str, *env_values: tuple[Any, str], default: Any = None) -> Any:
        """First non-empty value wins: runtime override → env → default.

        NOTE: the fallback must return ``default`` itself, not ``str(None)``.
        Converting a missing value to the *string* ``"None"`` would make an empty
        API key look configured — which is exactly the kind of silent failure
        that makes a product claim a cloud model is answering when it is not.
        """
        candidates = ((runtime.get(field_name), "runtime"), *env_values)
        value, origin = _first(*candidates)
        if value is None:
            sources[field_name] = "default"
            return default
        sources[field_name] = origin
        return value

    provider = str(
        pick("provider", (settings.ai_provider, "env"), default="deepseek")
    ).lower()
    if provider not in _PROVIDER_VALUES:
        raise ValidationError(
            f"Unknown AI provider '{provider}'",
            details={"allowed": sorted(_PROVIDER_VALUES)},
        )

    # DeepSeek keeps its own env namespace so a key can be supplied without
    # touching the generic variables.
    deepseek_key = settings.deepseek_api_key or settings.ai_api_key
    deepseek_base = settings.deepseek_base_url or settings.ai_base_url

    if provider == "deepseek":
        base_url = str(pick("base_url", (deepseek_base, "env"), default="https://api.deepseek.com"))
        api_key = str(pick("api_key", (deepseek_key, "env"), default=""))
        model = str(pick("model", (settings.deepseek_model, "env"), default="deepseek-flash"))
    elif provider == "ollama":
        base_url = str(pick("base_url", (settings.ai_base_url, "env"), default="http://127.0.0.1:11434"))
        api_key = str(pick("api_key", (None, "env"), default=""))
        model = str(pick("model", (settings.ai_model, "env"), default="qwen2.5:7b"))
    else:  # openai_compatible | offline
        base_url = str(pick("base_url", (settings.ai_base_url, "env"), default=""))
        api_key = str(pick("api_key", (settings.ai_api_key, "env"), default=""))
        model = str(pick("model", (settings.ai_model, "env"), default="deepseek-flash"))

    embedding_provider = str(
        pick(
            "embedding_provider",
            (settings.embedding_provider, "env"),
            default="offline",
        )
    ).lower()

    return ResolvedAIConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=float(pick("temperature", (settings.ai_temperature, "env"), default=0.2)),
        max_tokens=int(pick("max_tokens", (settings.ai_max_tokens, "env"), default=2048)),
        timeout_seconds=float(
            pick("timeout_seconds", (settings.ai_timeout_seconds, "env"), default=90.0)
        ),
        embedding_provider=embedding_provider,
        embedding_model=str(
            pick("embedding_model", (settings.embedding_model, "env"), default="text-embedding-3-small")
        ),
        embedding_base_url=str(
            pick(
                "embedding_base_url",
                (settings.resolved_embedding_base_url(), "env"),
                default="",
            )
        ),
        embedding_api_key=str(
            pick(
                "embedding_api_key",
                (settings.resolved_embedding_api_key(), "env"),
                default="",
            )
        ),
        enabled_providers=dict(
            runtime.get("enabled_providers")
            or dict.fromkeys(sorted(_PROVIDER_VALUES), True)
        ),
        sources=sources,
    )

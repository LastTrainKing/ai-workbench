"""Runtime AI configuration — the single source of truth for "which model answers".

Precedence (highest first):

1. **This account's overrides** — ``.runtime/ai-config/<owner_id>.json``.
2. **The installation's overrides** — ``.runtime/ai-config.json``, written by whoever
   set the machine up. This is the install-level default, not a shared setting: a
   request never writes here, so one account cannot change another's model.
3. **Environment variables** from ``.env`` (``DEEPSEEK_API_KEY``, ``AI_PROVIDER``, …).
4. **Built-in defaults** in ``app.core.config.Settings``.

Why per-account, and what was wrong before
------------------------------------------
There was one file. Every account read and wrote it, so account A changing the model,
the base URL or the API key changed what account B's *next* call would use. The response
masked the key, which made it look contained, but the effect was not: A could point B's
prompts and B's uploaded documents at an endpoint A controlled, and the bill for A's
changes arrived on B's provider account. A setting that belongs to a person cannot live
in a file that belongs to the machine.

The install-level file is kept, and is read as a fallback, because an existing install
already has one and its contents should not silently disappear on upgrade. It is now
read-only from the API's point of view.

Why a file and not a database table
-----------------------------------
An API key is a machine-local secret, not application data. Keeping it out of
PostgreSQL means it is not in the database dump, not in a backup, and not visible to
anything that can read the database. ``.runtime/`` is already git-ignored and already
excluded from the delivery archive, so the key cannot leak into Git, the ZIP, the logs
or the screenshots.

The files are re-read when their modification time changes, so a change made through the
UI (or by hand) takes effect on the very next AI call without a restart.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.core.config import RUNTIME_ROOT, settings
from app.core.errors import ValidationError
from app.core.logging import get_logger, mask_secret

logger = get_logger(__name__)

#: The install-level file. Read as a fallback; never written by a request.
CONFIG_PATH: Path = RUNTIME_ROOT / "ai-config.json"
#: Per-account overrides.
CONFIG_DIR: Path = RUNTIME_ROOT / "ai-config"

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

# --------------------------------------------------------------------------- #
# Which account is this call for?
# --------------------------------------------------------------------------- #
# `build_llm_provider()` is called from deep inside the RAG pipeline, the agent and the
# services, none of which take an owner argument. Threading one through every one of them
# would be a large change to code that is otherwise correct. The request dependency sets
# this once, and `resolve` reads it when the caller does not name an owner — which keeps
# the change in one place and makes the default explicit rather than implicit.
_owner_var: ContextVar[str | None] = ContextVar("ai_config_owner", default=None)


def set_current_owner(owner_id: Any) -> None:
    """Bind this request/task to an account. Called by the auth dependency."""
    _owner_var.set(str(owner_id) if owner_id else None)


def current_owner() -> str | None:
    return _owner_var.get()


def _coerce_owner(owner_id: Any) -> str | None:
    if owner_id is None:
        return None
    text = str(owner_id).strip()
    return text or None


def _path_for(owner_id: Any, root: Path | None = None) -> Path:
    """Where one layer's file lives, under the configured runtime root or an explicit one.

    ``root`` exists for the live integration tests. They are the one place that must read
    the **machine's real** configuration — their whole purpose is "does the real service
    work with the real credentials" — while the rest of the suite is deliberately pointed
    at a throwaway runtime directory so it cannot pick up a developer's key. Passing the
    root explicitly keeps both true without a second copy of the secret anywhere.
    """
    if root is None:
        config_path, config_dir = CONFIG_PATH, CONFIG_DIR
    else:
        config_path = root / "ai-config.json"
        config_dir = root / "ai-config"

    owner = _coerce_owner(owner_id)
    if owner is None:
        return config_path
    # `owner` is a user UUID, but it arrives as a string from a path/claim, so it is
    # sanitised rather than trusted: a value containing a separator would otherwise be a
    # path traversal into another file.
    safe = "".join(ch for ch in owner if ch.isalnum() or ch in "-_")
    if not safe:
        raise ValidationError("Invalid account id for AI configuration")
    return config_dir / f"{safe}.json"


_lock = threading.Lock()
#: path -> (mtime, parsed). A single-entry cache was fine when there was one file.
_cache: dict[Path, tuple[float, dict[str, Any]]] = {}


# --------------------------------------------------------------------------- #
# Raw store
# --------------------------------------------------------------------------- #
def _read_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        # A corrupt override file must not take the product down: fall back to
        # environment configuration and say so loudly.
        logger.error(
            "ai_config_unreadable",
            extra={"path": str(path), "error": f"{type(exc).__name__}: {exc}"},
        )
        return {}


def load(owner_id: Any = None, *, force: bool = False, root: Path | None = None) -> dict[str, Any]:
    """Return one layer of overrides, re-reading the file when it changes.

    ``owner_id=None`` means the install-level file. Anything else means that account's
    own file, which is the only one a request ever writes.

    ``root`` reads the layer from an explicit runtime root instead of the configured one.
    Only the live integration tests use it; see ``_path_for``.
    """
    path = _path_for(owner_id, root)
    with _lock:
        try:
            mtime = path.stat().st_mtime if path.exists() else -1.0
        except OSError:
            mtime = -1.0
        cached = _cache.get(path)
        if force or cached is None or mtime != cached[0]:
            _cache[path] = (mtime, _read_file(path))
        return dict(_cache[path][1])


def save(owner_id: Any, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge ``patch`` into **this account's** override file. ``None`` clears a key.

    There is deliberately no way to write the install-level file through this function:
    a request must not be able to change what every other account falls back to.
    """
    unknown = set(patch) - set(OVERRIDABLE)
    if unknown:
        raise ValidationError(
            "Unknown AI configuration field(s)",
            details={"unknown": sorted(unknown), "allowed": list(OVERRIDABLE)},
        )

    _validate_endpoint_fields(patch)
    _normalise_secret_fields(patch)

    path = _path_for(owner_id)
    if path == CONFIG_PATH:
        raise ValidationError(
            "AI configuration must be written per account, not installation-wide",
            code="ai_config_scope_required",
        )

    with _lock:
        current = _read_file(path)
        for key, value in patch.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        current["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

        # Restrict the file to the current user where the OS supports it
        # (Windows does not always honour POSIX modes).
        with contextlib.suppress(OSError):
            path.chmod(0o600)

    _cache.pop(path, None)
    logger.info(
        "ai_config_updated",
        extra={
            "owner": _coerce_owner(owner_id),
            "fields": sorted(patch),
            "api_key": mask_secret(patch.get("api_key")) if "api_key" in patch else None,
        },
    )
    return load(owner_id, force=True)


def clear(owner_id: Any) -> None:
    """Remove this account's overrides so it falls back to the installation's config."""
    path = _path_for(owner_id)
    if path == CONFIG_PATH:
        raise ValidationError(
            "AI configuration must be reset per account, not installation-wide",
            code="ai_config_scope_required",
        )
    with _lock:
        if path.exists():
            path.unlink()
        _cache.pop(path, None)
    logger.info("ai_config_cleared", extra={"owner": _coerce_owner(owner_id)})


# --------------------------------------------------------------------------- #
# Endpoint validation
# --------------------------------------------------------------------------- #
#: Schemes a provider base URL may use. `file:`, `ftp:`, `gopher:` and friends are not
#: base URLs for an HTTP client, and accepting them turns a configuration field into an
#: arbitrary-protocol request.
_ALLOWED_SCHEMES = {"http", "https"}
#: Hosts for which plain HTTP is allowed, because they are this machine. A remote
#: endpoint over HTTP sends the API key and the prompt in clear text.
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "host.docker.internal"}


def validate_endpoint(url: str, *, field_name: str = "base_url") -> str:
    """Check a user-supplied provider endpoint. Returns the value unchanged.

    Rules, and why each one exists:

    * **HTTPS for anything remote.** The key and the prompt travel in this request.
    * **HTTP only for a host that is this machine** — a local Ollama or a local
      OpenAI-compatible server has no certificate to offer.
    * **No userinfo.** ``https://user:pass@host`` embeds credentials in a value that is
      displayed, logged and stored, and some HTTP clients forward them.
    * **No fragment/query.** A base URL is a prefix, not a request.
    """
    raw = (url or "").strip()
    if not raw:
        return raw
    parts = urlsplit(raw)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValidationError(
            f"{field_name} 必须使用 http 或 https",
            details={"got": parts.scheme, "allowed": sorted(_ALLOWED_SCHEMES)},
        )
    if "@" in (parts.netloc or ""):
        raise ValidationError(
            f"{field_name} 不能在 URL 里内嵌用户名/密码",
            details={"hint": "凭据请单独填写，不要放进地址"},
        )
    host = (parts.hostname or "").lower()
    if not host:
        raise ValidationError(f"{field_name} 缺少主机名", details={"got": raw})
    if parts.scheme.lower() == "http" and host not in _LOCAL_HOSTS:
        raise ValidationError(
            f"{field_name} 指向远程主机时必须使用 https",
            details={"host": host, "hint": "远程 http 会让 API Key 与提示词明文传输"},
        )
    if parts.fragment or parts.query:
        raise ValidationError(f"{field_name} 不应包含查询串或片段", details={"got": raw})
    return raw


def _validate_endpoint_fields(patch: dict[str, Any]) -> None:
    for key in ("base_url", "embedding_base_url"):
        value = patch.get(key)
        if isinstance(value, str) and value.strip():
            validate_endpoint(value, field_name=key)


#: The two write-only fields the UI calls "API Key".
SECRET_FIELDS = ("api_key", "embedding_api_key")


def validate_secret(value: str, *, field_name: str = "api_key") -> str:
    """Check a pasted API key. Returns it with the surrounding blanks removed.

    Two things go wrong when a key is pasted rather than typed, and both used to be found
    by the HTTP client instead of here:

    * **A trailing newline or space.** Copying from a browser or a chat window picks one
      up; it travels inside the ``Authorization`` header and the provider answers 401.
    * **A non-ASCII character.** Keys are ASCII, and ``httpx`` encodes header values as
      ASCII. A key holding one — a stray Chinese character from the sentence around it,
      which is exactly how one arrives if the user pastes the whole line rather than the
      key — failed inside the client with ``UnicodeEncodeError: 'ascii' codec can't encode
      character ...``. That message reached the user as the Agent page's only explanation
      and says nothing about a key being wrong.

    Trailing blanks are trimmed because that is unambiguous. A character in the middle is
    refused rather than guessed at: stripping a prefix like ``key为`` would be inventing a
    key the user did not type, and the message can just say which character and where.
    """
    raw = (value or "").strip()
    for index, char in enumerate(raw):
        if char.isascii() and not char.isspace():
            continue
        if not char.isascii():
            raise ValidationError(
                f"{field_name} 只能包含 ASCII 字符，第 {index + 1} 个字符是「{char}」。"
                f"请只粘贴 Key 本身",
                code="ai_config_secret_not_ascii",
                details={"field": field_name, "position": index + 1, "character": char},
            )
        raise ValidationError(
            f"{field_name} 不能包含空格或换行，第 {index + 1} 个字符是空白",
            code="ai_config_secret_whitespace",
            details={"field": field_name, "position": index + 1},
        )
    return raw


def _normalise_secret_fields(patch: dict[str, Any]) -> None:
    """Rewrite the secrets in ``patch`` in place, so the trimmed value is what gets stored."""
    for key in SECRET_FIELDS:
        value = patch.get(key)
        if isinstance(value, str) and value:
            patch[key] = validate_secret(value, field_name=key)


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



# --------------------------------------------------------------------------- #
# What state is this account's AI configuration in?
# --------------------------------------------------------------------------- #
# **Five states, and they are not the same thing.** The settings page used to have two —
# "configured" and not — and it derived them from `settings.is_offline_ai`, which is a
# property of the *process*: it reads `.env` when the call has no owner bound. A saved key
# therefore showed up as "离线模式：是（未配置 Key）" on the same screen that displayed
# the key's mask, because the two halves of the screen read different layers.
#
# The states, in the order a person moves through them:
#
#   offline               the effective provider *is* the offline engine. Nothing to
#                         configure, and nothing is broken.
#   not_configured        a cloud provider is selected and a required field is missing.
#   configured_untested   every required field is saved, and no check has been run since
#                         they were last changed.
#   connected             a real request to the endpoint succeeded.
#   failed                a real request was made and was refused. The reason is kept.
#
# `configured_untested` is the one that was missing, and its absence is why "saved" had to
# be displayed as either "connected" (a claim the program cannot support) or
# "未配置" (false).
CHECK_KEY = "last_check"

#: The fields a check is only valid for. Change any of them and the result is about a
#: configuration that is no longer in effect, so the state goes back to untested rather
#: than showing a green light for something nobody verified.
CHECK_FINGERPRINT = ("provider", "model", "base_url")


def _check_path(owner_id: Any) -> Path:
    return _path_for(owner_id)


def load_check(owner_id: Any) -> dict[str, Any]:
    """The last recorded connection check for this account, or ``{}``."""
    data = load(owner_id)
    check = data.get(CHECK_KEY)
    return check if isinstance(check, dict) else {}


def record_check(owner_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    """Store the outcome of a connection test next to the configuration it tested.

    Written through the same file and the same lock as `save`, because it is part of the
    same answer: "is this account's AI working, and on what evidence".
    """
    if _coerce_owner(owner_id) is None:
        raise ValidationError(
            "A connection check must be recorded per account",
            code="ai_config_scope_required",
        )
    path = _path_for(owner_id)
    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ok": bool(result.get("ok")),
        "provider": result.get("provider", ""),
        "model": result.get("model", ""),
        "base_url": result.get("base_url", ""),
        "latency_ms": int(result.get("latency_ms") or 0),
        "error_code": result.get("error_code", ""),
        "error_message": result.get("error_message", ""),
    }
    with _lock:
        current = _read_file(path)
        current[CHECK_KEY] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        with contextlib.suppress(OSError):
            path.chmod(0o600)
    _cache.pop(path, None)
    logger.info(
        "ai_connection_checked",
        extra={"owner": _coerce_owner(owner_id), "ok": entry["ok"],
               "provider": entry["provider"], "error_code": entry["error_code"]},
    )
    return entry


def _missing_required(cfg: ResolvedAIConfig) -> bool:
    """A field the chosen provider cannot work without.

    A model name is required by every provider, including the local one. A key is required
    by the two that are reached over the network — and only by those, so an Ollama install
    is not told it is missing something it never needed.
    """
    if not cfg.model.strip():
        return True
    return cfg.provider in ("deepseek", "openai_compatible") and not cfg.api_key_set


def ai_status(owner_id: Any = None) -> dict[str, Any]:
    """The configuration's state, with the evidence for it.

    Returns the state and the facts behind it; the interface chooses the words. Nothing
    here performs a network call — a status that fires a request every time a page is
    opened is a status that costs money to look at.
    """
    cfg = resolve(owner_id)
    check = load_check(owner_id)

    state: str
    if cfg.provider == "offline":
        state = "offline"
    elif _missing_required(cfg):
        state = "not_configured"
    elif check and all(check.get(field) == getattr(cfg, field) for field in CHECK_FINGERPRINT):
        state = "connected" if check.get("ok") else "failed"
    else:
        # A check exists but is about a different provider, model or endpoint. Reporting
        # it would be reporting on something that is no longer configured.
        state = "configured_untested"

    return {
        "state": state,
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "api_key_set": cfg.api_key_set,
        "api_key_hint": mask_api_key(cfg.api_key),
        "check": check,
        "stale_check": bool(check) and state in ("configured_untested",),
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


def resolve(owner_id: Any = None, *, root: Path | None = None) -> ResolvedAIConfig:
    """Merge this account's overrides over the installation's over the environment.

    Order: **account file → installation file → environment → defaults**. The account
    layer is the one that makes a person's choice stop applying to everybody else; the
    installation layer is kept below it so an existing install's settings survive the
    upgrade instead of vanishing.

    With no owner — a background worker, a CLI, or a request that is not authenticated —
    only the installation layer applies. That is the correct reading of "whose setting is
    this?" when there is no user: nobody's.

    ``root`` resolves against an explicit runtime root. Only the live integration tests
    pass it; see ``_path_for`` for why they are the exception.
    """
    owner = _coerce_owner(owner_id) or current_owner()
    account = load(owner, root=root) if owner else {}
    install = load(None, root=root)
    runtime = {**install, **account}
    origin = {key: ("account" if key in account else "install") for key in runtime}
    sources: dict[str, str] = {}

    def pick(field_name: str, *env_values: tuple[Any, str], default: Any = None) -> Any:
        """First non-empty value wins: account → install → env → default.

        NOTE: the fallback must return ``default`` itself, not ``str(None)``.
        Converting a missing value to the *string* ``"None"`` would make an empty
        API key look configured — which is exactly the kind of silent failure
        that makes a product claim a cloud model is answering when it is not.
        """
        candidates = ((runtime.get(field_name), origin.get(field_name, "runtime")), *env_values)
        value, origin_name = _first(*candidates)
        if value is None:
            sources[field_name] = "default"
            return default
        sources[field_name] = origin_name
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

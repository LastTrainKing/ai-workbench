"""Application configuration.

All runtime configuration is sourced from environment variables (12-factor style)
so that switching AI providers, databases or vector stores never requires a code
change.  See ``.env.example`` at the repository root for the full list.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
# backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> <root>
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


def _resolve_runtime_root() -> Path:
    """Where the database, vectors, uploads and logs live.

    Resolved once, here, so the launcher and the backend cannot disagree about it.

    An **installed** copy keeps its data outside the program directory. That is not a
    preference, it is what makes uninstalling safe: if the user's database sits inside
    the install directory, then removing the program means either deleting the data or
    leaving a directory behind - and the first attempt at this moved the data aside with
    a rename that failed, taking the database with it.

    With the data outside, uninstalling is two independent decisions: remove the program
    (always), remove the data (asked, defaults to no). Nothing has to be moved.

    Resolution order:

    1. ``AI_WORKBENCH_DATA_DIR`` - explicit, wins over everything
    2. ``<root>/data-dir.txt`` - written by the installer, one line, the path
    3. ``<root>/.runtime`` - a development checkout

    The marker file rather than an environment variable because it is visible: a user
    (or a support conversation) can open it and see where their data is.
    """
    explicit = os.environ.get("AI_WORKBENCH_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()

    marker = PROJECT_ROOT / "data-dir.txt"
    if marker.is_file():
        try:
            text = marker.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text:
            return Path(text).expanduser()

    return PROJECT_ROOT / ".runtime"


RUNTIME_ROOT = _resolve_runtime_root()


def _read_version() -> str:
    """The product version, from the repository's single `VERSION` file.

    The backend used to hard-code ``1.0.0`` while the installer said ``2.1.0`` and the
    shell ``2.0.0`` — three numbers for one product, visible to the same user in the same
    session. `VERSION` at the repository root is the source; `scripts/sync_version.py`
    writes it into the files that cannot read it (JSON, NSIS) and the test suite asserts
    they all agree.

    Falls back to a visibly non-release value rather than to a plausible one: an install
    that cannot find its own version file should look wrong, not look like 1.0.0.
    """
    try:
        text = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    return text or "0.0.0-unknown"


def runtime_root() -> Path:
    """The one answer to "where does this copy keep its data?".

    Every module that used to compute `PROJECT_ROOT / ".runtime"` for itself now asks
    here. Five copies of one decision is five chances for an installed copy to write
    half its state into the program directory and half outside it - which is exactly
    the failure this exists to prevent.
    """
    return RUNTIME_ROOT


#: The value that ships in `.env.example` and in the repository. A token signed with a key
#: anyone can read is not signed, so `prod` refuses to start while this is in place.
DEFAULT_SECRET_KEY = "change-me-in-dotenv-please-32-chars-min"

#: Substrings that mark a value as an example rather than a secret. Checked
#: case-insensitively against the whole value.
#:
#: The old check compared against `DEFAULT_SECRET_KEY` and the length, and nothing else —
#: so `change-me-to-a-random-32-plus-character-string` (the value in `.env.example`) is
#: 47 characters, is not the built-in default, and **passed production validation**. A
#: placeholder that satisfies the length rule is still a placeholder.
_PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "change_me",
    "replace-me",
    "replaceme",
    "replace_me",
    "placeholder",
    "your-secret",
    "your_secret",
    "your-key",
    "example",
    "sample",
    "dummy",
    "insecure",
    "secret-key-here",
    "xxxxxxxx",
    # Keyboard/sequence runs. A real random key contains one of these with negligible
    # probability, so they cost nothing and catch a person typing "abcdefgh…".
    "abcdefgh",
    "12345678",
    "qwerty",
    "asdfgh",
)

#: A real 32+ character key is drawn from a large alphabet; a pattern like
#: `"abcdefghijklmnopqrstuvwxyz012345"` is not. Below this many distinct characters the
#: value is treated as a pattern rather than a key. Deliberately low: a hex key has only
#: sixteen possible characters, so a threshold that excluded it would reject real keys.
_MIN_DISTINCT_SECRET_CHARS = 10


def _secret_problems(value: str) -> list[str]:
    """Why this value is not acceptable as a production `SECRET_KEY` (empty = fine)."""
    key = value.strip()
    problems: list[str] = []
    if not key:
        return ["SECRET_KEY is empty"]
    if key == DEFAULT_SECRET_KEY:
        problems.append("SECRET_KEY is still the built-in default")
    if len(key) < 32:
        problems.append(f"SECRET_KEY is {len(key)} characters; 32 is the minimum")
    lowered = key.lower()
    markers = sorted({m for m in _PLACEHOLDER_MARKERS if m in lowered})
    if markers:
        problems.append(
            "SECRET_KEY contains placeholder text (" + ", ".join(markers) + ")"
        )
    if len(set(key)) < _MIN_DISTINCT_SECRET_CHARS:
        problems.append(
            f"SECRET_KEY uses only {len(set(key))} distinct characters; "
            "it looks like a pattern rather than a random key"
        )
    return problems


class Settings(BaseSettings):
    """Typed, validated application settings."""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- app ---
    app_name: str = "AI Workbench"
    #: From the repository `VERSION` file — one source for the whole product.
    app_version: str = _read_version()
    app_env: Literal["dev", "test", "prod"] = "dev"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # ------------------------------------------------------------- desktop --
    # The Tauri desktop shell loads the UI from this very server, so the window
    # origin is the API origin: no CORS, no second port, no dev server, and the
    # UI never runs inside Edge or Chrome.
    serve_frontend: bool = True
    frontend_dist: str = ""  # empty => <repo>/frontend/dist

    # --------------------------------------------------------- computer agent --
    # Everything the agent may touch on this machine. These are the defaults for
    # a *supervised* agent: it works inside the workspace without asking, and has
    # to be granted anything beyond that. They are overridable at runtime from
    # Settings -> Agent Permissions.
    agent_mode: str = "supervised"  # assist | supervised | autonomous
    agent_permission_level: int = 1  # 0 observe, 1 workspace, 2 system, 3 full
    #: Writable roots. The agent workspace is always included.
    agent_allowed_paths: str = ""
    #: Always refused, even if a broader allowed path would cover them. Secrets
    #: and system directories live here.
    agent_denied_paths: str = ""
    #: Hosts the agent may contact without asking.
    #:
    #: `localhost` and `127.0.0.1` used to be in this list. They are gone, and they must
    #: not come back. The app runs its own vector store on `127.0.0.1:6333` with no
    #: authentication, and the user may be running anything else locally; an allow-list
    #: entry for the loopback address meant the agent could reach all of it **without a
    #: confirmation prompt**. `check_domain` also refuses loopback and private addresses
    #: outright now, whatever any list says, so an entry here would not work anyway.
    agent_allowed_domains: str = (
        "github.com,raw.githubusercontent.com,pypi.org,files.pythonhosted.org,"
        "registry.npmjs.org,npmjs.com,api.deepseek.com,docs.python.org,"
        "developer.mozilla.org,stackoverflow.com"
    )
    #: Hosts the agent may never contact, whatever the allow-list says.
    #:
    #: Non-empty by default, unlike before: an empty blocklist means the blocklist step
    #: of `check_domain` never fires, so "always refused" refused nothing.
    agent_blocked_domains: str = (
        "localhost,localhost.localdomain,ip6-localhost,metadata.google.internal"
    )

    #: Where the agent's own workspace lives. Empty means the default:
    #: ``<project>/.runtime/workspace``.
    #:
    #: It used to be ``%USERPROFILE%\Desktop\AI Workbench Workspace`` — a personal
    #: AI workbench that opened its scratch area at the root of the user's desktop,
    #: which is the product emptying its pockets onto the one screen the user looks
    #: at all day. It is configurable because a user may legitimately want it
    #: somewhere else, but the default has to be a place the product owns.
    agent_workspace: str = ""

    #: Resource ceilings. A runaway agent must hit a wall, not the user's patience.
    agent_max_tool_calls: int = 100
    agent_max_runtime_seconds: int = 1800
    agent_max_retries: int = 3
    agent_max_output_bytes: int = 200_000
    agent_max_files_modified: int = 50
    agent_max_download_bytes: int = 200_000_000

    #: Retention. **Off by default, and nothing in the product calls it automatically.**
    #:
    #: Snapshots and audit rows only ever grew — 1,269 snapshots and 3,021 audit rows had
    #: accumulated with no rule saying which still mattered. The rules are here so they can
    #: be read and changed; they are inert until someone builds a plan and applies it, and
    #: the first release ships the preview rather than the deletion.
    #:
    #: A snapshot has to exceed **both** the count and the age limit to become a candidate,
    #: unless `retention_require_both_limits` is turned off. Pinned snapshots, snapshots
    #: belonging to a run that is still going, and the newest snapshot for each run are
    #: never candidates — see `app.ai.agent.retention`.
    retention_enabled: bool = False
    retention_snapshot_max_count: int = 200
    retention_snapshot_max_age_days: int = 30
    retention_audit_max_count: int = 50_000
    retention_audit_max_age_days: int = 180
    retention_require_both_limits: bool = True

    #: How long a high-risk confirmation stays open before the run gives up.
    agent_confirmation_timeout_seconds: int = 180

    @property
    def agent_allowed_path_list(self) -> list[str]:
        return [p.strip() for p in self.agent_allowed_paths.split(",") if p.strip()]

    @property
    def agent_denied_path_list(self) -> list[str]:
        return [p.strip() for p in self.agent_denied_paths.split(",") if p.strip()]

    @property
    def agent_allowed_domain_list(self) -> list[str]:
        return [d.strip().lower() for d in self.agent_allowed_domains.split(",") if d.strip()]

    @property
    def agent_blocked_domain_list(self) -> list[str]:
        return [d.strip().lower() for d in self.agent_blocked_domains.split(",") if d.strip()]

    # ------------------------------------------------------------- security --
    secret_key: str = Field(default=DEFAULT_SECRET_KEY)
    access_token_expire_minutes: int = 60 * 24
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ----------------------------------------------------------- database ---
    database_url: str = (
        "postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/aiworkbench"
    )
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # -------------------------------------------------------------- redis ---
    redis_url: str = "redis://127.0.0.1:6379/0"
    cache_enabled: bool = True
    cache_ttl_search_seconds: int = 180
    cache_ttl_prompt_seconds: int = 600
    cache_ttl_stats_seconds: int = 30

    # ------------------------------------------------------------- qdrant ---
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "ai_workbench_chunks"
    # 0 means "detect from the first embedding vector and create on demand"
    qdrant_vector_size: int = 0
    qdrant_distance: Literal["Cosine", "Dot", "Euclid"] = "Cosine"

    # ----------------------------------------------------------- AI: chat ---
    ai_provider: Literal["deepseek", "openai_compatible", "ollama", "offline"] = "deepseek"
    ai_base_url: str = "https://api.deepseek.com"
    ai_api_key: str = ""
    ai_model: str = "deepseek-flash"
    ai_temperature: float = 0.2
    ai_max_tokens: int = 8192
    ai_timeout_seconds: float = 90.0
    ai_max_retries: int = 3
    ai_backoff_base_seconds: float = 1.0

    # -------------------------------------------------------- AI: DeepSeek ---
    # Kept in their own namespace so a DeepSeek key can be supplied without
    # touching the generic OpenAI-compatible variables.
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"

    # ------------------------------------------------------ AI: embedding ---
    embedding_provider: Literal["openai_compatible", "ollama", "offline"] = (
        "openai_compatible"
    )
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_batch_size: int = 16
    embedding_timeout_seconds: float = 60.0

    # --------------------------------------------------------------- RAG ----
    chunk_size: int = 900
    chunk_overlap: int = 150
    retrieval_top_k: int = 8
    retrieval_candidate_k: int = 40
    hybrid_alpha: float = 0.6  # weight of dense score in hybrid fusion
    rerank_enabled: bool = True

    # ------------------------------------------------------------ storage ---
    storage_dir: str = str(RUNTIME_ROOT / "storage")
    max_upload_mb: int = 25
    allowed_extensions: str = ".md,.markdown,.txt,.pdf"

    # ------------------------------------------------------------- worker ---
    worker_enabled: bool = True
    worker_poll_interval: float = 1.0
    worker_concurrency: int = 2
    worker_max_attempts: int = 3
    worker_stale_job_seconds: int = 900
    worker_job_timeout_seconds: int = 900

    # --------------------------------------------------------------- misc ---
    log_level: str = "INFO"
    log_json: bool = False
    metrics_enabled: bool = True

    # ---------------------------------------------------------- validators --
    @field_validator("secret_key")
    @classmethod
    def _secret_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("SECRET_KEY must not be empty")
        return v

    @model_validator(mode="after")
    def _production_is_not_a_development_default(self) -> Settings:
        """A shipped build must not run on the development defaults.

        Three things were true out of the box, and all three are fine while building and
        wrong the moment someone else runs it:

            SECRET_KEY          the placeholder shipped in the repository
            DEBUG               True
            /docs, /redoc, /openapi.json    always served

        A JWT signed with a key that is in the repository is not signed. So in `prod` this
        **refuses to start** rather than warning: a warning on stderr is not read by
        anyone, and the failure it predicts is silent. `dev` and `test` are unaffected.
        """
        if self.app_env != "prod":
            return self

        problems: list[str] = _secret_problems(self.secret_key)
        if self.debug:
            problems.append("DEBUG is on")

        if problems:
            raise ValueError(
                "refusing to start with APP_ENV=prod: " + "; ".join(problems)
                + ". Set them in the environment or in .env."
            )
        return self

    @model_validator(mode="after")
    def _qdrant_key_from_launcher_file(self) -> Settings:
        """Pick up the Qdrant API key the launcher generated, if the env has none.

        `scripts/infra-start.ps1` starts Qdrant with an API key and writes it to
        ``<runtime>/qdrant-api-key.txt``. The backend is often started by a *different*
        process, so it cannot inherit that environment variable — reading the same file is
        what makes the two agree. Without this the launcher would enforce a key that the
        client never sends, and every vector call would fail with 401.

        An explicit ``QDRANT_API_KEY`` always wins, so a deployment that sets it properly
        is unaffected.
        """
        if self.qdrant_api_key.strip():
            return self
        key_file = RUNTIME_ROOT / "qdrant-api-key.txt"
        if not key_file.is_file():
            return self
        try:
            value = key_file.read_text(encoding="utf-8").strip()
        except OSError:
            return self
        if value:
            self.qdrant_api_key = value
        return self

    @property
    def api_docs_enabled(self) -> bool:
        """`/docs`, `/redoc` and `/openapi.json` describe every route and every schema.

        Useful while building, a map of the attack surface in a shipped build. Off in
        `prod`; on everywhere else.
        """
        return self.app_env != "prod"

    # ------------------------------------------------------------ helpers ---
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_extension_list(self) -> list[str]:
        return [e.strip().lower() for e in self.allowed_extensions.split(",") if e.strip()]

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def upload_path(self) -> Path:
        return self.storage_path / "uploads"

    @property
    def is_offline_ai(self) -> bool:
        """True when no real model backend is configured.

        Delegates to the runtime resolver so the answer reflects what the
        Settings UI currently has selected, not just what ``.env`` said at boot.
        """
        from app.ai.runtime_config import resolve

        try:
            resolved = resolve()
        except Exception:
            return True
        if resolved.provider == "offline":
            return True
        if resolved.provider in ("deepseek", "openai_compatible"):
            return not resolved.api_key_set
        return False

    def resolved_embedding_base_url(self) -> str:
        return self.embedding_base_url or self.ai_base_url

    def resolved_embedding_api_key(self) -> str:
        return self.embedding_api_key or self.ai_api_key

    def ensure_dirs(self) -> None:
        for d in (self.storage_path, self.upload_path, self.storage_path / "tmp"):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


settings = get_settings()

# Convenience for scripts that only need the root
__all__ = ["BACKEND_DIR", "PROJECT_ROOT", "Settings", "get_settings", "settings"]

if os.environ.get("AI_WORKBENCH_PRINT_CONFIG") == "1":  # pragma: no cover
    print(settings.model_dump_json(indent=2))

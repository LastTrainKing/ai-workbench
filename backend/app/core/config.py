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

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
# backend/app/core/config.py -> backend/app/core -> backend/app -> backend -> <root>
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent


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
    app_version: str = "1.0.0"
    app_env: Literal["dev", "test", "prod"] = "dev"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # ------------------------------------------------------------- security --
    secret_key: str = Field(default="change-me-in-dotenv-please-32-chars-min")
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
    ai_provider: Literal["openai_compatible", "ollama", "offline"] = "openai_compatible"
    ai_base_url: str = "https://api.deepseek.com/v1"
    ai_api_key: str = ""
    ai_model: str = "deepseek-chat"
    ai_temperature: float = 0.2
    ai_max_tokens: int = 2048
    ai_timeout_seconds: float = 90.0
    ai_max_retries: int = 3
    ai_backoff_base_seconds: float = 1.0

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
    storage_dir: str = str(PROJECT_ROOT / ".runtime" / "storage")
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
        """True when no real model backend is configured."""
        return self.ai_provider == "offline" or (
            self.ai_provider == "openai_compatible" and not self.ai_api_key.strip()
        )

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

"""Health, dependency and metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.ai.providers.factory import check_provider_health
from app.api.deps import DbSession
from app.core.cache import cache_stats
from app.core.cache import ping as redis_ping
from app.core.config import settings
from app.core.response import ok
from app.infrastructure.database import healthcheck as db_health
from app.infrastructure.vector_store import get_vector_store
from app.services.ai_services import AIRunService

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict:
    return ok(
        {
            "status": "ok",
            "app": settings.app_name,
            "version": settings.app_version,
            "env": settings.app_env,
        }
    )


@router.get("/health/deps", summary="Readiness probe for every dependency")
async def dependencies() -> dict:
    db_ok, db_detail = await db_health()
    redis_ok = await redis_ping()
    qdrant_ok, qdrant_detail = await get_vector_store().ping()
    providers = await check_provider_health()

    degraded = providers.get("llm", {}).get("degraded") if isinstance(providers.get("llm"), dict) else False
    status = "ok" if (db_ok and redis_ok and qdrant_ok) else "degraded"

    return ok(
        {
            "status": status,
            "postgres": {"ok": db_ok, "detail": db_detail[:120]},
            "redis": {"ok": redis_ok},
            "qdrant": {"ok": qdrant_ok, "detail": qdrant_detail},
            "ai": providers,
            "ai_degraded": bool(degraded),
            "config": {
                "ai_provider": settings.ai_provider,
                "embedding_provider": settings.embedding_provider,
                "chunk_size": settings.chunk_size,
                "chunk_overlap": settings.chunk_overlap,
                "hybrid_alpha": settings.hybrid_alpha,
                "rerank_enabled": settings.rerank_enabled,
                "cache_enabled": settings.cache_enabled,
                "worker_enabled": settings.worker_enabled,
            },
        }
    )


@router.get("/metrics", summary="Lightweight application metrics")
async def metrics(session: DbSession) -> dict:
    if not settings.metrics_enabled:
        return ok({"enabled": False})
    return ok(
        {
            "enabled": True,
            "ai_24h": await AIRunService(session).health(),
            "cache": cache_stats(),
            "uptime_source": "process",
        }
    )

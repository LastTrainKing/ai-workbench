"""Health, dependency and metrics endpoints."""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, HTTPException, Request

from app.ai.providers.factory import check_provider_health
from app.api.deps import CurrentUser, DbSession
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
    """Alive, and nothing else.

    It used to also report the version and the environment. Neither is a secret, but a
    probe that anyone can reach should answer the one question it is asked — "is the
    process up?" — and the rest belongs behind the same door as everything else.
    """
    return ok({"status": "ok", "app": settings.app_name})


def _is_local(request: Request) -> bool:
    """Was this request made from this machine?

    `/health/deps` reports which dependencies are up and, through the provider block,
    which model is configured and why it is degraded. That is operational detail about
    the user's machine, useful to the desktop shell and to nobody else.
    """
    client = request.client
    if client is None:
        return False
    host = (client.host or "").strip().lower()
    if host in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@router.get("/health/deps", summary="Readiness probe for every dependency (this machine only)")
async def dependencies(request: Request) -> dict:
    if not _is_local(request):
        raise HTTPException(status_code=404, detail="Not Found")

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


@router.get("/metrics", summary="Lightweight application metrics (authenticated)")
async def metrics(session: DbSession, user: CurrentUser) -> dict:
    # Counts of documents, chunks, memories, runs and tool calls for the caller. It was
    # open, which meant anyone who could reach the port could read how much the user has
    # and how much they have been using it. Not secret; not theirs either.
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

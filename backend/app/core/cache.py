"""Redis-backed cache with an explicit, documented degradation strategy.

What we cache, why, and for how long
------------------------------------
====================  ==========================  ===============================
Cache family          Why                         TTL / invalidation
====================  ==========================  ===============================
``search:*``          Hybrid search is CPU-heavy  ``CACHE_TTL_SEARCH_SECONDS``;
                      (embedding + Qdrant +       invalidated by namespace bump
                      keyword scoring).           whenever a document changes.
``prompt:*``          Prompt templates change     ``CACHE_TTL_PROMPT_SECONDS``;
                      rarely but are read on      invalidated explicitly on
                      every AI call.              template create/update.
``stats:*``           Dashboard aggregates run    ``CACHE_TTL_STATS_SECONDS``;
                      several GROUP BY queries.   short TTL only, no explicit
                                                  invalidation needed.
``embed:*``           Embeddings are               24h, content-hash keyed,
                      deterministic for a given   so identical text is never
                      (model, text) pair.         re-embedded (cost saving).
``lock:*``            Prevent duplicate workers    30s, best-effort release.
                      from processing the same
                      job.
====================  ==========================  ===============================

If Redis is unavailable the application keeps working (cache miss) but logs a
warning and increments a counter — a cache outage must never break the product.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: aioredis.Redis | None = None
_degraded_logged = False
metrics: dict[str, int] = {"hit": 0, "miss": 0, "set": 0, "error": 0, "bypass": 0}


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
            health_check_interval=30,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except RedisError as exc:
            logger.warning("redis_close_failed", extra={"error": str(exc)})
        finally:
            _client = None


async def ping() -> bool:
    try:
        return bool(await get_redis().ping())
    except RedisError as exc:
        logger.warning("redis_ping_failed", extra={"error": str(exc)})
        return False


def _note_degraded(exc: BaseException) -> None:
    global _degraded_logged
    metrics["error"] += 1
    if not _degraded_logged:
        logger.warning(
            "redis_unavailable_degrading_to_no_cache",
            extra={"error": f"{type(exc).__name__}: {exc}"},
        )
        _degraded_logged = True


def make_key(namespace: str, *parts: Any) -> str:
    raw = "|".join(json.dumps(p, sort_keys=True, default=str) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"awb:{namespace}:{digest}"


async def cache_get(namespace: str, key: str) -> Any | None:
    if not settings.cache_enabled:
        metrics["bypass"] += 1
        return None
    try:
        raw = await get_redis().get(key)
    except RedisError as exc:
        _note_degraded(exc)
        return None
    if raw is None:
        metrics["miss"] += 1
        return None
    metrics["hit"] += 1
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("cache_corrupt_entry", extra={"key": key})
        try:
            await get_redis().delete(key)
        except RedisError as exc:
            _note_degraded(exc)
        return None


async def cache_set(namespace: str, key: str, value: Any, ttl: int) -> bool:
    if not settings.cache_enabled:
        return False
    try:
        await get_redis().set(key, json.dumps(value, default=str), ex=ttl)
        metrics["set"] += 1
        return True
    except (RedisError, TypeError) as exc:
        _note_degraded(exc)
        return False


async def cache_delete(*keys: str) -> int:
    if not keys:
        return 0
    try:
        return int(await get_redis().delete(*keys))
    except RedisError as exc:
        _note_degraded(exc)
        return 0


async def cache_delete_prefix(prefix: str) -> int:
    """Delete all keys under a prefix using SCAN (never KEYS on production data)."""
    removed = 0
    try:
        client = get_redis()
        async for key in client.scan_iter(match=f"{prefix}*", count=200):
            removed += int(await client.delete(key))
    except RedisError as exc:
        _note_degraded(exc)
    return removed


async def cache_incr(namespace: str, key: str, ttl: int | None = None) -> int:
    try:
        client = get_redis()
        pipe = client.pipeline()
        pipe.incr(key)
        if ttl:
            pipe.expire(key, ttl)
        result = await pipe.execute()
        return int(result[0])
    except RedisError as exc:
        _note_degraded(exc)
        return 0


async def acquire_lock(name: str, ttl: int = 30) -> bool:
    """Best-effort distributed lock; returns False when the lock is held."""
    try:
        return bool(await get_redis().set(f"awb:lock:{name}", "1", nx=True, ex=ttl))
    except RedisError as exc:
        _note_degraded(exc)
        # Without Redis we prefer progress over perfect mutual exclusion.
        return True


async def release_lock(name: str) -> None:
    try:
        await get_redis().delete(f"awb:lock:{name}")
    except RedisError as exc:
        _note_degraded(exc)


async def namespace_version(ns: str) -> int:
    """Read the current version stamp of a namespace (default 0)."""
    try:
        raw = await get_redis().get(f"awb:nsver:{ns}")
        return int(raw) if raw else 0
    except (RedisError, ValueError) as exc:
        _note_degraded(exc)
        return 0


async def bump_namespace(ns: str) -> int:
    """Invalidate a whole cache family at once (cheap, race-free)."""
    try:
        return int(await get_redis().incr(f"awb:nsver:{ns}"))
    except RedisError as exc:
        _note_degraded(exc)
        return 0


def cache_stats() -> dict[str, int]:
    total = metrics["hit"] + metrics["miss"]
    return {
        **metrics,
        "hit_rate": round(metrics["hit"] / total, 4) if total else 0.0,
    }

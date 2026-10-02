"""Bounded retry with exponential backoff + jitter.

Deliberately hand-rolled instead of pulling in ``tenacity`` so that the retry
policy is explicit, testable and identical across HTTP/AI/worker call sites.

Rules enforced here (see docs/ai.md):
* only errors explicitly marked ``retryable`` are retried;
* the attempt count is hard-capped (no infinite loops);
* total wall-clock time is bounded by ``max_elapsed``;
* every retry is logged with the attempt number and the reason.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import TypeVar

from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 20.0
    multiplier: float = 2.0
    jitter: float = 0.25
    max_elapsed: float = 180.0
    retry_on: tuple[type[BaseException], ...] = (Exception,)
    no_retry_on: tuple[type[BaseException], ...] = ()
    _delays: list[float] = field(default_factory=list, init=False, repr=False)

    def delay_for(self, attempt: int) -> float:
        """attempt is 1-based (the delay *after* attempt N failed)."""
        raw = self.base_delay * (self.multiplier ** (attempt - 1))
        raw = min(raw, self.max_delay)
        if self.jitter:
            raw = raw * (1 - self.jitter) + raw * self.jitter * 2 * random.random()
        return round(max(0.0, raw), 3)

    def should_retry(self, exc: BaseException) -> bool:
        if self.no_retry_on and isinstance(exc, self.no_retry_on):
            return False
        if getattr(exc, "retryable", None) is False:
            return False
        return isinstance(exc, self.retry_on)


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    operation: str = "operation",
    on_retry: Callable[[int, BaseException, float], None] | None = None,
) -> T:
    """Execute ``fn`` with bounded retries. Re-raises the final error."""
    policy = policy or RetryPolicy()
    started = time.monotonic()
    last_exc: BaseException | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await fn()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            last_exc = exc
            elapsed = time.monotonic() - started
            can_retry = (
                attempt < policy.max_attempts
                and policy.should_retry(exc)
                and elapsed < policy.max_elapsed
            )
            if not can_retry:
                logger.warning(
                    "retry_giving_up",
                    extra={
                        "operation": operation,
                        "attempt": attempt,
                        "elapsed_s": round(elapsed, 3),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                raise
            delay = policy.delay_for(attempt)
            if elapsed + delay > policy.max_elapsed:
                raise
            logger.info(
                "retry_scheduled",
                extra={
                    "operation": operation,
                    "attempt": attempt,
                    "delay_s": delay,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            if on_retry:
                on_retry(attempt, exc, delay)
            await asyncio.sleep(delay)

    assert last_exc is not None  # pragma: no cover - loop always returns or raises
    raise last_exc


def chunked(items: Iterable[T], size: int) -> list[list[T]]:
    """Split an iterable into fixed-size batches."""
    if size <= 0:
        raise ValueError("size must be > 0")
    batch: list[T] = []
    out: list[list[T]] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            out.append(batch)
            batch = []
    if batch:
        out.append(batch)
    return out

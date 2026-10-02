"""Unit tests for the bounded retry helper."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.core.retry import RetryPolicy, chunked, retry_async


class Transient(Exception):
    retryable = True


class Permanent(Exception):
    retryable = False


@pytest.mark.asyncio
async def test_succeeds_without_retry() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert await retry_async(fn, operation="test") == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_retries_then_succeeds() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise Transient("boom")
        return "recovered"

    policy = RetryPolicy(max_attempts=5, base_delay=0.01, jitter=0.0)
    assert await retry_async(fn, policy=policy, operation="test") == "recovered"
    assert calls == 3


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        raise Transient("always broken")

    policy = RetryPolicy(max_attempts=3, base_delay=0.01, jitter=0.0)
    with pytest.raises(Transient):
        await retry_async(fn, policy=policy, operation="test")
    assert calls == 3


@pytest.mark.asyncio
async def test_non_retryable_error_fails_fast() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        raise Permanent("bad credentials")

    policy = RetryPolicy(max_attempts=5, base_delay=0.01)
    with pytest.raises(Permanent):
        await retry_async(fn, policy=policy, operation="test")
    assert calls == 1, "a non-retryable error must not be retried"


@pytest.mark.asyncio
async def test_backoff_is_exponential_and_capped() -> None:
    policy = RetryPolicy(base_delay=1.0, multiplier=2.0, max_delay=10.0, jitter=0.0)
    assert policy.delay_for(1) == 1.0
    assert policy.delay_for(2) == 2.0
    assert policy.delay_for(3) == 4.0
    assert policy.delay_for(4) == 8.0
    assert policy.delay_for(5) == 10.0  # capped


@pytest.mark.asyncio
async def test_max_elapsed_bounds_total_time() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        raise Transient("nope")

    policy = RetryPolicy(max_attempts=100, base_delay=0.05, max_elapsed=0.2, jitter=0.0)
    started = time.perf_counter()
    with pytest.raises(Transient):
        await retry_async(fn, policy=policy, operation="test")
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0
    assert calls < 100, "the elapsed-time budget must stop the loop early"


@pytest.mark.asyncio
async def test_on_retry_callback_receives_attempt_number() -> None:
    seen: list[int] = []

    async def fn() -> str:
        if len(seen) < 2:
            raise Transient("again")
        return "done"

    policy = RetryPolicy(max_attempts=4, base_delay=0.01, jitter=0.0)
    await retry_async(fn, policy=policy, operation="test", on_retry=lambda n, e, d: seen.append(n))
    assert seen == [1, 2]


@pytest.mark.asyncio
async def test_cancellation_is_not_retried() -> None:
    async def fn() -> str:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await retry_async(fn, operation="test")


def test_chunked_splits_evenly() -> None:
    assert chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_chunked_handles_empty_and_invalid() -> None:
    assert chunked([], 3) == []
    with pytest.raises(ValueError):
        chunked([1], 0)

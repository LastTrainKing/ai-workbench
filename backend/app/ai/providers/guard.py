"""A hard block on real model calls, at the provider boundary.

## Why a counter is not enough

`scripts/model_call_count.py` counts rows in `ai_runs`. That is a good second piece of
evidence and a bad primary one, for two reasons:

* it is a **measurement taken after the fact**. A call that was made and failed before
  writing its row is invisible to it;
* it only covers calls that go through the code that writes those rows. A script that
  imports `openai` or `httpx` directly and posts to the API never touches the table.

The round that forbids model calls needs a mechanism that **prevents** rather than
**counts**. This module is it: every provider that the factory hands out is wrapped, and
the wrapper raises before any transport is touched.

## How it is armed

    TEST_NO_LLM=1        in the environment or .env
    running under pytest  detected automatically, so a test cannot forget

When armed, `chat()` raises `ModelCallBlocked`, the attempt is counted, and the count is
exposed for the report. Nothing is swallowed: a caller that catches the exception and
carries on is a caller that has to say so in its own code.

## What it deliberately does not do

It does not fake a response. Returning a canned answer would make a blocked call look
like a successful one, which is the exact failure this project keeps writing comments
about. Blocked means blocked.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: Every attempt that was refused, with the provider and model it was aimed at.
BLOCKED_ATTEMPTS: list[dict[str, str]] = []


class ModelCallBlocked(RuntimeError):
    """A real model call was attempted while calls are forbidden.

    Raised instead of performing the request. Deliberately not caught anywhere in the
    provider layer: a caller that wants to continue without a model has to say so, and
    the honest way to say so is to handle this exception by name.
    """

    def __init__(self, provider: str, model: str, reason: str) -> None:
        super().__init__(
            f"MODEL_CALL_BLOCKED: {provider}/{model} — {reason}. "
            "This round forbids real model calls; nothing was sent."
        )
        self.provider = provider
        self.model = model
        self.reason = reason


def _under_pytest() -> bool:
    return "pytest" in sys.modules or bool(os.environ.get("PYTEST_CURRENT_TEST"))


def is_armed() -> bool:
    """Are real model calls forbidden right now?"""
    flag = os.environ.get("TEST_NO_LLM", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    # A test that forgets the flag still gets the block. Forgetting is the normal case;
    # requiring the flag would make the guard opt-in, and an opt-in guard guards nothing.
    return _under_pytest()


def block_reason() -> str:
    flag = os.environ.get("TEST_NO_LLM", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return "TEST_NO_LLM is set"
    return "running under pytest"


def blocked_count() -> int:
    return len(BLOCKED_ATTEMPTS)


class BlockedProvider:
    """Wraps a real provider and refuses to reach it.

    A plain wrapper rather than a subclass of `LLMProvider`: it has to be able to stand in
    front of any provider, including ones that do not subclass it, and subclassing would
    make it inherit `chat`'s signature rather than enforce it.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.name = getattr(inner, "name", "blocked")
        self.model = getattr(inner, "model", "")
        self.timeout = getattr(inner, "timeout", 0)
        self.max_retries = getattr(inner, "max_retries", 0)

    @property
    def supports_tool_calling(self) -> bool:
        return bool(getattr(self._inner, "supports_tool_calling", False))

    def _refuse(self) -> None:
        attempt = {"provider": str(self.name), "model": str(self.model)}
        BLOCKED_ATTEMPTS.append(attempt)
        logger.warning("MODEL_CALL_BLOCKED", extra={**attempt, "reason": block_reason()})
        raise ModelCallBlocked(str(self.name), str(self.model), block_reason())

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        self._refuse()

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if close is not None:
            await close()

    def __getattr__(self, item: str) -> Any:
        # Anything else is a read-only attribute or a method that does not perform a
        # call. Delegating keeps the wrapper transparent for the health checks, which
        # must still be able to report *why* the provider is unavailable.
        return getattr(self._inner, item)


def guard(provider: Any, *, kind: str = "") -> Any:
    """Wrap `provider` when calls are forbidden, otherwise return it unchanged.

    **The offline engine is not wrapped, and that is deliberate.** It performs no network
    call at all — it answers from the request text with a deterministic local algorithm —
    so it is not a model call and blocking it would be blocking arithmetic. Blocking it
    also broke three integration tests that legitimately exercise the whole agent loop
    against the offline engine, which is the only way to test that loop without a key.

    What must be blocked is anything that could **leave the machine**: DeepSeek, an
    OpenAI-compatible endpoint, Ollama. Those are the ones that cost money and that this
    round forbids.
    """
    if provider is None:
        return provider
    if not is_armed():
        return provider
    if kind == "offline":
        return provider
    if isinstance(provider, BlockedProvider):
        return provider
    return BlockedProvider(provider)

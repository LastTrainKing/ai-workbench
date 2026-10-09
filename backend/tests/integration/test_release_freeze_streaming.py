"""Streaming: the transport, the terminator, and the frames that break naive parsers.

These are transport tests, so they build the provider directly against the stub rather than
going through the database — no account, no config file, no cost. The stub is a real HTTP
server on loopback, so `httpx` really opens a socket and really reads the body in pieces.

The case worth naming: **the usage frame has an empty `choices` array.** A parser that does
`chunk["choices"][0]` unconditionally works on every frame of a streamed response and then
crashes on the last one — in production, after the user has already watched the answer
appear. There is a test for it because that is the kind of bug that survives review.
"""

from __future__ import annotations

import pytest

from app.ai.providers.base import LLMProvider
from app.ai.providers.guard import BlockedProvider, guard
from app.ai.providers.heuristic import HeuristicLLMProvider
from app.ai.providers.openai_compatible import OpenAICompatibleProvider
from app.ai.types import ChatMessage, ChatResult, ChatStreamChunk, Usage
from app.core.errors import AIProviderError

from ._stub_provider import StubProvider

#: Synthetic and marked as such so the repository's own secret scanner does not flag it.
STUB_KEY = "sk-fake-stub-0000000000-not-a-real-credential"
PIECES = ["封版", "版本号", "是 ", "2.2.0", "。"]


def _provider(stub: StubProvider, *, timeout: float = 20.0) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url=stub.base_url,
        api_key=STUB_KEY,
        model="stub-model",
        timeout=timeout,
        max_retries=1,
    )


async def _collect(provider: OpenAICompatibleProvider) -> list[ChatStreamChunk]:
    return [
        chunk
        async for chunk in provider.stream_chat(
            [ChatMessage(role="user", content="封版版本号是多少？")], max_tokens=64
        )
    ]


async def test_the_provider_declares_streaming_support() -> None:
    with StubProvider([], stream_pieces=PIECES) as stub:
        provider = _provider(stub)
        assert provider.supports_streaming is True
        await provider.aclose()


async def test_chunks_arrive_in_order_and_reassemble() -> None:
    with StubProvider([], stream_pieces=PIECES) as stub:
        provider = _provider(stub)
        chunks = await _collect(provider)
        await provider.aclose()

    text = "".join(c.delta for c in chunks)
    assert text == "".join(PIECES)
    # One chunk per frame, in the order the server sent them.
    deltas = [c.delta for c in chunks if c.delta]
    assert deltas == PIECES


async def test_the_request_actually_asked_for_a_stream() -> None:
    """A test that passed on the non-streaming path would prove nothing."""
    with StubProvider([], stream_pieces=PIECES) as stub:
        provider = _provider(stub)
        await _collect(provider)
        await provider.aclose()
        sent = stub.requests[-1]

    assert sent.get("stream") is True
    # Without this the vendor reports no token counts on a streamed response at all, and
    # the run record would show zero tokens for a call that cost real money.
    assert sent.get("stream_options", {}).get("include_usage") is True


async def test_the_terminator_ends_the_stream() -> None:
    with StubProvider([], stream_pieces=PIECES) as stub:
        provider = _provider(stub)
        chunks = await _collect(provider)
        await provider.aclose()

    # 5 content frames + 1 finish frame. Nothing after [DONE].
    assert len([c for c in chunks if c.delta]) == len(PIECES)
    assert [c for c in chunks if c.finish_reason][-1].finish_reason == "stop"


async def test_the_usage_frame_with_empty_choices_is_parsed() -> None:
    usage = {"prompt_tokens": 41, "completion_tokens": 12, "total_tokens": 53}
    with StubProvider([], stream_pieces=PIECES, stream_usage=usage) as stub:
        provider = _provider(stub)
        chunks = await _collect(provider)
        await provider.aclose()

    reported = [c.usage for c in chunks if c.usage is not None]
    assert reported, "the usage frame was dropped"
    assert reported[-1].total_tokens == 53
    # And the answer still reassembled — the usage frame must not truncate the text.
    assert "".join(c.delta for c in chunks) == "".join(PIECES)


async def test_a_malformed_frame_is_skipped_not_fatal() -> None:
    """One bad frame must not discard an answer that is already half-received."""
    with StubProvider([], stream_pieces=PIECES, stream_garbage="{not json at all") as stub:
        provider = _provider(stub)
        chunks = await _collect(provider)
        await provider.aclose()

    assert "".join(c.delta for c in chunks) == "".join(PIECES)


async def test_an_http_error_before_the_stream_is_classified() -> None:
    """A 401 read as "the stream ended" would look like an empty answer."""
    with StubProvider([], status=503, stream_pieces=PIECES) as stub:
        provider = _provider(stub)
        with pytest.raises(AIProviderError):
            await _collect(provider)
        await provider.aclose()


async def test_the_guard_refuses_a_streamed_call_too() -> None:
    """The guard has to cover the newest code path, not only the one it was written for."""

    class _Vendor:
        name = "openai_compatible"
        model = "gpt-4o-mini"
        base_url = "https://api.deepseek.com/v1"
        supports_streaming = True

    wrapped = guard(_Vendor(), kind="openai_compatible")
    assert isinstance(wrapped, BlockedProvider)
    with pytest.raises(Exception) as caught:
        async for _chunk in wrapped.stream_chat([]):
            pass
    assert "MODEL_CALL_BLOCKED" in str(caught.value)


async def test_the_default_stream_is_honest_about_not_being_incremental() -> None:
    """A provider that cannot stream must not pretend to.

    The base class default yields the whole answer as one chunk and keeps
    `supports_streaming` False, so a caller can tell "this provider streams" from "this
    provider was wrapped to look like it does".
    """

    class _OneShot(LLMProvider):
        name = "one-shot"

        async def chat(self, messages, **kwargs) -> ChatResult:
            return ChatResult(content="whole answer", usage=Usage(total_tokens=3))

    provider = _OneShot(model="x", timeout=5, max_retries=1)
    assert provider.supports_streaming is False
    chunks = [c async for c in provider.stream_chat([ChatMessage(role="user", content="hi")])]
    assert len(chunks) == 1
    assert chunks[0].delta == "whole answer"


async def test_the_offline_engine_reports_no_streaming_support() -> None:
    """The offline engine computes the whole answer at once, so it cannot stream."""
    provider = HeuristicLLMProvider()
    assert provider.supports_streaming is False

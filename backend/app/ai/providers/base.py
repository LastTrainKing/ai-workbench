"""Abstract provider interfaces.

Every concrete provider must implement these two interfaces.  Business code
depends only on the interfaces, which is what makes ``AI_PROVIDER=ollama`` a
pure configuration change (see docs/ai.md).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence

from app.ai.types import ChatMessage, ChatResult, ChatStreamChunk, EmbeddingResult, ToolSpec


class LLMProvider(ABC):
    """Chat-completion capable model backend."""

    name: str = "abstract"

    def __init__(self, *, model: str, timeout: float, max_retries: int) -> None:
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    @property
    def supports_tool_calling(self) -> bool:
        return False

    @property
    def supports_streaming(self) -> bool:
        """Whether `stream_chat` produces real incremental output.

        False by default, and the default is the honest answer for the offline engine: it
        computes the whole answer from the request text in one go, so "streaming" it would
        be splitting a string that already exists. A caller that wants the incremental
        behaviour has to check this rather than assume it.
        """
        return False

    @abstractmethod
    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: Sequence[ToolSpec] | None = None,
        json_mode: bool = False,
    ) -> ChatResult:
        """Run a single chat completion."""

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: Sequence[ToolSpec] | None = None,
        json_mode: bool = False,
    ) -> AsyncIterator[ChatStreamChunk]:
        """Yield a completion incrementally.

        The default is deliberately **not** a fake stream. It performs the ordinary call and
        yields the whole answer as one chunk, and `supports_streaming` stays False — so a
        caller that checks can tell "this provider streams" from "this provider was wrapped
        to look like it does". A one-chunk stream that pretends to be incremental is exactly
        the kind of thing this project keeps writing comments about.
        """
        result = await self.chat(
            messages, temperature=temperature, max_tokens=max_tokens, tools=tools, json_mode=json_mode
        )
        if result.content or result.reasoning_content:
            yield ChatStreamChunk(
                delta=result.content,
                finish_reason=result.finish_reason,
                usage=result.usage,
                reasoning_delta=result.reasoning_content,
            )

    async def aclose(self) -> None:
        """Release transport resources (no-op by default)."""
        return None


class EmbeddingProvider(ABC):
    """Text-to-vector backend."""

    name: str = "abstract"

    def __init__(self, *, model: str, timeout: float) -> None:
        self.model = model
        self.timeout = timeout

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Vector dimension, used to create the Qdrant collection."""

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        """Embed a batch of texts."""

    async def aclose(self) -> None:
        return None

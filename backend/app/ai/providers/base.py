"""Abstract provider interfaces.

Every concrete provider must implement these two interfaces.  Business code
depends only on the interfaces, which is what makes ``AI_PROVIDER=ollama`` a
pure configuration change (see docs/ai.md).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.ai.types import ChatMessage, ChatResult, EmbeddingResult, ToolSpec


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

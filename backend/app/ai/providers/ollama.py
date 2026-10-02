"""Ollama provider — fully local inference (local-first privacy mode).

Requires a running Ollama daemon (``ollama serve``) and a pulled model, e.g.::

    ollama pull qwen2.5:7b
    ollama pull nomic-embed-text

Switching to it is a pure configuration change::

    AI_PROVIDER=ollama
    AI_BASE_URL=http://127.0.0.1:11434
    AI_MODEL=qwen2.5:7b
    EMBEDDING_PROVIDER=ollama
    EMBEDDING_MODEL=nomic-embed-text
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

import httpx

from app.ai.providers.base import EmbeddingProvider, LLMProvider
from app.ai.types import (
    ChatMessage,
    ChatResult,
    EmbeddingResult,
    ToolCallRequest,
    ToolSpec,
    Usage,
    estimate_tokens,
)
from app.core.errors import (
    AIProviderError,
    AIResponseFormatError,
    AITimeoutError,
)
from app.core.logging import get_logger
from app.core.retry import RetryPolicy, retry_async

logger = get_logger(__name__)


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        model: str,
        timeout: float,
        max_retries: int,
        backoff_base: float = 1.0,
    ) -> None:
        super().__init__(model=model, timeout=timeout, max_retries=max_retries)
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._policy = RetryPolicy(max_attempts=max_retries, base_delay=backoff_base)

    @property
    def supports_tool_calling(self) -> bool:
        return True

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=5.0),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async def _call() -> dict[str, Any]:
            try:
                resp = await self._http().post(path, json=payload)
            except httpx.TimeoutException as exc:
                raise AITimeoutError(
                    f"Ollama request timed out after {self.timeout}s",
                    details={"path": path, "hint": "increase AI_TIMEOUT_SECONDS"},
                ) from exc
            except httpx.HTTPError as exc:
                raise AIProviderError(
                    "Cannot reach the local Ollama daemon. Is `ollama serve` running?",
                    details={"base_url": self.base_url, "error": str(exc)},
                ) from exc
            if resp.status_code >= 400:
                raise AIProviderError(
                    f"Ollama returned HTTP {resp.status_code}",
                    details={"body": resp.text[:500]},
                )
            try:
                return resp.json()
            except json.JSONDecodeError as exc:
                raise AIResponseFormatError("Ollama returned a non-JSON body") from exc

        return await retry_async(_call, policy=self._policy, operation=f"ollama:{path}")

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: Sequence[ToolSpec] | None = None,
        json_mode: bool = False,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    **({"tool_calls": m.tool_calls} if m.tool_calls else {}),
                }
                for m in messages
            ],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_mode:
            payload["format"] = "json"
        if tools:
            payload["tools"] = [t.to_openai()["function"] for t in tools]

        started = time.perf_counter()
        data = await self._post("/api/chat", payload)
        latency_ms = int((time.perf_counter() - started) * 1000)

        message = data.get("message") or {}
        content = message.get("content") or ""
        tool_calls: list[ToolCallRequest] = []
        for i, tc in enumerate(message.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(
                ToolCallRequest(
                    id=f"ollama_call_{i}",
                    name=fn.get("name") or "",
                    arguments=dict(args or {}),
                )
            )

        prompt_tokens = int(data.get("prompt_eval_count") or 0)
        completion_tokens = int(data.get("eval_count") or 0)
        if not prompt_tokens:
            prompt_tokens = sum(estimate_tokens(m.content) for m in messages)
        if not completion_tokens:
            completion_tokens = estimate_tokens(content)

        return ChatResult(
            content=content,
            model=data.get("model") or self.model,
            provider=self.name,
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            tool_calls=tool_calls,
            finish_reason=data.get("done_reason") or "stop",
            latency_ms=latency_ms,
        )

    async def list_models(self) -> list[str]:
        try:
            resp = await self._http().get("/api/tags")
            resp.raise_for_status()
            return [m.get("name", "") for m in resp.json().get("models", [])]
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.warning("ollama_list_models_failed", extra={"error": str(exc)})
            return []


class OllamaEmbeddingProvider(EmbeddingProvider):
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "nomic-embed-text",
        timeout: float = 60.0,
        dimension_hint: int = 0,
    ) -> None:
        super().__init__(model=model, timeout=timeout)
        self.base_url = base_url.rstrip("/")
        self._dim = dimension_hint
        self._client: httpx.AsyncClient | None = None

    @property
    def dimension(self) -> int:
        return self._dim

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=httpx.Timeout(self.timeout, connect=5.0)
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], model=self.model, provider=self.name)
        started = time.perf_counter()
        try:
            resp = await self._http().post(
                "/api/embed", {"model": self.model, "input": list(texts)}
            )
            if resp.status_code == 404:
                # Older Ollama versions only expose /api/embeddings (single input).
                vectors: list[list[float]] = []
                for text in texts:
                    single = await self._http().post(
                        "/api/embeddings", {"model": self.model, "prompt": text}
                    )
                    single.raise_for_status()
                    vectors.append([float(v) for v in single.json()["embedding"]])
            else:
                resp.raise_for_status()
                vectors = [[float(v) for v in row] for row in resp.json()["embeddings"]]
        except httpx.TimeoutException as exc:
            raise AITimeoutError(f"Ollama embedding timed out after {self.timeout}s") from exc
        except (httpx.HTTPError, KeyError, json.JSONDecodeError) as exc:
            raise AIProviderError(
                "Ollama embedding failed. Is the embedding model pulled?",
                details={"model": self.model, "error": str(exc)},
            ) from exc

        self._dim = len(vectors[0]) if vectors else self._dim
        return EmbeddingResult(
            vectors=vectors,
            model=self.model,
            provider=self.name,
            dimension=self._dim,
            usage=Usage(prompt_tokens=sum(estimate_tokens(t) for t in texts)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

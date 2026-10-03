"""OpenAI-compatible chat / embedding provider.

Works with any vendor exposing the OpenAI HTTP contract: OpenAI, DeepSeek,
Moonshot, Zhipu, SiliconFlow, DashScope-compatible mode, vLLM, LM Studio, ...

All transport concerns (timeout, bounded retry with exponential backoff, error
classification) live here so that services never deal with ``httpx`` directly.
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
    AIAuthError,
    AIProviderError,
    AIRateLimitError,
    AIResponseFormatError,
    AITimeoutError,
)
from app.core.logging import get_logger
from app.core.retry import RetryPolicy, retry_async

logger = get_logger(__name__)

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 522, 524}


def _vendor_message(body: str) -> str:
    """Extract the vendor's own error text so it reaches the operator.

    Without this, a 400 shows up in the AI run trace as
    "AI provider returned an unexpected HTTP 400." — which tells you nothing.
    Vendors almost always explain the problem in the body (for example
    "Prompt must contain the word 'json' …"), so it is surfaced verbatim.
    """
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return body[:300]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "")[:300]
    if isinstance(error, str):
        return error[:300]
    return body[:300]


def _classify_status(status_code: int, body: str) -> Exception:
    snippet = _vendor_message(body)
    details = {"status": status_code, "body": body[:500], "vendor_message": snippet}

    if status_code in (401, 403):
        return AIAuthError(
            f"The AI provider rejected the credentials. Check AI_API_KEY. ({snippet})",
            details=details,
        )
    if status_code == 429:
        return AIRateLimitError(
            f"The AI provider rate-limited this request. ({snippet})", details=details
        )
    if status_code in _RETRYABLE_STATUS:
        return AIProviderError(
            f"AI provider returned HTTP {status_code}. ({snippet})", details=details
        )
    return AIProviderError(
        f"AI provider returned an unexpected HTTP {status_code}. ({snippet})",
        details=details,
        status_code=502,
    )


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float,
        max_retries: int,
        backoff_base: float = 1.0,
    ) -> None:
        super().__init__(model=model, timeout=timeout, max_retries=max_retries)
        if not api_key:
            raise AIAuthError(
                "AI_API_KEY is not configured for the openai_compatible provider."
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client: httpx.AsyncClient | None = None
        self._policy = RetryPolicy(max_attempts=max_retries, base_delay=backoff_base)

    @property
    def supports_tool_calling(self) -> bool:
        return True

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=min(15.0, self.timeout)),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
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
                    f"AI request timed out after {self.timeout}s",
                    details={"path": path},
                ) from exc
            except httpx.HTTPError as exc:
                raise AIProviderError(
                    f"Network error talking to the AI provider: {type(exc).__name__}",
                    details={"path": path, "error": str(exc)},
                ) from exc

            if resp.status_code >= 400:
                raise _classify_status(resp.status_code, resp.text)

            try:
                return resp.json()
            except json.JSONDecodeError as exc:
                raise AIResponseFormatError(
                    "AI provider returned a non-JSON body",
                    details={"body": resp.text[:500]},
                ) from exc

        return await retry_async(_call, policy=self._policy, operation=f"openai:{path}")

    # ------------------------------------------------------------------ chat --
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
            "messages": [m.to_openai() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [t.to_openai() for t in tools]
            payload["tool_choice"] = "auto"
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        data = await self._post("/chat/completions", payload)
        latency_ms = int((time.perf_counter() - started) * 1000)

        choices = data.get("choices") or []
        if not choices:
            raise AIResponseFormatError(
                "AI provider response contained no choices", details={"keys": list(data)}
            )
        message = choices[0].get("message") or {}
        content = message.get("content") or ""

        tool_calls: list[ToolCallRequest] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                # Keep the malformed payload: the agent layer decides how to
                # recover instead of us silently discarding the model's intent.
                args = {}
            tool_calls.append(
                ToolCallRequest(
                    id=tc.get("id") or f"call_{len(tool_calls)}",
                    name=fn.get("name") or "",
                    arguments=args,
                    raw_arguments=raw_args if isinstance(raw_args, str) else "",
                )
            )

        usage_raw = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
            completion_tokens=int(usage_raw.get("completion_tokens") or 0),
            total_tokens=int(usage_raw.get("total_tokens") or 0),
        )
        if not usage.total_tokens:
            usage = Usage(
                prompt_tokens=sum(estimate_tokens(m.content) for m in messages),
                completion_tokens=estimate_tokens(content),
                total_tokens=0,
            )
            usage.total_tokens = usage.prompt_tokens + usage.completion_tokens

        return ChatResult(
            content=content,
            model=data.get("model") or self.model,
            provider=self.name,
            usage=usage,
            tool_calls=tool_calls,
            finish_reason=choices[0].get("finish_reason") or "stop",
            latency_ms=latency_ms,
            reasoning_content=str(message.get("reasoning_content") or ""),
        )

    # ------------------------------------------------------------- embedding --
    async def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], model=self.model, provider=self.name, dimension=0)

        started = time.perf_counter()
        data = await self._post(
            "/embeddings", {"model": self.model, "input": list(texts)}
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise AIResponseFormatError(
                "Embedding response size does not match the request",
                details={"expected": len(texts), "got": len(rows) if isinstance(rows, list) else None},
            )
        # Some vendors return rows out of order; honour the explicit index.
        ordered = sorted(rows, key=lambda r: r.get("index", 0))
        vectors = [list(map(float, r["embedding"])) for r in ordered]
        usage_raw = data.get("usage") or {}
        return EmbeddingResult(
            vectors=vectors,
            model=data.get("model") or self.model,
            provider=self.name,
            dimension=len(vectors[0]) if vectors else 0,
            usage=Usage(
                prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
                total_tokens=int(usage_raw.get("total_tokens") or 0),
            ),
            latency_ms=latency_ms,
        )


class OpenAICompatibleEmbeddingProvider(OpenAICompatibleProvider, EmbeddingProvider):
    """Reuses the same HTTP plumbing for ``/embeddings``."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float,
        max_retries: int = 3,
        dimension_hint: int = 0,
    ) -> None:
        OpenAICompatibleProvider.__init__(
            self,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
        )
        self._dimension = dimension_hint

    @property
    def dimension(self) -> int:
        return self._dimension

    def set_dimension(self, dim: int) -> None:
        self._dimension = dim

"""DeepSeek provider.

DeepSeek exposes an OpenAI-compatible HTTP contract, so this provider **reuses**
:class:`OpenAICompatibleProvider` rather than reimplementing transport, retry and
error classification. It only adds what is genuinely DeepSeek-specific:

* DeepSeek's own defaults (base URL, model catalogue, env namespace);
* discovery of the account's real model list via ``GET /models``;
* a dedicated exception for the "insufficient balance" response, which is a
  configuration problem and must never be retried;
* extraction of ``reasoning_content`` when a reasoning-capable model returns it.

Architecture position::

    AI service → ProviderRegistry → DeepSeekProvider → DeepSeek API
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.ai.providers.openai_compatible import OpenAICompatibleProvider
from app.ai.types import ChatMessage, ChatResult
from app.core.errors import AIAuthError, AIError, AIProviderError
from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"

#: Models exposed by DeepSeek, with the label shown in the UI.
#: ``deepseek-flash`` is DeepSeek V4.1 Flash.
KNOWN_MODELS: list[dict[str, str]] = [
    {
        "id": "deepseek-flash",
        "label": "DeepSeek V4.1 Flash",
        "description": "快速、低成本的通用模型，适合 RAG 问答、抽取与规划",
    },
    {
        "id": "deepseek-v4-pro",
        "label": "DeepSeek V4 Pro",
        "description": "更强推理能力，适合复杂规划与 Agent 任务，成本更高",
    },
]

#: HTTP status → meaning for DeepSeek specifically.
_PAYMENT_REQUIRED = 402


class DeepSeekProvider(OpenAICompatibleProvider):
    """Chat provider for the DeepSeek API."""

    name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-flash",
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 90.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ) -> None:
        if not api_key or not api_key.strip():
            raise AIAuthError(
                "DEEPSEEK_API_KEY is not configured. Add it to .env or set it in "
                "Settings → AI Provider."
            )
        super().__init__(
            base_url=_normalise_base_url(base_url),
            api_key=api_key.strip(),
            model=model or "deepseek-flash",
            timeout=timeout,
            max_retries=max_retries,
            backoff_base=backoff_base,
        )

    # ------------------------------------------------------------- discovery --
    async def list_models(self) -> list[dict[str, str]]:
        """Return the account's real model list, annotated with UI labels.

        Falls back to :data:`KNOWN_MODELS` when the endpoint is unreachable so
        the Settings page is never empty.
        """
        labels = {m["id"]: m for m in KNOWN_MODELS}
        try:
            response = await self._http().get("/models")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "deepseek_list_models_failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return list(KNOWN_MODELS)

        models: list[dict[str, str]] = []
        for row in payload.get("data") or []:
            model_id = str(row.get("id") or "").strip()
            if not model_id:
                continue
            known = labels.get(model_id)
            models.append(
                {
                    "id": model_id,
                    "label": known["label"] if known else model_id,
                    "description": known["description"] if known else "",
                }
            )
        return models or list(KNOWN_MODELS)

    # ------------------------------------------------------------------ chat --
    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: Sequence[Any] | None = None,
        json_mode: bool = False,
    ) -> ChatResult:
        prepared = list(messages)
        if json_mode:
            prepared = _ensure_json_keyword(prepared)

        try:
            result = await super().chat(
                prepared,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                json_mode=json_mode,
            )
        except AIProviderError as exc:
            raise _translate_deepseek_error(exc) from exc

        # `deepseek-flash` is a reasoning model: it streams its chain of thought
        # into `reasoning_content` and only then writes the answer into
        # `content`. Keep the reasoning on the trace instead of discarding it,
        # and surface the case where the token budget ran out mid-thought —
        # otherwise the caller sees an empty answer with no explanation.
        reasoning = result.reasoning_content or _extract_reasoning(result.raw)
        if reasoning:
            result.reasoning_content = reasoning
            result.raw = {**result.raw, "reasoning_content": reasoning}
        if not result.content.strip() and not result.tool_calls and reasoning:
            raise AIError(
                "模型在思考阶段用尽了 token 预算，没有产出最终回答。"
                "请提高 Max Tokens（推理模型需要额外的思考预算）。",
                code="ai_reasoning_budget_exhausted",
                status_code=502,
                details={"reasoning_chars": len(reasoning), "max_tokens": max_tokens},
            )
        return result


def _ensure_json_keyword(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Guarantee the prompt literally contains the word ``json``.

    DeepSeek rejects ``response_format={"type": "json_object"}`` with HTTP 400
    unless the word "json" appears somewhere in the prompt::

        Prompt must contain the word 'json' in some form to use
        'response_format' of type 'json_object'.

    Our prompt templates are written in Chinese ("输出严格 JSON"), and a
    case-insensitive search for the ASCII string "json" does match "JSON", so
    most templates are fine — but a custom template written as "输出结构化对象"
    would fail. Rather than making every prompt author remember a vendor quirk,
    the requirement is satisfied here, in the one place that knows about it.
    """
    haystack = " ".join(m.content or "" for m in messages).lower()
    if "json" in haystack:
        return messages

    instruction = "Return a single valid json object and nothing else."
    for index, message in enumerate(messages):
        if message.role == "system":
            updated = list(messages)
            updated[index] = ChatMessage(
                role="system",
                content=f"{message.content}\n\n{instruction}",
            )
            logger.debug("deepseek_json_keyword_injected", extra={"role": "system"})
            return updated

    # No system message: attach the requirement to the last user turn.
    updated = list(messages)
    for index in range(len(updated) - 1, -1, -1):
        if updated[index].role == "user":
            updated[index] = ChatMessage(
                role="user",
                content=f"{updated[index].content}\n\n{instruction}",
            )
            break
    logger.debug("deepseek_json_keyword_injected", extra={"role": "user"})
    return updated


def _normalise_base_url(base_url: str) -> str:
    """Accept both ``https://api.deepseek.com`` and ``.../v1``.

    DeepSeek serves the OpenAI-compatible routes at both, but normalising keeps
    the client's path joining predictable and avoids a double ``/v1`` when a
    user pastes the versioned URL from the docs.
    """
    cleaned = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if cleaned.endswith("/v1"):
        return cleaned
    return cleaned


def _extract_reasoning(raw: dict[str, Any] | None) -> str:
    if not isinstance(raw, dict):
        return ""
    choices = raw.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("reasoning_content") or "").strip()


def _translate_deepseek_error(exc: AIProviderError) -> AIError:
    """Turn DeepSeek's non-retryable failures into precise, actionable errors."""
    details = exc.details if isinstance(exc.details, dict) else {}
    status = details.get("status")
    body = str(details.get("body") or "")

    if status == _PAYMENT_REQUIRED or "Insufficient Balance" in body:
        return AIError(
            "DeepSeek 账户余额不足，请充值后重试（或切换到 Offline Provider）。",
            code="ai_insufficient_balance",
            status_code=402,
            details={"provider": "deepseek"},
        )
    if status == 400 and "Model Not Exist" in body:
        return AIError(
            "DeepSeek 不认识该模型名称。请在设置中选择 deepseek-flash 或 deepseek-v4-pro。",
            code="ai_model_not_found",
            status_code=400,
            details={"body": body[:300]},
        )
    return exc


__all__ = ["DEFAULT_BASE_URL", "KNOWN_MODELS", "DeepSeekProvider"]

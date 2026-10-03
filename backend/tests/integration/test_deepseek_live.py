"""Live DeepSeek integration tests.

These call the **real** DeepSeek API. They are skipped automatically when no key
is configured, so the suite still passes on a machine without credentials and in
CI — and the skip reason is reported, never hidden.

The key is read from the environment / ``.env`` by the application itself; it is
never referenced, printed or hard-coded here.

Run just these::

    pytest tests/integration/test_deepseek_live.py -v

Cost: each test sends a deliberately short request (``max_tokens`` capped), so a
full run costs a fraction of a cent.
"""

from __future__ import annotations

import os

import pytest

from app.ai.providers.deepseek import DeepSeekProvider
from app.ai.runtime_config import resolve
from app.ai.types import ChatMessage, ToolSpec
from app.core.config import settings

pytestmark = [pytest.mark.integration, pytest.mark.slow]

#: Skip reason surfaced by pytest when the key is missing.
NO_KEY = "DEEPSEEK_API_KEY is not configured — live DeepSeek tests skipped"


def _api_key() -> str:
    return (settings.deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY") or "").strip()


requires_key = pytest.mark.skipif(not _api_key(), reason=NO_KEY)


@pytest.fixture
def provider() -> DeepSeekProvider:
    return DeepSeekProvider(
        api_key=_api_key(),
        model="deepseek-flash",
        base_url=settings.deepseek_base_url,
        timeout=60.0,
        max_retries=2,
    )


# --------------------------------------------------------------------------- #
# connectivity
# --------------------------------------------------------------------------- #
@requires_key
@pytest.mark.asyncio
async def test_real_chat_completion(provider: DeepSeekProvider) -> None:
    """A real round-trip returns content, usage and a model name."""
    result = await provider.chat(
        [ChatMessage(role="user", content="只回答两个字：收到")],
        temperature=0.0,
        max_tokens=256,
    )
    assert result.provider == "deepseek"
    assert result.model.startswith("deepseek")
    assert result.usage.total_tokens > 0
    assert result.latency_ms >= 0


@requires_key
@pytest.mark.asyncio
async def test_real_model_listing(provider: DeepSeekProvider) -> None:
    models = await provider.list_models()
    ids = {m["id"] for m in models}
    assert "deepseek-flash" in ids
    assert all(m["label"] for m in models)


# --------------------------------------------------------------------------- #
# structured output — the vendor requires the literal word "json"
# --------------------------------------------------------------------------- #
@requires_key
@pytest.mark.asyncio
async def test_real_json_mode_succeeds_without_the_word_json(provider: DeepSeekProvider) -> None:
    """The provider injects the required keyword, so a Chinese prompt works.

    Without the injection DeepSeek answers HTTP 400 with
    "Prompt must contain the word 'json' …". This test is the regression guard.
    """
    from app.ai.rag.pipeline import load_json_object

    result = await provider.chat(
        [
            ChatMessage(role="system", content="请输出一个结构化对象，包含字段 ok。"),
            ChatMessage(role="user", content="输出 {\"ok\": true}"),
        ],
        temperature=0.0,
        max_tokens=512,
        json_mode=True,
    )
    payload = load_json_object(result.content)
    assert isinstance(payload, dict)


# --------------------------------------------------------------------------- #
# tool calling — and the reasoning_content contract
# --------------------------------------------------------------------------- #
@requires_key
@pytest.mark.asyncio
async def test_real_tool_calling_round_trip(provider: DeepSeekProvider) -> None:
    """Two turns: the model requests a tool, then we feed the result back.

    The second turn is the important part. ``deepseek-flash`` is a reasoning
    model and rejects a follow-up that omits the previous assistant turn's
    ``reasoning_content`` with HTTP 400. This test proves the value is echoed
    back correctly.
    """
    tools = [
        ToolSpec(
            name="knowledge_search",
            description="Search the user's knowledge base.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
    ]
    messages = [
        ChatMessage(role="system", content="You must call a tool to answer."),
        ChatMessage(role="user", content="Search my notes for RAG chunking."),
    ]

    first = await provider.chat(messages, temperature=0.0, max_tokens=1024, tools=tools)
    assert first.tool_calls, "the model should have requested a tool"

    call = first.tool_calls[0]
    messages.append(
        ChatMessage(
            role="assistant",
            content=first.content or "",
            reasoning_content=first.reasoning_content or None,
            tool_calls=[
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": "{}"},
                }
            ],
        )
    )
    messages.append(
        ChatMessage(
            role="tool",
            name=call.name,
            tool_call_id=call.id,
            content='{"results":[{"title":"chunking","snippet":"overlap keeps context"}]}',
        )
    )

    second = await provider.chat(messages, temperature=0.0, max_tokens=1024, tools=tools)
    assert second.content or second.tool_calls


# --------------------------------------------------------------------------- #
# the configuration the application actually resolves
# --------------------------------------------------------------------------- #
@requires_key
def test_resolution_honours_the_configured_provider(monkeypatch) -> None:
    """With ``AI_PROVIDER=deepseek`` the resolver must target DeepSeek.

    The test suite pins ``AI_PROVIDER=offline`` (conftest) so that the rest of
    the tests never touch a billable endpoint. This test therefore sets the
    value explicitly rather than relying on the ambient environment.
    """
    monkeypatch.setattr(settings, "ai_provider", "deepseek")
    monkeypatch.setattr(settings, "deepseek_model", "deepseek-flash")

    cfg = resolve()
    assert cfg.provider == "deepseek"
    assert cfg.model == "deepseek-flash"
    assert cfg.api_key_set is True
    assert cfg.base_url.startswith("https://api.deepseek.com")


@requires_key
def test_the_raw_key_is_never_serialised() -> None:
    key = _api_key()
    assert key.startswith("sk-")

    from app.ai.runtime_config import mask_api_key

    masked = mask_api_key(key)
    assert masked.endswith(")")
    assert key not in masked
    assert key[-4:] not in masked

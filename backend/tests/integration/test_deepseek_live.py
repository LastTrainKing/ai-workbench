"""Live DeepSeek integration tests.

These call the **real** DeepSeek API. They are skipped automatically when no key
is configured, so the suite still passes on a machine without credentials and in
CI — and the skip reason is reported, never hidden.

Run just these::

    pytest tests/integration/test_deepseek_live.py -v

Cost: each test sends a deliberately short request (``max_tokens`` capped), so a
full run costs a fraction of a cent.

**Where the key comes from — one decision, one implementer**
------------------------------------------------------------
These tests used to read ``settings.deepseek_api_key`` or ``DEEPSEEK_API_KEY`` in the
environment, while the application reads the **account → installation → environment →
defaults** chain. Environment is the *third* layer, so a key configured through the
interface (``.runtime/ai-config.json``) was invisible here: six live tests skipped on a
machine where the product worked, and the old docstring — "the key is read from the
environment by the application itself" — described a resolution order the application had
stopped using.

The tests now ask ``runtime_config.resolve()``, which is the application's own answer. The
alternative (copying the key into ``.env``) would leave two copies of one secret, free to
drift apart — which is the shape of the bug being fixed. It also means these tests exercise
the same provider, model, endpoint and timeout the product would use, rather than a
hand-built approximation of them.
"""

from __future__ import annotations

import os

import pytest

from app.ai.providers.deepseek import DeepSeekProvider
from app.ai.runtime_config import ResolvedAIConfig, mask_api_key, resolve
from app.ai.types import ChatMessage, ToolSpec
from app.core.config import PROJECT_ROOT

pytestmark = [pytest.mark.integration, pytest.mark.slow]

#: The machine's **real** runtime root — the repository's `.runtime`.
#:
#: Not the one the suite runs against: `conftest.py` points `AI_WORKBENCH_DATA_DIR` at
#: `.runtime/test-runtime` so the other tests cannot pick up a developer's key. These
#: tests are the exception, and deliberately so — their whole purpose is "does the real
#: service work with the real credentials". Reading the throwaway root would make them
#: skip forever on a machine where the product works, which is exactly the bug this
#: module was fixed for.
REAL_RUNTIME_ROOT = PROJECT_ROOT / ".runtime"

#: Skip reason surfaced by pytest when the key is missing.
NO_KEY = (
    "no DeepSeek key in the resolved configuration at "
    f"{REAL_RUNTIME_ROOT} (account → installation → environment → defaults); "
    "live tests skipped"
)


def _resolved() -> ResolvedAIConfig:
    """The configuration the application itself would use.

    ``resolve()`` needs no database — it reads the runtime config files and the
    environment — so it is safe to call at import time, which is where the skip marker
    is decided.
    """
    return resolve(root=REAL_RUNTIME_ROOT)


def _api_key() -> str:
    return (_resolved().api_key or "").strip()


requires_key = pytest.mark.skipif(not _api_key(), reason=NO_KEY)


def key_origin() -> str:
    """Which layer the key came from, for the report. Never returns the key itself."""
    cfg = _resolved()
    return cfg.sources.get("api_key", "unknown")


@pytest.fixture
def provider() -> DeepSeekProvider:
    """A provider built from the resolved configuration, and counting what it spends.

    Model, endpoint and timeout come from the same place the product gets them; only the
    retry count is tightened, because a live test should fail fast rather than spend
    minutes retrying a request the assertion is about to reject.
    """
    cfg = _resolved()
    return _CountingProvider(
        api_key=cfg.api_key,
        model=cfg.model,
        base_url=cfg.base_url,
        timeout=60.0,
        max_retries=2,
    )


#: What this module actually spent, counted from the API's own ``usage`` field.
#:
#: **Why not `scripts/model_call_count.py`**: that script counts rows in `ai_runs`, and
#: these tests call the provider directly rather than through the application's run
#: recorder — so they leave no rows and the script would report **0**, which is a wrong
#: answer rather than a missing one. Counting the responses is the only measurement that
#: reflects what was actually billed.
SPEND: dict[str, int] = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}


class _CountingProvider(DeepSeekProvider):
    """The real provider, plus a tally of what came back.

    A subclass rather than a wrapper so the tests keep talking to the same object type
    they are testing; the only thing added is arithmetic.
    """

    async def chat(self, *args: object, **kwargs: object) -> object:
        result = await super().chat(*args, **kwargs)  # type: ignore[arg-type]
        SPEND["calls"] += 1
        usage = getattr(result, "usage", None)
        if usage is not None:
            SPEND["prompt_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
            SPEND["completion_tokens"] += int(getattr(usage, "completion_tokens", 0) or 0)
        return result

    async def list_models(self) -> object:
        models = await super().list_models()
        # A models listing is a request but not a completion: it bills as a call with no
        # tokens, which is why it is counted separately from `chat`.
        SPEND["calls"] += 1
        return models


@pytest.fixture(scope="module", autouse=True)
def _report_spend():
    """Print the real tally at the end of the module. Measured, never estimated."""
    yield
    total = SPEND["prompt_tokens"] + SPEND["completion_tokens"]
    print(
        "\n[live DeepSeek spend] "
        f"calls={SPEND['calls']} "
        f"prompt_tokens={SPEND['prompt_tokens']} "
        f"completion_tokens={SPEND['completion_tokens']} "
        f"total_tokens={total}"
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
            ChatMessage(role="user", content='输出 {"ok": true}'),
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
def test_resolution_honours_the_configured_provider() -> None:
    """The resolver targets what this machine is actually configured to use.

    This used to monkeypatch ``settings`` because the suite pins ``AI_PROVIDER=offline``
    and the test needed to escape it. That escape hatch is gone: it now resolves against
    the real runtime root, which is the same call the application makes, so the assertion
    is about the configuration rather than about a patched copy of it.

    The interesting assertion is the last one — the key comes from a **config file**, not
    from the environment. That is precisely the layer the old `_api_key()` could not see,
    and the reason six live tests skipped on a machine where the product worked.
    """
    cfg = resolve(root=REAL_RUNTIME_ROOT)

    assert cfg.provider == "deepseek"
    assert cfg.api_key_set is True
    assert cfg.base_url.startswith("https://api.deepseek.com")
    assert cfg.sources.get("api_key") in {"account", "install"}, cfg.sources


@requires_key
def test_the_raw_key_is_never_serialised() -> None:
    key = _api_key()
    assert key.startswith("sk-")

    masked = mask_api_key(key)
    assert masked.endswith(")")
    assert key not in masked
    assert key[-4:] not in masked

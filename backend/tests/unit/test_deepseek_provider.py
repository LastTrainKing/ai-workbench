"""Unit tests for the DeepSeek provider and the runtime AI configuration.

These never touch the network: they cover the provider's own logic (validation,
base-URL normalisation, the vendor-specific JSON requirement, error
translation), the provider registry and the configuration precedence rules.

The live API tests live in ``tests/integration/test_deepseek_live.py`` and skip
themselves when no key is configured.
"""

from __future__ import annotations

import pytest

from app.ai.providers.deepseek import (
    DEFAULT_BASE_URL,
    KNOWN_MODELS,
    DeepSeekProvider,
    _ensure_json_keyword,
    _normalise_base_url,
    _translate_deepseek_error,
)
from app.ai.providers.registry import PROVIDERS, catalog, get, ids
from app.ai.runtime_config import OVERRIDABLE, mask_api_key
from app.ai.types import ChatMessage
from app.core.errors import AIAuthError, AIError, AIProviderError


# --------------------------------------------------------------------------- #
# construction & validation
# --------------------------------------------------------------------------- #
def test_provider_requires_an_api_key() -> None:
    with pytest.raises(AIAuthError) as exc:
        DeepSeekProvider(api_key="")
    assert "DEEPSEEK_API_KEY" in exc.value.message


def test_provider_rejects_whitespace_only_key() -> None:
    with pytest.raises(AIAuthError):
        DeepSeekProvider(api_key="   ")


def test_provider_accepts_a_key_and_defaults() -> None:
    provider = DeepSeekProvider(api_key="sk-test")
    assert provider.name == "deepseek"
    assert provider.model == "deepseek-flash"
    assert provider.supports_tool_calling is True


def test_key_is_stripped_not_stored_verbatim() -> None:
    provider = DeepSeekProvider(api_key="  sk-test  ")
    assert provider.api_key == "sk-test"


# --------------------------------------------------------------------------- #
# base URL handling
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://api.deepseek.com", "https://api.deepseek.com"),
        ("https://api.deepseek.com/", "https://api.deepseek.com"),
        ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1"),
        ("  https://api.deepseek.com  ", "https://api.deepseek.com"),
        ("", DEFAULT_BASE_URL),
    ],
)
def test_base_url_is_normalised(raw: str, expected: str) -> None:
    assert _normalise_base_url(raw) == expected


def test_default_base_url_is_deepseek() -> None:
    assert DeepSeekProvider(api_key="sk-test").base_url == DEFAULT_BASE_URL


# --------------------------------------------------------------------------- #
# the vendor's JSON-mode requirement
# --------------------------------------------------------------------------- #
def test_json_keyword_is_not_duplicated_when_already_present() -> None:
    messages = [ChatMessage(role="system", content="输出严格 JSON 对象")]
    assert _ensure_json_keyword(messages) is messages


def test_json_keyword_is_injected_into_the_system_message() -> None:
    messages = [
        ChatMessage(role="system", content="请输出结构化对象。"),
        ChatMessage(role="user", content="开始"),
    ]
    prepared = _ensure_json_keyword(messages)
    assert "json" in prepared[0].content.lower()
    assert prepared[1].content == "开始", "the user turn must be left alone"


def test_json_keyword_falls_back_to_the_user_turn() -> None:
    messages = [ChatMessage(role="user", content="请输出结构化对象。")]
    prepared = _ensure_json_keyword(messages)
    assert "json" in prepared[-1].content.lower()


def test_json_keyword_detects_uppercase_json() -> None:
    """The templates say "JSON"; a case-insensitive search must accept it."""
    messages = [ChatMessage(role="system", content="输出严格 JSON")]
    assert _ensure_json_keyword(messages) is messages


# --------------------------------------------------------------------------- #
# error translation
# --------------------------------------------------------------------------- #
def test_insufficient_balance_is_not_retryable() -> None:
    exc = AIProviderError(
        "AI provider returned an unexpected HTTP 402.",
        details={"status": 402, "body": '{"error":{"message":"Insufficient Balance"}}'},
    )
    translated = _translate_deepseek_error(exc)
    assert isinstance(translated, AIError)
    assert translated.code == "ai_insufficient_balance"
    assert translated.status_code == 402


def test_unknown_model_is_reported_precisely() -> None:
    exc = AIProviderError(
        "AI provider returned an unexpected HTTP 400.",
        details={"status": 400, "body": '{"error":{"message":"Model Not Exist"}}'},
    )
    translated = _translate_deepseek_error(exc)
    assert translated.code == "ai_model_not_found"


def test_other_errors_pass_through_unchanged() -> None:
    exc = AIProviderError("boom", details={"status": 500})
    assert _translate_deepseek_error(exc) is exc


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
def test_registry_lists_the_four_documented_providers() -> None:
    assert set(ids()) == {"deepseek", "openai_compatible", "ollama", "offline"}


def test_deepseek_is_the_first_and_default_provider() -> None:
    assert PROVIDERS[0].id == "deepseek"
    assert PROVIDERS[0].default_model == "deepseek-flash"
    assert PROVIDERS[0].requires_api_key is True


def test_deepseek_model_labels_are_human_readable() -> None:
    deepseek = get("deepseek")
    assert deepseek is not None
    labels = {m.id: m.label for m in deepseek.models}
    assert labels["deepseek-flash"] == "DeepSeek V4.1 Flash"
    assert "deepseek-v4-pro" in labels


def test_known_models_are_exposed_by_the_registry() -> None:
    registry_ids = {m.id for m in (get("deepseek").models if get("deepseek") else [])}
    assert registry_ids == {m["id"] for m in KNOWN_MODELS}


def test_catalog_is_serialisable_and_complete() -> None:
    entries = catalog()
    assert len(entries) == 4
    for entry in entries:
        assert entry["id"] and entry["label"] and entry["kind"]
        assert isinstance(entry["enabled"], bool)
        assert isinstance(entry["configured"], bool)
        assert isinstance(entry["models"], list) and entry["models"]


def test_unknown_provider_lookup_returns_none() -> None:
    assert get("not-a-provider") is None


def test_registry_lookup_is_case_insensitive() -> None:
    assert get("DeepSeek") is not None


# --------------------------------------------------------------------------- #
# configuration safety
# --------------------------------------------------------------------------- #
def test_mask_api_key_never_reveals_the_tail() -> None:
    """Only the vendor prefix and the length may be exposed.

    The sample is a synthetic key with the real shape (``sk-`` + 32 hex chars).
    A real credential must never appear in test source, documentation, fixtures
    or screenshots — this test itself is proof that the rule is followed.
    """
    secret = "sk-0123456789abcdef0123456789abcdef"  # allowlist secret
    masked = mask_api_key(secret)
    assert masked == "sk-****(len=35)"
    assert secret[-4:] not in masked
    assert secret[3:9] not in masked


def test_mask_api_key_handles_empty_and_none() -> None:
    assert mask_api_key("") == ""
    assert mask_api_key(None) == ""
    assert mask_api_key("   ") == ""


def test_mask_api_key_handles_a_key_without_a_prefix() -> None:
    assert mask_api_key("abcdef123456").startswith("****")


def test_overridable_fields_are_explicit() -> None:
    assert "provider" in OVERRIDABLE
    assert "api_key" in OVERRIDABLE
    assert "enabled_providers" in OVERRIDABLE

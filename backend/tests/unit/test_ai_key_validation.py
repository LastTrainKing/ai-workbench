"""A pasted API key must be refused where it is typed, and never reach the HTTP client.

The Agent page showed the user this, and nothing else:

    UnicodeEncodeError: 'ascii' codec can't encode character '\\u4e3a' in position 10

The key stored for that account was ``key为sk-2bed0f3d...`` — the whole line the user had
copied, Chinese word included. ``httpx`` encodes header values as ASCII and raised while
*constructing* the client, so the failure surfaced in the middle of an Agent run and named
neither the key nor the setting it came from. The page still rendered, which is why it read
as a broken Agent rather than a bad key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai import runtime_config
from app.ai.providers import factory
from app.ai.providers.openai_compatible import OpenAICompatibleProvider
from app.ai.runtime_config import save
from app.core.errors import AIAuthError, ValidationError

OWNER = "11111111-1111-1111-1111-111111111111"

#: What actually ended up in the account's config file: the user's whole line, not the key.
#: The key half is a placeholder — the value that broke is the shape of it, not the secret.
PASTED_LINE = "key为sk-00000000000000000000000000000000"


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Keep every write inside the test's own directory, and forget what the last test set."""
    monkeypatch.setattr(runtime_config, "CONFIG_PATH", tmp_path / "ai-config.json")
    monkeypatch.setattr(runtime_config, "CONFIG_DIR", tmp_path / "ai-config")
    runtime_config._cache.clear()
    factory._llm_cache.clear()
    runtime_config.set_current_owner(None)
    yield
    runtime_config.set_current_owner(None)


def _account_file() -> Path:
    return runtime_config.CONFIG_DIR / f"{OWNER}.json"


def test_a_pasted_line_is_refused_and_named() -> None:
    with pytest.raises(ValidationError) as caught:
        save(OWNER, {"provider": "deepseek", "api_key": PASTED_LINE})

    message = str(caught.value)
    # The user sees which character, and where — "第 4 个字符是「为」" is the difference
    # between fixing the key and filing a bug against the Agent page.
    assert "为" in message
    assert "第 4 个字符" in message
    assert "ASCII" in message

    # And nothing was written, so the settings page cannot hold a key that only fails
    # later, somewhere else.
    assert not _account_file().exists()


def test_a_space_inside_a_key_is_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        save(OWNER, {"api_key": "sk-abc 123"})
    assert "空白" in str(caught.value)


def test_the_embedding_key_is_checked_the_same_way() -> None:
    with pytest.raises(ValidationError):
        save(OWNER, {"embedding_api_key": PASTED_LINE})


def test_blanks_around_a_pasted_key_are_trimmed_rather_than_refused() -> None:
    """A newline from the clipboard is unambiguous, so it is removed, not reported."""
    save(OWNER, {"api_key": "  sk-abc123\n"})
    assert runtime_config.load(OWNER)["api_key"] == "sk-abc123"


def test_a_non_ascii_key_already_on_disk_degrades_instead_of_crashing() -> None:
    """A config file written before this check existed, or edited by hand."""
    runtime_config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _account_file().write_text(
        json.dumps(
            {"provider": "deepseek", "model": "deepseek-flash", "api_key": PASTED_LINE}
        ),
        encoding="utf-8",
    )
    runtime_config._cache.clear()
    runtime_config.set_current_owner(OWNER)

    _provider, info = factory.build_llm_provider()

    assert info.degraded is True
    assert "非 ASCII" in info.reason


def test_the_provider_refuses_the_key_before_httpx_ever_sees_it() -> None:
    """The crash, at the layer that raised it.

    ``httpx.Headers`` normalises values to ASCII inside ``AsyncClient.__init__``, so the
    exception came from a constructor called during ``chat()`` — after the run had started.
    """
    with pytest.raises(AIAuthError) as caught:
        OpenAICompatibleProvider(
            base_url="https://api.deepseek.com/v1",
            api_key=PASTED_LINE,
            model="deepseek-flash",
            timeout=30.0,
            max_retries=1,
        )
    assert "非 ASCII" in str(caught.value)

"""Release-freeze regression tests — per-account AI provider configuration.

The confirmed defect: the provider, model, base URL and API key all lived in one file
(``.runtime/ai-config.json``) that every account read *and wrote*. Account A changing the
model changed what account B's next call used; A could point B's prompts and uploaded
documents at an endpoint A controlled, and the bill landed on B's provider account. The
response masked the key, which made it look contained — the effect was not.

The store is now layered: account file → installation file → environment → defaults, and
a request can only ever write its own layer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.ai import runtime_config
from app.ai.runtime_config import (
    clear,
    load,
    resolve,
    save,
    validate_endpoint,
)
from app.core.errors import ValidationError


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the store at a temporary directory.

    Without this the tests would write real per-account files into the developer's
    ``.runtime``, which is exactly the kind of side effect a test must not have.
    """
    install = tmp_path / "ai-config.json"
    account_dir = tmp_path / "ai-config"
    monkeypatch.setattr(runtime_config, "CONFIG_PATH", install)
    monkeypatch.setattr(runtime_config, "CONFIG_DIR", account_dir)
    runtime_config._cache.clear()
    return tmp_path


# ------------------------------------------------------------------- isolation --


#: Credential-shaped, and deliberately marked as synthetic so the repo's own secret
#: scanner can tell it apart from a real key. A test is the one place a value like this
#: gets committed by accident, which is exactly what that guard exists for.
FAKE_KEY = "sk-fake-0000000000-not-a-real-credential"


def test_one_account_cannot_change_another() -> None:
    """The defect, stated as an assertion."""
    save("account-a", {"model": "model-chosen-by-a"})

    assert resolve("account-a").model == "model-chosen-by-a"
    # B is untouched.
    assert resolve("account-b").model != "model-chosen-by-a"


def test_an_accounts_key_is_not_visible_to_another() -> None:
    save("account-a", {"api_key": FAKE_KEY, "provider": "openai_compatible"})

    a = resolve("account-a")
    b = resolve("account-b")

    assert a.api_key == FAKE_KEY
    assert a.api_key_set is True
    assert b.api_key == ""
    assert b.api_key_set is False


def test_reset_only_clears_the_calling_account() -> None:
    save("account-a", {"model": "model-a"})
    save("account-b", {"model": "model-b"})

    clear("account-a")

    assert resolve("account-a").model != "model-a"
    assert resolve("account-b").model == "model-b"


def test_writes_are_refused_at_installation_scope() -> None:
    """A request must not be able to change what every account falls back to."""
    with pytest.raises(ValidationError):
        save(None, {"model": "machine-wide"})
    with pytest.raises(ValidationError):
        clear(None)


def test_installation_file_is_read_as_a_fallback(_isolated_store: Path) -> None:
    """An existing install's settings must survive the upgrade."""
    runtime_config.CONFIG_PATH.write_text('{"model": "install-level-model"}', encoding="utf-8")
    runtime_config._cache.clear()

    assert resolve("account-a").model == "install-level-model"
    assert resolve("account-a").sources.get("model") == "install"


def test_account_layer_wins_over_the_installation_layer() -> None:
    runtime_config.CONFIG_PATH.write_text('{"model": "install-level-model"}', encoding="utf-8")
    runtime_config._cache.clear()
    save("account-a", {"model": "account-level-model"})

    assert resolve("account-a").model == "account-level-model"
    assert resolve("account-a").sources.get("model") == "account"
    # B still sees the installation's value, not A's.
    assert resolve("account-b").model == "install-level-model"


def test_owner_id_is_sanitised_against_traversal() -> None:
    """The owner id arrives as a string; a separator in it must not reach another file."""
    save("../../escape", {"model": "x"})
    written = list(runtime_config.CONFIG_DIR.glob("*.json"))
    assert len(written) == 1
    assert written[0].parent == runtime_config.CONFIG_DIR
    assert ".." not in written[0].name


def test_unknown_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        save("account-a", {"not_a_field": 1})


def test_load_returns_only_the_named_layer() -> None:
    runtime_config.CONFIG_PATH.write_text('{"model": "install-level-model"}', encoding="utf-8")
    runtime_config._cache.clear()
    save("account-a", {"temperature": 0.9})

    assert load(None) == {"model": "install-level-model"}
    account = load("account-a")
    assert account["temperature"] == 0.9
    assert "model" not in account


# --------------------------------------------------------------- url validation --


@pytest.mark.parametrize(
    "url",
    [
        "https://api.deepseek.com",
        "https://api.openai.com/v1",
        "http://127.0.0.1:11434",  # a local Ollama, which has no certificate
        "http://localhost:8000/v1",
    ],
)
def test_acceptable_endpoints(url: str) -> None:
    assert validate_endpoint(url) == url


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("ftp://example.com", "not an http client's scheme"),
        ("file:///etc/passwd", "local file read"),
        ("gopher://example.com", "arbitrary protocol"),
        ("https://user:pass@api.example.com", "credentials embedded in the URL"),
        ("http://api.deepseek.com", "remote over plain http sends the key in clear text"),
        ("https://api.deepseek.com?x=1", "a base URL is a prefix, not a request"),
        ("https://api.deepseek.com#frag", "fragment"),
        ("https://", "no host"),
    ],
)
def test_rejected_endpoints(url: str, why: str) -> None:
    with pytest.raises(ValidationError):
        validate_endpoint(url)


def test_saving_a_bad_base_url_is_refused() -> None:
    with pytest.raises(ValidationError):
        save("account-a", {"base_url": "http://evil.example.com"})
    with pytest.raises(ValidationError):
        save("account-a", {"embedding_base_url": "file:///etc/passwd"})


def test_masked_form_never_carries_the_key() -> None:
    save("account-a", {"api_key": FAKE_KEY})
    safe: dict[str, Any] = resolve("account-a").safe()

    assert safe["api_key_set"] is True
    blob = repr(safe)
    assert FAKE_KEY not in blob
    # The hint keeps the vendor prefix and the length, and nothing else.
    assert "sk-" in safe["api_key_hint"]
    assert "not-a-real-credential" not in safe["api_key_hint"]

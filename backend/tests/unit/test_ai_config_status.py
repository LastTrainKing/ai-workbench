"""What state is the AI configuration in, and who is reading it.

The bug these exist for: a key was saved for the account, the settings page displayed its
mask, and the same page reported **「离线模式 是（未配置 Key）」** — because that one row
read `settings.is_offline_ai`, which resolves against the *installation* layer whenever no
owner is bound, while the fields beside it read the account's file. Two layers, two
answers, one screen.

So the tests are about two things: the ladder of states is right, and every reader reads
the same layer. The configuration directory is redirected at a temporary path — nothing
here touches the machine's real configuration, and no request leaves the process.
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DESKTOP = REPO / "desktop-native"
if str(DESKTOP) not in sys.path:
    sys.path.insert(0, str(DESKTOP))

# See the long note in `test_native_bridge_session.py`: a missing Qt runtime is a *collection*
# error here, and one collection error stops the entire suite. Skip the module instead.
pytest.importorskip("PySide6", reason="没有可用的 Qt 运行时，无法测试桥接层")

from awbnative import resources

resources.configure(create=False)

from awbnative.bridge import ServiceBridge

OWNER = uuid.UUID("de9ea2c2-a49c-40cf-890e-4e59989bb1cf")


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the configuration store at a temporary directory.

    Both paths are patched, not just the directory: `_path_for` returns `CONFIG_PATH`
    itself for the installation layer, so patching only the directory would leave the
    install-level file pointing at the real runtime root.
    """
    from app.ai import runtime_config

    monkeypatch.setattr(runtime_config, "CONFIG_DIR", tmp_path / "ai-config")
    monkeypatch.setattr(runtime_config, "CONFIG_PATH", tmp_path / "ai-config.json")
    runtime_config._cache.clear()
    yield tmp_path / "ai-config"
    runtime_config._cache.clear()


def _save(owner: uuid.UUID | None = OWNER, **patch: object) -> None:
    from app.ai.runtime_config import save

    save(owner, dict(patch))


def _state(owner: uuid.UUID | None = OWNER) -> str:
    from app.ai.runtime_config import ai_status

    return ai_status(owner)["state"]


# --------------------------------------------------------------------------- #
# The ladder
# --------------------------------------------------------------------------- #
def test_a_fresh_install_is_the_offline_engine(config: Path) -> None:
    """Not 未配置.

    With nothing saved, the provider that actually answers is the offline engine, and that
    is a working configuration rather than a missing one. Calling it 未配置 would tell
    somebody their install is broken when it is doing exactly what it does out of the box.
    """
    assert _state() == "offline"


def test_a_saved_configuration_is_untested_not_connected(config: Path) -> None:
    """The state that was missing.

    Without it, "the key is saved" had to be displayed as either 未配置 (false) or as a
    connection that nobody had made (a claim the program cannot support).
    """
    _save(
        provider="deepseek",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        api_key="sk-test-0000000000000000000000000000",
    )
    assert _state() == "configured_untested"


def test_a_missing_key_is_not_configured(config: Path) -> None:
    _save(provider="deepseek", model="deepseek-flash", base_url="https://api.deepseek.com")
    assert _state() == "not_configured"


def test_the_offline_provider_is_offline_rather_than_broken(config: Path) -> None:
    _save(provider="offline", model="heuristic-extractive-v1")
    assert _state() == "offline"


def test_a_successful_check_makes_it_connected(config: Path) -> None:
    from app.ai.runtime_config import record_check

    _save(
        provider="deepseek",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        api_key="sk-test-0000000000000000000000000000",
    )
    record_check(
        OWNER,
        {
            "ok": True,
            "provider": "deepseek",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
            "latency_ms": 420,
        },
    )
    assert _state() == "connected"


def test_a_refused_check_makes_it_failed_and_keeps_the_reason(config: Path) -> None:
    from app.ai.runtime_config import ai_status, record_check

    _save(
        provider="deepseek",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        api_key="sk-test-0000000000000000000000000000",
    )
    record_check(
        OWNER,
        {
            "ok": False,
            "provider": "deepseek",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
            "error_code": "ai_auth_error",
            "error_message": "401 Unauthorized",
        },
    )
    status = ai_status(OWNER)
    assert status["state"] == "failed"
    assert status["check"]["error_code"] == "ai_auth_error"


def test_changing_the_model_makes_an_old_check_stale(config: Path) -> None:
    """A green light that outlives the configuration it describes is a lie about the
    configuration that is in effect now."""
    from app.ai.runtime_config import record_check

    _save(
        provider="deepseek",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        api_key="sk-test-0000000000000000000000000000",
    )
    record_check(
        OWNER,
        {
            "ok": True,
            "provider": "deepseek",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
        },
    )
    assert _state() == "connected"

    _save(model="deepseek-chat")
    assert _state() == "configured_untested"


def test_clearing_the_configuration_clears_the_check_with_it(config: Path) -> None:
    from app.ai.runtime_config import clear, record_check

    _save(
        provider="deepseek",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        api_key="sk-test-0000000000000000000000000000",
    )
    record_check(
        OWNER,
        {
            "ok": True,
            "provider": "deepseek",
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
        },
    )
    clear(OWNER)
    assert _state() == "offline"


def test_the_key_never_leaves_the_store_unmasked(config: Path) -> None:
    from app.ai.runtime_config import ai_status, resolve

    _save(
        provider="deepseek",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        api_key="sk-0123456789abcdef0123456789abcdef",
    )
    safe = resolve(OWNER).safe()
    assert safe["api_key_set"] is True
    assert "0123456789abcdef" not in safe["api_key_hint"]
    assert "api_key" not in safe
    assert "0123456789abcdef" not in str(ai_status(OWNER))


# --------------------------------------------------------------------------- #
# One layer, every reader
# --------------------------------------------------------------------------- #
def test_resolving_without_an_owner_does_not_see_an_accounts_key(config: Path) -> None:
    """**The root cause, in one assertion.**

    `resolve()` falls back to the installation layer when no owner is bound, and a key
    saved for an account does not exist there. Anything that reported a state without
    binding the owner therefore reported "not configured" no matter what the account had
    saved — which is what the settings page did.
    """
    _save(
        provider="deepseek",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        api_key="sk-test-0000000000000000000000000000",
    )
    from app.ai.runtime_config import resolve

    assert resolve(OWNER).api_key_set is True
    assert resolve(None).api_key_set is False


def test_the_provider_a_call_uses_is_the_one_that_was_saved(config: Path) -> None:
    """The chat and the agent build their provider from the same store the page writes to."""
    from app.ai.providers.factory import build_llm_provider
    from app.ai.runtime_config import set_current_owner

    _save(
        provider="deepseek",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        api_key="sk-test-0000000000000000000000000000",
    )

    set_current_owner(str(OWNER))
    try:
        _provider, info = build_llm_provider()
        assert info.kind == "deepseek"
        assert info.model == "deepseek-flash"
        assert info.api_key_set is True
    finally:
        set_current_owner(None)


# --------------------------------------------------------------------------- #
# The bridge slot that reported it wrong
# --------------------------------------------------------------------------- #
def _bridge() -> ServiceBridge:
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication([])
    instance = ServiceBridge(owner_id=None)
    instance.record_startup({}, {"ready": True})
    return instance


def _call(bridge: ServiceBridge, slot: str, *args: object) -> dict:
    """Drive a slot the way QML does and return the parsed payload."""
    import json

    received: dict[str, str] = {}
    bridge.completed.connect(lambda rid, text: received.setdefault(rid, text))
    bridge.failed.connect(lambda rid, message, code: received.setdefault(rid, f"!{message}"))
    request_id = f"{slot}-{time.monotonic_ns()}"
    getattr(bridge, slot)(request_id, *args)
    # **Pump the Qt event loop.** The runner answers on its own thread and the bridge
    # forwards the result through a queued connection, so it is delivered by the event loop
    # of the thread that built the bridge. Sleeping alone waits forever: the call has
    # finished (`inflight` is zero) and the signal is sitting in a queue nobody drains.
    from PySide6.QtCore import QCoreApplication

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and request_id not in received:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    assert request_id in received, f"{slot} never answered"
    return json.loads(received[request_id])


def test_the_runtime_info_slot_reports_the_accounts_provider(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression: this slot reported `offline: true` and `APP_ENV`'s provider.

    It never bound the owner, so it resolved against the installation layer — on the same
    screen where the fields, which do bind it, showed a saved key. Against the old code
    `offline` is True here and this fails.
    """
    _save(
        provider="deepseek",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        api_key="sk-test-0000000000000000000000000000",
    )

    bridge = _bridge()
    try:

        async def fake_owner(self: ServiceBridge) -> uuid.UUID:
            return OWNER

        monkeypatch.setattr(ServiceBridge, "_owner", fake_owner)
        payload = _call(bridge, "loadRuntimeInfo")
    finally:
        bridge._runner.shutdown()

    assert payload["offline"] is False, (
        "the runtime info slot reported the offline engine for an account that has a key — "
        "this is the 离线模式：是（未配置 Key） bug"
    )
    assert payload["ai_provider"] == "deepseek"
    assert payload["ai_model"] == "deepseek-flash"
    assert payload["status_state"] == "configured_untested"


def test_the_config_slot_carries_the_state_so_the_page_need_not_guess(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save(
        provider="deepseek",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        api_key="sk-test-0000000000000000000000000000",
    )

    bridge = _bridge()
    try:

        async def fake_owner(self: ServiceBridge) -> uuid.UUID:
            return OWNER

        monkeypatch.setattr(ServiceBridge, "_owner", fake_owner)
        payload = _call(bridge, "loadAiConfig")
    finally:
        bridge._runner.shutdown()

    assert payload["status"]["state"] == "configured_untested"
    assert payload["effective"]["api_key_set"] is True
    assert "sk-test" not in str(payload), "the key value crossed into the interface"


def test_saving_then_reading_back_gives_the_same_state(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Save, re-open the page, get the same answer — the acceptance step, as a test."""
    import json as _json

    bridge = _bridge()
    try:

        async def fake_owner(self: ServiceBridge) -> uuid.UUID:
            return OWNER

        monkeypatch.setattr(ServiceBridge, "_owner", fake_owner)
        _call(
            bridge,
            "saveAiConfig",
            _json.dumps(
                {
                    "provider": "deepseek",
                    "model": "deepseek-flash",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "sk-test-0000000000000000000000000000",
                }
            ),
        )
        reopened = _call(bridge, "loadAiConfig")
    finally:
        bridge._runner.shutdown()

    assert reopened["status"]["state"] == "configured_untested"
    assert reopened["effective"]["provider"] == "deepseek"
    assert reopened["effective"]["api_key_set"] is True
    hint = reopened["effective"]["api_key_hint"]
    assert hint.startswith("sk-") and hint.endswith(")") and "len=" in hint
    assert "0000" not in hint, "the key value must not be recoverable from the mask"

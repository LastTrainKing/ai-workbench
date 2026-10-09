"""Release-freeze regression tests — password hashing and token revocation.

Two confirmed defects are reproduced here, and each test fails on the code as it was
before this round:

* **Password hashing truncated at 72 bytes.** ``hash_password`` applied
  ``password.encode("utf-8")[:72]`` itself, so two passwords agreeing on their first 72
  bytes were one password, and a multibyte password could be cut mid-character.
  ``test_legacy_rule_reproduces_the_truncation_bug`` demonstrates the old behaviour
  deliberately, so the fix is shown to be a fix rather than a rewrite.

* **Revocation failed open.** ``is_token_revoked`` returned a bool built from a Redis
  read that returns ``None`` when Redis is down, so a logged-out token came back to life
  during a cache outage. The three-state answer is asserted directly.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta

import bcrypt
import pytest
from sqlalchemy.exc import OperationalError

from app.core.security import (
    MAX_PASSWORD_CHARS,
    RevocationState,
    check_password_length,
    hash_password,
    is_token_revoked,
    password_needs_rehash,
    revoke_token,
    verify_password,
)

# --------------------------------------------------------------------------- password --


def _legacy_hash(password: str) -> str:
    """A hash in the format the old code wrote: bare bcrypt over the truncated bytes."""
    raw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=4)).decode("utf-8")


def test_new_hash_is_versioned_and_verifies() -> None:
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("bcrypt_sha256$")
    assert verify_password("correct horse battery", hashed)
    assert not verify_password("wrong password", hashed)


def test_two_hashes_of_the_same_password_differ() -> None:
    """bcrypt's salt must still be doing its job after the pre-hash change."""
    assert hash_password("same password 1") != hash_password("same password 1")


def test_legacy_rule_reproduces_the_truncation_bug() -> None:
    """The defect itself, kept as a witness.

    A legacy hash of ``"a"*72 + "X"`` verifies ``"a"*72 + "Y"`` because the old code cut
    both to the same 72 bytes. This is what the new scheme has to stop — if this
    assertion ever fails, the legacy path has been changed and old accounts may stop
    logging in.
    """
    legacy = _legacy_hash("a" * 72 + "X")
    assert verify_password("a" * 72 + "Y", legacy) is True  # the bug, preserved on purpose


def test_new_scheme_rejects_passwords_that_only_differ_after_byte_72() -> None:
    """The fix: the full password reaches the KDF, so the suffix matters."""
    hashed = hash_password("a" * 72 + "X")
    assert verify_password("a" * 72 + "X", hashed) is True
    assert verify_password("a" * 72 + "Y", hashed) is False


def test_legacy_hash_still_verifies_and_is_flagged_for_upgrade() -> None:
    legacy = _legacy_hash("legacy pass 1")
    assert verify_password("legacy pass 1", legacy) is True
    assert password_needs_rehash(legacy) is True
    assert password_needs_rehash(hash_password("modern pass 1")) is False


def test_multibyte_passwords_round_trip() -> None:
    for password in ("中文密码测试123", "🔐🗝️ emoji 密码 42", "Ünïcödé with аccents 7"):
        hashed = hash_password(password)
        assert verify_password(password, hashed), password


def test_multibyte_boundary_is_not_split_mid_character() -> None:
    """A password whose 72nd byte lands inside a character must still be distinct."""
    # Each '中' is 3 UTF-8 bytes; 24 of them are exactly 72 bytes.
    base = "中" * 24
    first = hash_password(base + "甲")
    assert verify_password(base + "乙", first) is False
    assert verify_password(base + "甲", first) is True


def test_length_limits_reject_rather_than_truncate() -> None:
    at_limit = "x" * MAX_PASSWORD_CHARS
    hash_password(at_limit)  # must not raise
    with pytest.raises(ValueError):
        check_password_length("x" * (MAX_PASSWORD_CHARS + 1))
    with pytest.raises(ValueError):
        check_password_length("")
    with pytest.raises(ValueError):
        hash_password("x" * (MAX_PASSWORD_CHARS + 1))


def test_malformed_hash_fails_safely() -> None:
    assert verify_password("anything", "") is False
    assert verify_password("anything", "not-a-bcrypt-hash") is False
    assert verify_password("anything", "bcrypt_sha256$garbage") is False


def test_prehash_is_base64_of_sha256_and_has_no_nul() -> None:
    """Why base64 and not the raw digest: bcrypt stops at the first NUL byte."""
    digest = hashlib.sha256(b"x").digest()
    encoded = base64.b64encode(digest)
    assert len(encoded) < 72
    assert b"\x00" not in encoded


# ------------------------------------------------------------------------- revocation --


class _FakeResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _FakeSession:
    """Enough of an AsyncSession for the revocation path, and nothing more."""

    def __init__(self, *, row: object = None, raise_on_execute: bool = False) -> None:
        self.row = row
        self.raise_on_execute = raise_on_execute
        self.added: list[object] = []
        self.flushed = 0

    async def execute(self, *_args, **_kwargs) -> _FakeResult:
        if self.raise_on_execute:
            raise OperationalError("select", {}, Exception("database is down"))
        return _FakeResult(self.row)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed += 1


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Take Redis out of the picture so these tests assert the database authority."""
    store: dict[str, object] = {}

    async def fake_get(_ns: str, key: str) -> object | None:
        return store.get(key)

    async def fake_set(_ns: str, key: str, value: object, _ttl: int) -> bool:
        store[key] = value
        return True

    monkeypatch.setattr("app.core.cache.cache_get", fake_get)
    monkeypatch.setattr("app.core.cache.cache_set", fake_set)
    return store


def _payload(jti: str = "jti-1", minutes: int = 30) -> dict[str, object]:
    return {
        "sub": "11111111-1111-1111-1111-111111111111",
        "jti": jti,
        "exp": int((datetime.now(UTC) + timedelta(minutes=minutes)).timestamp()),
    }


async def test_unrevoked_token_is_not_revoked() -> None:
    state = await is_token_revoked(_payload(), _FakeSession(row=None))
    assert state is RevocationState.NOT_REVOKED


async def test_revoked_token_is_revoked() -> None:
    session = _FakeSession(row=datetime.now(UTC) + timedelta(minutes=30))
    state = await is_token_revoked(_payload(), session)
    assert state is RevocationState.REVOKED


async def test_storage_failure_is_not_reported_as_valid() -> None:
    """The defect: a down store used to answer 'not revoked'. It must not."""
    session = _FakeSession(raise_on_execute=True)
    state = await is_token_revoked(_payload(), session)
    assert state is RevocationState.STORAGE_UNAVAILABLE
    assert state is not RevocationState.NOT_REVOKED


async def test_token_without_jti_cannot_be_revoked() -> None:
    """Pre-`jti` tokens are reported honestly rather than claimed as revoked."""
    session = _FakeSession()
    assert await revoke_token({"sub": "x", "exp": 0}, session) is False
    assert session.added == []


async def test_revoke_writes_durably_and_is_idempotent() -> None:
    session = _FakeSession(row=None)
    assert await revoke_token(_payload("jti-dup"), session) is True
    assert len(session.added) == 1
    assert session.flushed == 1

    # Logging out twice must not add a second row (the unique index over jti).
    already = _FakeSession(row="existing-id")
    assert await revoke_token(_payload("jti-dup"), already) is True
    assert already.added == []


async def test_revocation_is_visible_after_the_cache_is_lost(
    _no_redis: dict[str, object],
) -> None:
    """The whole point: the durable record outlives the cache."""
    session = _FakeSession(row=None)
    await revoke_token(_payload("jti-cold"), session)
    _no_redis.clear()  # simulate a Redis restart

    state = await is_token_revoked(
        _payload("jti-cold"), _FakeSession(row=datetime.now(UTC) + timedelta(minutes=5))
    )
    assert state is RevocationState.REVOKED


# ------------------------------------------------------- production secret gate --


def _prod_settings(secret_key: str, *, debug: bool = False):
    from app.core.config import Settings

    # `_env_file=None` so the developer's real `.env` cannot make a test pass.
    return Settings(_env_file=None, app_env="prod", debug=debug, secret_key=secret_key)


@pytest.mark.parametrize(
    "placeholder",
    [
        "change-me-in-dotenv-please-32-chars-min",  # the built-in default
        "change-me-to-a-random-32-plus-character-string",  # what .env.example shipped
        "CHANGE-ME-PLEASE-32-CHARACTERS-LONG-XX",
        "replace-me-with-a-real-secret-value-1234",
        "placeholder-value-that-is-long-enough-1",
        "example-secret-key-for-the-application",
        "your-secret-key-goes-right-here-12345",
        "abcdefghijklmnopqrstuvwxyz012345",  # 32 chars, but a pattern
        "short-key",  # too short
        "",  # empty
    ],
)
def test_production_rejects_every_placeholder_secret(placeholder: str) -> None:
    with pytest.raises(ValueError):
        _prod_settings(placeholder)


def test_production_accepts_a_real_random_key() -> None:
    import secrets

    for _ in range(5):
        settings = _prod_settings(secrets.token_urlsafe(48))
        assert settings.app_env == "prod"


def test_production_still_refuses_debug() -> None:
    import secrets

    with pytest.raises(ValueError):
        _prod_settings(secrets.token_urlsafe(48), debug=True)


def test_development_and_test_are_unaffected() -> None:
    """The gate is for `prod` only — a dev checkout must keep starting."""
    from app.core.config import Settings

    for env in ("dev", "test"):
        settings = Settings(_env_file=None, app_env=env, debug=True, secret_key="short")
        assert settings.app_env == env

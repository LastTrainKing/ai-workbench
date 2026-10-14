"""Password hashing and JWT issuing / verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import settings
from app.core.errors import AuthenticationError
from app.core.logging import get_logger

logger = get_logger(__name__)

ALGORITHM = "HS256"
_BCRYPT_ROUNDS = 12

# --------------------------------------------------------------- password hashing --
#
# bcrypt silently ignores everything past 72 bytes. The previous code applied that
# limit itself — ``password.encode("utf-8")[:72]`` — and the consequences were not
# theoretical:
#
#   * a password with multibyte characters could be cut **in the middle of a
#     character**, so two different strings could reduce to the same 72 bytes;
#   * two passwords that agree on their first 72 bytes authenticated as the same
#     account. `"a"*72 + "X"` and `"a"*72 + "Y"` were one password.
#
# The scheme is now versioned and pre-hashes the **whole** password to a fixed-size
# digest before bcrypt sees it. bcrypt still salts and stretches; it just stretches a
# 44-byte base64 digest instead of raw text, so there is no ceiling to truncate at.
#
# The stored value carries a ``bcrypt_sha256$`` prefix. A bare ``$2b$…`` value is a
# hash written before this change: it still verifies (using the old, truncating rule,
# which is the only rule that can reproduce it) and is upgraded to the new format the
# next time the owner logs in — see ``password_needs_rehash``.
_HASH_PREFIX = "bcrypt_sha256$"
_MAX_PASSWORD_BYTES = 72  # bcrypt's hard limit; only the legacy path can reach it

#: Ceiling on the *input*, enforced before hashing so an oversized password is
#: rejected rather than quietly reduced. Far above any real password, small enough
#: that hashing stays cheap. Register and login must agree on this number.
MAX_PASSWORD_CHARS = 128
#: A second ceiling in bytes, because 128 astral-plane characters are 512 bytes and a
#: caller can send more than 128 of them if a validator is ever bypassed.
MAX_PASSWORD_UTF8_BYTES = 512

#: A valid hash of a value nobody knows, used to spend the same bcrypt work on a login
#: for a **non-existent** account as on a real one. Without it, "no such user" returns
#: faster than "wrong password" and the difference is an account-existence oracle.
DUMMY_PASSWORD_HASH = (
    "bcrypt_sha256$$2b$12$3Q5Ixs2IO3nQGdC4jLml3OFgnqzg.q2QZeTBBGzTp2gPx8Ez3Tzq2"
)


def check_password_length(password: str) -> None:
    """Reject an out-of-range password instead of truncating it.

    Raises ``ValueError`` so the schema layer can turn it into a 422. Whitespace is
    *not* stripped here: a password is used exactly as typed, and silently trimming it
    would make the stored value disagree with what the user believes they set.
    """
    if not password:
        raise ValueError("password must not be empty")
    if len(password) > MAX_PASSWORD_CHARS:
        raise ValueError(f"password must be at most {MAX_PASSWORD_CHARS} characters")
    if len(password.encode("utf-8")) > MAX_PASSWORD_UTF8_BYTES:
        raise ValueError("password is too long when encoded as UTF-8")


def _prehash(password: str) -> bytes:
    """SHA-256 → base64. Always 44 ASCII bytes, always under bcrypt's ceiling.

    Base64 and not the raw digest: a raw SHA-256 output can contain a NUL byte, and
    bcrypt stops at the first NUL — which would reintroduce exactly the truncation this
    is here to remove.
    """
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    check_password_length(password)
    hashed = bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS))
    return _HASH_PREFIX + hashed.decode("utf-8")


def _verify_legacy(password: str, hashed: str) -> bool:
    """The old rule, kept only so hashes written before the change still work."""
    raw = password.encode("utf-8")[:_MAX_PASSWORD_BYTES]
    return bcrypt.checkpw(raw, hashed.encode("utf-8"))


def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        if hashed.startswith(_HASH_PREFIX):
            return bcrypt.checkpw(_prehash(password), hashed[len(_HASH_PREFIX):].encode("utf-8"))
        return _verify_legacy(password, hashed)
    except (ValueError, TypeError):
        # Malformed hash in the database -> treat as a failed login, never crash.
        return False


def password_needs_rehash(hashed: str) -> bool:
    """True when ``hashed`` predates the current scheme and should be upgraded.

    Called after a **successful** verification only: that is the one moment the
    plaintext is available, and rewriting the row then is what lets the old format be
    retired without resetting anybody's account.
    """
    return bool(hashed) and not hashed.startswith(_HASH_PREFIX)


def create_access_token(
    subject: str,
    *,
    extra: dict[str, Any] | None = None,
    expires_minutes: int | None = None,
    token_type: Literal["access"] = "access",
) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=expires_minutes or settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "typ": token_type,
        "jti": secrets.token_urlsafe(12),
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
    return token, expire


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired", code="token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid authentication token", code="token_invalid") from exc


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def generate_api_token(prefix: str = "awb") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"

# ------------------------------------------------------------- token revocation --
#
# A JWT is stateless: it is valid until it expires, and nothing the server does can
# change that. The logout endpoint used to acknowledge the action and rely on the client
# throwing the token away — which is what a browser does and what an attacker does not.
#
# The payload already carries a `jti` (issued in `create_access_token`), so the fix is a
# revocation list keyed on it. Redis rather than a process-local set: two workers, or one
# worker that restarted, must not disagree about whether a token is still good.
#
# The entry expires at the same moment the token does. A token that has expired on its own
# does not need a revocation entry, and keeping one forever is how a list like this grows
# without bound.

_REVOCATION_NS = "auth:revoked"


class RevocationState(StrEnum):
    """Three answers, not two.

    ``is_token_revoked`` used to return a bool, and when Redis was unreachable
    ``cache_get`` returned ``None`` — which the function read as "not revoked". A logout
    therefore stopped meaning anything for as long as the cache was down, and the token
    kept working for the rest of its 24 hours. "No record of a revocation" and "could not
    find out" are different facts, and the difference has to exist somewhere. It exists
    here, and the caller decides what to do about it.
    """

    REVOKED = "revoked"
    NOT_REVOKED = "not_revoked"
    STORAGE_UNAVAILABLE = "storage_unavailable"


async def revoke_token(payload: dict[str, Any], session: Any) -> bool:
    """Make this token unusable from now on.

    Durable first, cache second. The database row is what makes the revocation survive a
    Redis restart; the Redis entry is only a fast path in front of it. Written in that
    order deliberately: a crash between the two leaves the token revoked (correct) rather
    than un-revoked (the bug this replaces).
    """
    from sqlalchemy import select

    from app.core.cache import cache_set
    from app.models.user import RevokedToken

    jti = str(payload.get("jti") or "")
    if not jti:
        # Tokens issued before `jti` existed cannot be revoked individually. Reported as
        # not-revoked rather than silently claiming success.
        return False

    expires_at = datetime.fromtimestamp(int(payload.get("exp") or 0), UTC)

    # Idempotent: logging out twice is not an error, and must not raise on the unique
    # index over `jti`.
    existing = (
        await session.execute(select(RevokedToken.id).where(RevokedToken.jti == jti))
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            RevokedToken(
                jti=jti,
                subject=str(payload.get("sub") or ""),
                expires_at=expires_at,
            )
        )
        await session.flush()

    ttl = max(int((expires_at - datetime.now(UTC)).total_seconds()), 1)
    await cache_set(_REVOCATION_NS, jti, True, ttl)
    return True


async def is_token_revoked(payload: dict[str, Any], session: Any) -> RevocationState:
    """Whether this token has been logged out — and whether we can even tell.

    The cache is checked first because it is the common case and it is cheap. A **hit**
    is decisive: only a revocation ever writes to this namespace. A **miss** is not
    decisive, because it is also what a Redis outage looks like, so the durable store is
    asked. If that fails too the answer is ``STORAGE_UNAVAILABLE`` and the caller fails
    closed — an outage must not silently re-enable a token someone logged out.

    This cannot lock anyone out permanently: nothing is written on the failure path, so
    once storage returns, tokens that were never revoked verify normally again.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from app.core.cache import cache_get, cache_set
    from app.models.user import RevokedToken

    jti = str(payload.get("jti") or "")
    if not jti:
        return RevocationState.NOT_REVOKED

    if await cache_get(_REVOCATION_NS, jti) is not None:
        return RevocationState.REVOKED

    try:
        expires_at = (
            await session.execute(
                select(RevokedToken.expires_at).where(RevokedToken.jti == jti)
            )
        ).scalar_one_or_none()
    except SQLAlchemyError as exc:
        logger.error(
            "revocation_store_unavailable",
            extra={"error": f"{type(exc).__name__}: {exc}"},
        )
        return RevocationState.STORAGE_UNAVAILABLE

    if expires_at is None:
        return RevocationState.NOT_REVOKED

    # Repopulate the cache so the next request is a hit.
    ttl = max(int((expires_at - datetime.now(UTC)).total_seconds()), 1)
    await cache_set(_REVOCATION_NS, jti, True, ttl)
    return RevocationState.REVOKED


async def purge_expired_revocations(session: Any) -> int:
    """Delete revocation rows whose token has already expired on its own.

    Called at startup. An expired token is rejected by its signature, so its revocation
    row is dead weight — and without this the table grows with every logout forever.
    """
    from sqlalchemy import delete

    from app.models.user import RevokedToken

    result = await session.execute(
        delete(RevokedToken).where(RevokedToken.expires_at < datetime.now(UTC))
    )
    return int(result.rowcount or 0)

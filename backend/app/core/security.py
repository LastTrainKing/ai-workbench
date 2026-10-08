"""Password hashing and JWT issuing / verification."""

from __future__ import annotations

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import settings
from app.core.errors import AuthenticationError

ALGORITHM = "HS256"
_BCRYPT_ROUNDS = 12
_MAX_PASSWORD_BYTES = 72  # bcrypt hard limit


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")[:_MAX_PASSWORD_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        raw = password.encode("utf-8")[:_MAX_PASSWORD_BYTES]
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed hash in the database -> treat as a failed login, never crash.
        return False


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


async def revoke_token(payload: dict[str, Any]) -> bool:
    """Make this token unusable from now on. Returns whether it was recorded."""
    from app.core.cache import cache_set

    jti = str(payload.get("jti") or "")
    if not jti:
        return False
    expires_at = int(payload.get("exp") or 0)
    ttl = max(expires_at - int(datetime.now(UTC).timestamp()), 1)
    return await cache_set(_REVOCATION_NS, jti, True, ttl)


async def is_token_revoked(payload: dict[str, Any]) -> bool:
    """Has this token been logged out?

    **Fails open, deliberately, and says so.** If Redis is down, `cache_get` returns None
    and every token looks valid. The alternative — failing closed — means one Redis
    hiccup signs everybody out, and the thing being protected is a local desktop
    application whose data is on the same machine. The trade is recorded here rather than
    left for someone to discover.
    """
    from app.core.cache import cache_get

    jti = str(payload.get("jti") or "")
    if not jti:
        return False
    return await cache_get(_REVOCATION_NS, jti) is not None

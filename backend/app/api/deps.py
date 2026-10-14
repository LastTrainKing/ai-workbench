"""Shared FastAPI dependencies."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, InfrastructureError
from app.core.logging import get_logger
from app.core.response import PaginationParams
from app.core.security import RevocationState, decode_access_token, is_token_revoked
from app.infrastructure.database import get_db
from app.models.user import User

logger = get_logger(__name__)

bearer = HTTPBearer(auto_error=False, description="JWT access token")

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: DbSession,
) -> User:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token")
    payload = decode_access_token(credentials.credentials)
    # A token that has been logged out is not accepted, even though its signature is
    # still valid — that is the whole point of a revocation list. The check has three
    # outcomes, and the third one matters: if the revocation store cannot be read we
    # **refuse** the request rather than assuming the token is fine. Failing open here is
    # what let a logged-out token come back to life during a cache outage.
    revocation = await is_token_revoked(payload, session)
    if revocation is RevocationState.REVOKED:
        raise AuthenticationError("This token has been logged out", code="token_revoked")
    if revocation is RevocationState.STORAGE_UNAVAILABLE:
        raise InfrastructureError(
            "无法确认该 Token 是否已撤销（撤销存储不可用），已按安全策略拒绝本次请求",
            code="revocation_storage_unavailable",
        )
    subject = payload.get("sub")
    if not subject:
        raise AuthenticationError("Token is missing the subject claim")
    try:
        user_id = uuid.UUID(str(subject))
    except ValueError as exc:
        raise AuthenticationError("Token subject is not a valid user id") from exc

    user = await session.get(User, user_id)
    if user is None:
        raise AuthenticationError("The account referenced by this token no longer exists")
    if not user.is_active:
        raise AuthenticationError("This account has been disabled")

    # Bind the rest of this request to the account. `resolve()` reads this when it is not
    # told an owner, which is what makes the AI provider configuration the caller's own
    # rather than the machine's — see `app.ai.runtime_config`.
    from app.ai.runtime_config import set_current_owner

    set_current_owner(user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def pagination(
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(20, ge=1, le=200),
) -> PaginationParams:
    return PaginationParams(page=page, page_size=page_size)


Pagination = Annotated[PaginationParams, Depends(pagination)]


async def optional_request_id(
    request: Request,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> str:
    return x_request_id or request.headers.get("X-Request-ID", "")


__all__ = [
    "CurrentUser",
    "DbSession",
    "Pagination",
    "get_current_user",
    "optional_request_id",
    "pagination",
]

"""Shared FastAPI dependencies."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError
from app.core.logging import get_logger
from app.core.response import PaginationParams
from app.core.security import decode_access_token
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

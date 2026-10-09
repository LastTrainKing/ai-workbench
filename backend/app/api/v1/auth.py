"""Authentication and user profile endpoints."""

from __future__ import annotations

import contextlib
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import CurrentUser, DbSession, bearer
from app.core.errors import AuthenticationError
from app.core.response import ok
from app.core.security import decode_access_token, revoke_token
from app.schemas.auth import TokenOut, UserCreate, UserLogin, UserOut, UserUpdate
from app.services.auth_service import AuthService

router = APIRouter(tags=["auth"])


@router.post(
    "/auth/register",
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and receive an access token",
)
async def register(payload: UserCreate, session: DbSession) -> dict:
    user, token, expires = await AuthService(session).register(payload)
    return ok(
        TokenOut(
            access_token=token,
            expires_at=expires,
            user=UserOut.model_validate(user),
        ).model_dump(mode="json"),
        message="账号创建成功",
    )


@router.post("/auth/login", summary="Exchange credentials for an access token")
async def login(payload: UserLogin, session: DbSession) -> dict:
    user, token, expires = await AuthService(session).login(payload.identifier, payload.password)
    return ok(
        TokenOut(
            access_token=token,
            expires_at=expires,
            user=UserOut.model_validate(user),
        ).model_dump(mode="json")
    )


@router.get("/auth/me", summary="Current authenticated user")
async def me(user: CurrentUser) -> dict:
    return ok(UserOut.model_validate(user).model_dump(mode="json"))


@router.post("/auth/logout", summary="Log out, and make this token stop working")
async def logout(
    user: CurrentUser,
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> dict:
    """Revoke this token, rather than trusting the client to discard it.

    It used to acknowledge the action and rely on the browser throwing the token away.
    That is what a browser does and what anyone else does not: the token stayed valid for
    the rest of its 24 hours, and "log out" was a message to the client rather than a
    decision by the server. The `jti` is now recorded as revoked until the token would
    have expired on its own — **in the database**, so the revocation survives a restart of
    the cache it is also written to.
    """
    revoked = False
    if credentials is not None and credentials.credentials:
        with contextlib.suppress(AuthenticationError):
            revoked = await revoke_token(decode_access_token(credentials.credentials), session)
    return ok({"logged_out": True, "username": user.username, "token_revoked": revoked})


user_router = APIRouter(tags=["users"])


@user_router.get("/users/me", summary="Read the current profile")
async def get_profile(user: CurrentUser) -> dict:
    return ok(UserOut.model_validate(user).model_dump(mode="json"))


@user_router.patch("/users/me", summary="Update display name or avatar colour")
async def update_profile(
    payload: UserUpdate, user: CurrentUser, session: DbSession
) -> dict:
    updated = await AuthService(session).update_profile(
        user.id,
        display_name=payload.display_name,
        avatar_color=payload.avatar_color,
    )
    return ok(UserOut.model_validate(updated).model_dump(mode="json"))

"""Authentication and user profile endpoints."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.core.response import ok
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


@router.post("/auth/logout", summary="Client-side logout acknowledgement")
async def logout(user: CurrentUser) -> dict:
    # JWTs are stateless; the client discards the token. The endpoint exists so
    # the frontend has a single place to clear local state and audit the action.
    return ok({"logged_out": True, "username": user.username})


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

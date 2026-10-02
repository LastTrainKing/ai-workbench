"""Authentication service."""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.repositories.ai_repo import ActivityRepository
from app.repositories.base import BaseRepository
from app.schemas.auth import UserCreate

logger = get_logger(__name__)


class UserRepository(BaseRepository[User]):
    model = User

    async def by_identifier(self, identifier: str) -> User | None:
        return await self.get_or_none(
            or_(User.email == identifier.lower(), User.username == identifier)
        )

    async def by_email(self, email: str) -> User | None:
        return await self.get_or_none(User.email == email.lower())

    async def by_username(self, username: str) -> User | None:
        return await self.get_or_none(User.username == username)


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.activity = ActivityRepository(session)

    async def register(self, payload: UserCreate) -> tuple[User, str, object]:
        email = payload.email.lower()
        if await self.users.by_email(email):
            raise ConflictError("This email is already registered", details={"field": "email"})
        if await self.users.by_username(payload.username):
            raise ConflictError(
                "This username is already taken", details={"field": "username"}
            )

        user = User(
            email=email,
            username=payload.username,
            display_name=payload.display_name or payload.username,
            hashed_password=hash_password(payload.password),
        )
        self.session.add(user)
        await self.session.flush()

        await self.activity.record(
            owner_id=user.id,
            event_type="user.registered",
            title=f"账号 {user.username} 创建成功",
            entity_type="user",
            entity_id=str(user.id),
        )
        token, expires = create_access_token(str(user.id), extra={"username": user.username})
        logger.info("user_registered", extra={"user_id": str(user.id)})
        return user, token, expires

    async def login(self, identifier: str, password: str) -> tuple[User, str, object]:
        user = await self.users.by_identifier(identifier.strip())
        # Always run a hash comparison so timing does not reveal account existence.
        if user is None:
            verify_password(password, "$2b$12$" + "x" * 53)
            raise AuthenticationError("Incorrect username or password")
        if not verify_password(password, user.hashed_password):
            raise AuthenticationError("Incorrect username or password")
        if not user.is_active:
            raise AuthenticationError("This account has been disabled")

        token, expires = create_access_token(str(user.id), extra={"username": user.username})
        logger.info("user_login", extra={"user_id": str(user.id)})
        return user, token, expires

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("User not found")
        return user

    async def update_profile(
        self,
        user_id: uuid.UUID,
        *,
        display_name: str | None = None,
        avatar_color: str | None = None,
    ) -> User:
        user = await self.get_user(user_id)
        if display_name is not None:
            user.display_name = display_name
        if avatar_color is not None:
            user.avatar_color = avatar_color
        await self.session.flush()
        return user

    async def ensure_demo_user(self) -> User:
        """Create the default local account on first boot (single-user friendly)."""
        existing = await self.users.by_username("demo")
        if existing:
            return existing
        user = User(
            email="demo@aiworkbench.dev",
            username="demo",
            display_name="Demo User",
            hashed_password=hash_password("demo1234"),
        )
        self.session.add(user)
        await self.session.flush()
        logger.info("demo_user_created", extra={"user_id": str(user.id)})
        return user

    async def user_count(self) -> int:
        from sqlalchemy import func

        return int((await self.session.execute(select(func.count(User.id)))).scalar_one())

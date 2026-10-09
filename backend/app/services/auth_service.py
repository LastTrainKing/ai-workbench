"""Authentication service."""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
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
        # Always run a full bcrypt comparison so timing does not reveal account
        # existence. The dummy is a real hash of the current scheme, not an invalid
        # string: an invalid one makes `checkpw` fail before it does any work, which is
        # fast — and "fast" is the oracle this line exists to remove.
        if user is None:
            verify_password(password, DUMMY_PASSWORD_HASH)
            raise AuthenticationError("Incorrect username or password")
        if not verify_password(password, user.hashed_password):
            raise AuthenticationError("Incorrect username or password")
        if not user.is_active:
            raise AuthenticationError("This account has been disabled")

        # The plaintext is available exactly here, and nowhere else. A hash written
        # before the scheme change is upgraded now, so legacy accounts migrate as they
        # are used instead of being reset.
        if password_needs_rehash(user.hashed_password):
            user.hashed_password = hash_password(password)
            await self.session.flush()
            logger.info("password_hash_upgraded", extra={"user_id": str(user.id)})

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

    async def ensure_demo_user(self) -> User | None:
        """Create the default local account on first boot — **development only**.

        This account is `demo` / `demo1234`, and the password is printed on the sign-in
        screen. That is convenient while building and unacceptable in a shipped build,
        because this application reads and writes the user's files, runs commands,
        reaches the network, and holds their memory and knowledge base. A known
        credential on a machine with those powers is a back door with a sign on it.

        Refuses outside `dev`. `main.py` also checks before calling, so this is the
        second of two gates rather than the only one.
        """
        from app.core.config import settings

        if settings.app_env != "dev":
            logger.info("demo_user_skipped", extra={"app_env": settings.app_env})
            return None

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

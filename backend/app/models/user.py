"""User account model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

if TYPE_CHECKING:
    from app.models.project import Project


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    avatar_color: Mapped[str] = mapped_column(String(16), nullable=False, default="#6366f1")

    projects: Mapped[list[Project]] = relationship(
        back_populates="owner", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username}>"


class RevokedToken(Base, UUIDPrimaryKeyMixin):
    """One logged-out token, recorded durably.

    The revocation list used to live only in Redis, and a Redis outage made every token
    look un-revoked — so "log out" silently stopped meaning anything for as long as the
    cache was down. The database is now the authority: a row here means the token is
    dead, and Redis is a fast path in front of it rather than the only copy.

    A row is only useful until the token expires, because an expired token is rejected
    on its own signature. ``purge_expired`` removes rows past that point so the table
    stays the size of the logout window rather than the size of the account's history.
    """

    __tablename__ = "revoked_tokens"

    #: The JWT id claim. Unique: revoking the same token twice must not create two rows.
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    #: The subject claim, kept so a purge can be scoped and so an operator can see whose
    #: token it was. Not used for authorization.
    subject: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: The moment the token would have expired anyway; past this the row is garbage.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RevokedToken {self.jti}>"

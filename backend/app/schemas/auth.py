"""Authentication & user schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserBase(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(default="", max_length=128)


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        if v.isdigit() or v.isalpha():
            raise ValueError("password must mix letters and digits")
        return v


class UserLogin(BaseModel):
    # Accepts either the username or the email address.
    identifier: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=72)


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    avatar_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    # NOTE: deliberately ``str`` and not ``EmailStr``. Validation happens on the
    # way in (UserCreate); re-validating persisted data on read would let one bad
    # legacy row break every endpoint that returns a user.
    email: str
    username: str
    display_name: str
    avatar_color: str
    is_active: bool
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut

"""Installed skills, and the record of how they have been used.

One row per adopted skill, owned by a user. The columns that matter are the ones that
make a skill *reviewable* rather than merely stored:

* `source` and `source_commit` — a skill that cannot be traced cannot be re-verified when
  the world moves, and the scan's result is only meaningful for the revision it scanned;
* `verified_at` and `verification` — what the scan found, kept so a later reader can see
  what was known at adoption time rather than trusting that it was checked;
* `use_count` and `last_used_at` — the review stage needs evidence, and "how often has
  this actually been used" is the only honest basis for keeping or retiring it;
* `rating` — set by the person after a use. Without it the library fills with skills
  nobody has judged, which is the failure the review stage exists to prevent.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class InstalledSkill(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A skill this user has adopted."""

    __tablename__ = "installed_skills"
    __table_args__ = (
        Index("ix_skill_user_status", "user_id", "status"),
        # A user adopting the same skill twice should update, not duplicate. The unique
        # constraint is what makes "already installed" a fact rather than a check someone
        # remembered to write.
        Index("ix_skill_user_name", "user_id", "name", unique=True),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    when_to_use: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")

    #: "document" | "script" — scripts are executed, so the rest of the system branches on
    #: this when deciding whether to sandbox.
    skill_type: Mapped[str] = mapped_column(String(16), nullable=False, default="document")
    scripts: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: The personalisation slots, with whatever the user's profile filled in.
    params: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    #: The body after params were bound. This is what actually runs.
    rendered_body: Mapped[str] = mapped_column(Text, nullable=False, default="")

    source: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    source_commit: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    license: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0.0")

    #: active | disabled | retired
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    verified_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    #: What the scan said, kept verbatim.
    verification: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set by the person after a use. None means nobody has judged it yet, which is a
    #: different thing from "it was fine".
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


__all__ = ["InstalledSkill"]

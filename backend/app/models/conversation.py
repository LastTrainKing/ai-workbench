"""Conversations, and the things said in them.

Why this exists
---------------
The previous round made the home screen a conversation and wrote down, in
``ConversationPage.tsx``, that a conversation table was unnecessary:

    A turn is a build run. There is no separate conversation table and there does not
    need to be one.

That was wrong, and the user's own words are why:

    我点开这个软件，就是要和我的数字员工对话

A build run is not a conversation. One conversation contains the questions it asked
before starting, the answers to them, the files it produced, and the things it said that
needed no file at all. Modelling that as "one run per thing the user typed" made two
things impossible:

* **Asking first.** A clarifying question is not a build run — nothing is being built — so
  with only ``build_runs`` to write to, the question had nowhere to live and the product
  started work on a request it had not understood.
* **A history you can reopen.** Runs are a work log. Reopening a conversation means
  reading back what was said, in order, including the parts that produced nothing.

So ``Conversation`` is the container, ``Message`` is one thing said in it, and a message
may point at the build run it triggered. The link is stored on the message rather than on
the run: the run does not exist until the message that starts it, and a conversation can
hold several runs.

Only new tables
---------------
Both tables are new, so ``Base.metadata.create_all`` at startup creates them and no
existing table is altered. That is deliberate — this layer adds a record, it does not
reshape the ones already there.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow


class MessageRole(StrEnum):
    """Who said it."""

    USER = "user"
    ASSISTANT = "assistant"


class MessageKind(StrEnum):
    """What the assistant *did* with this turn.

    This is the three-outcome decision from the understanding layer, made visible, plus
    one value for "nothing to decide":

    ``ANSWER``
        It answered in the conversation. No file was made and none was needed.
    ``CLARIFY``
        It asked one to three questions and is waiting. **No build run exists for this
        message**, and that is the point — the gate in front of starting work.
    ``START``
        It created and started a build run, which this message points at.
    ``None``
        Just words: the opening line of an empty conversation, "好，开始做。", and every
        message the user sent. Nothing to act on, so nothing to record.
    """

    ANSWER = "answer"
    CLARIFY = "clarify"
    START = "start"


class Conversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One continuous conversation. What the left-hand list is a list of."""

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: Derived from the first thing the user said, so the list is readable. Never
    #: rewritten afterwards: a title that changes under the user is worse than a title
    #: that is slightly stale, and the first sentence is what they will recognise.
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    def touch(self) -> None:
        """Mark the conversation as changed because something was said in it.

        ``updated_at`` has an ``onupdate``, but that only fires when this row is UPDATEd —
        appending a message touches a different table. Ordering the list by "when was this
        last spoken in" therefore needs this explicit nudge.

        Called from exactly one place (``ConversationService.append``), so there is one
        implementer of "the conversation changed".
        """
        self.updated_at = utcnow()

    def as_dict(self) -> dict[str, Any]:
        return self.to_dict()


class Message(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One thing said, in order."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Denormalised on purpose. Every read of a message is an ownership check, and making
    #: that check require a join would mean one forgotten join somewhere is a leak. It is
    #: written once, by the service, from the conversation's own owner.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    role: Mapped[str] = mapped_column(String(12), nullable=False)
    #: One of :class:`MessageKind`, or NULL for "just words". See that enum for why NULL
    #: is a real value here rather than a missing one.
    kind: Mapped[str | None] = mapped_column(String(12), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    #: What it is waiting on, when ``kind`` is ``clarify``:
    #: ``[{"question": "...", "default": "..."}]``. The default is not decoration — the
    #: user's own rule is that a question without one hands the cost of choosing back to
    #: them, so every entry has both keys.
    questions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    #: The run this message started, when ``kind`` is ``start``.
    build_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("build_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    #: What it decided on the user's behalf, when the user said "don't ask, you decide".
    #: Recorded because the result has to say what was assumed — an assumption the user
    #: cannot see is one they cannot correct.
    decided_for_you: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    #: What this turn consulted: ``{"tier": "memory|knowledge|general", "sources": [...]}``.
    #: On the message rather than the run because an ``answer`` has no run, and the
    #: retrieval ladder is supposed to be inspectable for those too.
    retrieval: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: Set when this message is a question *of* the user that has since been answered, so
    #: the UI can stop showing it as outstanding.
    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_user(self) -> bool:
        return self.role == MessageRole.USER.value

    @property
    def waiting(self) -> bool:
        """True while it is the user's turn to reply."""
        return self.kind == MessageKind.CLARIFY.value and self.answered_at is None

    def as_dict(self, include_plan: bool = False) -> dict[str, Any]:
        payload = self.to_dict()
        payload["waiting"] = self.waiting
        payload["questions"] = list(self.questions or [])
        payload["decided_for_you"] = list(self.decided_for_you or [])
        payload["retrieval"] = dict(self.retrieval or {})
        if not include_plan:
            payload.pop("answered_at", None)
        return payload


__all__ = ["Conversation", "Message", "MessageKind", "MessageRole"]

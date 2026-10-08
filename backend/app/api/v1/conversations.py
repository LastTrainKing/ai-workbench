"""Conversations: the one interface the user actually asked for.

    "我点开这个软件，就是要和我的数字员工对话"

Everything else in this API exists to serve that sentence. A conversation is the container;
a message is one thing said in it; a build run is what happened after a message that
started one. The three-outcome decision lives in
``app.services.conversation_service`` — this module only moves it across the wire.

Ownership follows the same rule as every run-scoped route: one message for "no such
conversation" and "not yours", so an id cannot be probed for existence.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession
from app.core.response import ok
from app.services.conversation_service import CONFIRM_PHRASE, ConversationService

router = APIRouter(tags=["conversation"])


class ConversationCreate(BaseModel):
    title: str = Field(default="", max_length=120)


class MessageCreate(BaseModel):
    #: One sentence is enough; there is no minimum beyond "not empty" because the whole
    #: point of the understanding layer is that it can ask when the sentence is thin.
    content: str = Field(min_length=1, max_length=4000)


@router.get("/conversations", summary="List conversations")
async def list_conversations(
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    service = ConversationService(session, user_id=user.id)
    items = await service.list_conversations(limit=limit)
    return ok({"conversations": items, "count": len(items)})


@router.post("/conversations", status_code=201, summary="Start a conversation")
async def create_conversation(
    payload: ConversationCreate, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    """Start one, and let it speak first.

    The opening line is written server-side so that an empty conversation is never a blank
    page: a new colleague says something brief rather than waiting to be addressed.
    """
    service = ConversationService(session, user_id=user.id)
    conversation = await service.create(payload.title)
    await service.ensure_greeting(conversation)
    return ok(conversation.as_dict(), message="已开始")


@router.get("/conversations/{conversation_id}", summary="One conversation, with its messages")
async def get_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    service = ConversationService(session, user_id=user.id)
    conversation = await service.get(conversation_id)
    messages = await service.messages(conversation_id)
    payload = conversation.as_dict()
    payload["messages"] = [message.as_dict() for message in messages]
    payload["deliverables"] = await service.deliverables(conversation_id)
    return ok(payload)


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=201,
    summary="Say something",
)
async def send_message(
    conversation_id: uuid.UUID,
    payload: MessageCreate,
    user: CurrentUser,
    session: DbSession,
) -> dict[str, Any]:
    """Say something and get back one of three outcomes.

    ``answer`` — it replied in the conversation. ``clarify`` — it asked one to three
    questions and is waiting, and **no build run exists**. ``start`` — it created and
    started a run, whose id comes back so the caller can follow it.

    The outcome is returned rather than inferred by the client. Which of the three
    happened is the decision this endpoint exists to make, and a client that guessed it
    from the shape of the payload would be a second implementation of it.
    """
    service = ConversationService(session, user_id=user.id)
    turn = await service.send(conversation_id, payload.content)
    return ok(turn.as_dict(), message=_outcome_message(turn.outcome))


@router.post(
    "/conversations/{conversation_id}/confirm",
    status_code=201,
    summary="Do not ask, just start",
)
async def confirm(
    conversation_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    """「就按你说的做」.

    Sent as an ordinary message on purpose — the phrase the button sends and the phrase the
    understanding layer is told to recognise are the same constant, in the service, so they
    cannot drift.
    """
    service = ConversationService(session, user_id=user.id)
    turn = await service.confirm(conversation_id)
    return ok(turn.as_dict(), message=_outcome_message(turn.outcome))


def _outcome_message(outcome: str) -> str:
    return {
        "answer": "已回答",
        "clarify": "等你的回答",
        "start": "已开始做",
    }.get(outcome, "已收到")


__all__ = ["CONFIRM_PHRASE", "router"]

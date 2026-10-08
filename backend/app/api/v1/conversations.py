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
from app.core.errors import ValidationError
from app.core.response import ok
from app.services.conversation_service import CONFIRM_PHRASE, ConversationService

router = APIRouter(tags=["conversation"])


class ConversationCreate(BaseModel):
    title: str = Field(default="", max_length=120)


class MessageCreate(BaseModel):
    #: One sentence is enough; there is no minimum beyond "not empty" because the whole
    #: point of the understanding layer is that it can ask when the sentence is thin.
    content: str = Field(min_length=1, max_length=4000)


class ConversationDelete(BaseModel):
    #: One endpoint for all three ways the user asked for — the row's own button, a
    #: selection, and 全选. A single-delete route plus a bulk route would be two code
    #: paths for one operation, and the "select all" case is just a longer list.
    #:
    #: `min_length=1` unless `all` is set — an empty list with no flag is a client bug, not
    #: a no-op: it would return "deleted 0" and look like a success.
    ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)

    #: Delete every conversation this user has.
    #:
    #: 「全选删除：列表顶部一个"全选"，选中后一键清空」 — and it did not clear. The list
    #: endpoint returns 50 by default, so the client's 全选 could only ever select what it
    #: had loaded, and a user with 60 conversations ended up with 10 left and no way to see
    #: why. The alternative was to raise the page size, which moves the cliff rather than
    #: removing it. The client asks for "all" and the server decides what all means.
    all: bool = False


@router.get("/conversations", summary="List conversations")
async def list_conversations(
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    service = ConversationService(session, user_id=user.id)
    items = await service.list_conversations(limit=limit)
    return ok({"conversations": items, "count": len(items)})


@router.post("/conversations/delete", summary="Delete one, several, or all conversations")
async def delete_conversations(
    payload: ConversationDelete,
    user: CurrentUser,
    session: DbSession,
) -> dict[str, Any]:
    """Delete conversations and everything that only existed because of them.

    POST rather than DELETE with a body: a body on DELETE is not reliably supported by
    clients and proxies, and "select all" needs to send a list. The route is still
    idempotent in effect — deleting something already gone is not an error.

    The response carries the counts, so the screen can say what actually happened rather
    than what it hoped happened.
    """
    service = ConversationService(session, user_id=user.id)
    ids = payload.ids
    if payload.all:
        ids = await service.all_conversation_ids()
    if not ids:
        raise ValidationError("No conversations were named and 'all' was not set")
    return ok(await service.delete_conversations(ids))


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

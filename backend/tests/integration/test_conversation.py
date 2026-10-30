"""The understanding layer: does it ask before it works?

The claim being tested is not "it can ask a question". It is that **asking is the default
when the request is thin, and that nothing gets built until it stops being thin**. The
first test below is the one that matters most: a `clarify` decision must leave the database
with zero build runs in it, because a run that exists is a run that can be executed, and
the user's complaint was precisely that work started on a request nobody had understood.

Everything else here is a consequence of that: at most three questions, each carrying a
default, and "don't ask, you decide" being an ordinary message rather than a special path.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.ai.types import ChatResult
from app.models.conversation import MessageKind
from app.models.delivery import BuildRun
from app.services.conversation_service import (
    CONFIRM_PHRASE,
    MAX_QUESTIONS,
    ConversationService,
)
from app.services.delivery_service import DeliveryService


class ScriptedLLM:
    """Hands back the decisions it was given, in order, and keeps the prompts it saw."""

    def __init__(self, *decisions: dict[str, Any]) -> None:
        self.decisions = list(decisions)
        self.prompts: list[str] = []

    async def chat(self, messages: Any, **_kwargs: Any) -> ChatResult:
        self.prompts.append(str(getattr(messages[0], "content", "") or ""))
        index = min(len(self.prompts) - 1, len(self.decisions) - 1)
        return ChatResult(content=json.dumps(self.decisions[index], ensure_ascii=False))


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Record calls to the one implementation of "start a run", without running the agent.

    The conversation must go through `DeliveryService.start_build_run` rather than
    assembling context and handing off itself — this fixture is how that is checked, and
    it doubles as the reason the suite does not spawn a real agent run per test.
    """
    calls: list[uuid.UUID] = []

    async def fake(self: DeliveryService, run: BuildRun) -> dict[str, Any]:
        calls.append(run.id)
        return {"build_run_id": str(run.id), "run_id": "scripted", "state": "PLANNING"}

    monkeypatch.setattr(DeliveryService, "start_build_run", fake)
    return calls


async def _runs(session: Any) -> list[BuildRun]:
    return list((await session.execute(select(BuildRun))).scalars())


def _service(session: Any, user: dict, llm: ScriptedLLM) -> ConversationService:
    return ConversationService(session, user_id=uuid.UUID(user["id"]), llm=llm)


# --------------------------------------------------------------------------- the gate --


@pytest.mark.asyncio
async def test_a_thin_request_is_asked_about_and_builds_nothing(
    session: Any, user_token: tuple[str, dict], started: list[uuid.UUID]
) -> None:
    """The core claim. `clarify` means no run exists — not "a run exists in DRAFT"."""
    _, user = user_token
    llm = ScriptedLLM(
        {
            "decision": "clarify",
            "reply": "好。做之前我想确认几件事 ——",
            "questions": [
                {"question": "要什么形式？", "default": "Markdown 目录，每阶段两三个例子"},
                {"question": "要多少？", "default": "只要一个学习目录"},
                {"question": "给谁看？", "default": "自己学"},
            ],
        }
    )
    service = _service(session, user, llm)
    conversation = await service.create()

    turn = await service.send(conversation.id, "帮我做一个 java 的指引指南，教教我怎么学习")

    assert turn.outcome == "clarify"
    assert turn.message.kind == MessageKind.CLARIFY.value
    assert turn.message.waiting is True
    assert turn.build_run_id is None
    assert await _runs(session) == []
    assert started == []


@pytest.mark.asyncio
async def test_it_never_asks_more_than_three_questions(
    session: Any, user_token: tuple[str, dict], started: list[uuid.UUID]
) -> None:
    _, user = user_token
    llm = ScriptedLLM(
        {
            "decision": "clarify",
            "questions": [{"question": f"问题 {i}", "default": f"默认 {i}"} for i in range(1, 6)],
        }
    )
    service = _service(session, user, llm)
    conversation = await service.create()

    turn = await service.send(conversation.id, "做一份东西")

    assert len(turn.message.questions) == MAX_QUESTIONS


@pytest.mark.asyncio
async def test_a_question_without_a_default_is_kept_and_marked(
    session: Any, user_token: tuple[str, dict], started: list[uuid.UUID]
) -> None:
    """A default-less question must not be dropped.

    Dropping it would turn "I should ask" into "I will guess", which is the failure this
    whole layer exists to prevent. An empty default is a real value: it means it has no
    recommendation for that one, and the screen says so.
    """
    _, user = user_token
    llm = ScriptedLLM({"decision": "clarify", "questions": [{"question": "要什么格式？"}]})
    service = _service(session, user, llm)
    conversation = await service.create()

    turn = await service.send(conversation.id, "做一份东西")

    assert [q["question"] for q in turn.message.questions] == ["要什么格式？"]
    assert turn.message.questions[0]["default"] == ""


# ------------------------------------------------------------------------ the outcomes --


@pytest.mark.asyncio
async def test_a_question_that_needs_no_file_is_just_answered(
    session: Any, user_token: tuple[str, dict], started: list[uuid.UUID]
) -> None:
    _, user = user_token
    llm = ScriptedLLM({"decision": "answer", "reply": "闭包是函数加上它捕获的环境。"})
    service = _service(session, user, llm)
    conversation = await service.create()

    turn = await service.send(conversation.id, "什么是闭包？")

    assert turn.outcome == "answer"
    assert turn.message.kind == MessageKind.ANSWER.value
    assert turn.message.content == "闭包是函数加上它捕获的环境。"
    assert await _runs(session) == []
    assert started == []


@pytest.mark.asyncio
async def test_an_answer_records_which_tier_replied(
    session: Any, user_token: tuple[str, dict], started: list[uuid.UUID]
) -> None:
    """「命中层级仍要能看见」— and an answer has no run to hang that on."""
    _, user = user_token
    llm = ScriptedLLM({"decision": "answer", "reply": "好。"})
    service = _service(session, user, llm)
    conversation = await service.create()

    turn = await service.send(conversation.id, "在吗")

    assert turn.retrieval.get("tier")
    assert turn.message.retrieval.get("tier") == turn.retrieval["tier"]


@pytest.mark.asyncio
async def test_enough_information_starts_a_run_and_points_the_message_at_it(
    session: Any, user_token: tuple[str, dict], started: list[uuid.UUID]
) -> None:
    _, user = user_token
    llm = ScriptedLLM(
        {
            "decision": "start",
            "reply": "好，开始做。",
            "goal": "做一份 Java 学习指引 PPT，只要目录，每阶段带两三个例子",
            "deliverable_kind": "pptx",
        }
    )
    service = _service(session, user, llm)
    conversation = await service.create()

    turn = await service.send(conversation.id, "PPT，要目录，带点例子")

    assert turn.outcome == "start"
    assert turn.message.kind == MessageKind.START.value
    runs = await _runs(session)
    assert len(runs) == 1
    assert str(runs[0].id) == turn.build_run_id
    assert turn.message.build_run_id == runs[0].id
    assert runs[0].deliverable_kind == "pptx"
    # Started through the single implementation, not by this module's own hand.
    assert started == [runs[0].id]


@pytest.mark.asyncio
async def test_an_unknown_deliverable_kind_falls_back_rather_than_failing(
    session: Any, user_token: tuple[str, dict], started: list[uuid.UUID]
) -> None:
    """A model that names a format we do not have is not a reason to lose the request."""
    _, user = user_token
    llm = ScriptedLLM(
        {
            "decision": "start",
            "goal": "整理一份学习指引",
            "deliverable_kind": "powerpoint",
        }
    )
    service = _service(session, user, llm)
    conversation = await service.create()

    turn = await service.send(conversation.id, "整理一份学习指引")

    runs = await _runs(session)
    assert turn.outcome == "start"
    assert runs[0].deliverable_kind == "mixed"


# ---------------------------------------------------------------------- don't ask, do --


@pytest.mark.asyncio
async def test_telling_it_not_to_ask_starts_work_and_records_the_assumptions(
    session: Any, user_token: tuple[str, dict], started: list[uuid.UUID]
) -> None:
    """「别问了，你自己看着办」 must start work *and* say what it decided."""
    _, user = user_token
    llm = ScriptedLLM(
        {
            "decision": "start",
            "goal": "做一份 Java 学习指引 PPT，只要目录",
            "deliverable_kind": "pptx",
            "assumptions": ["形式定为 PPT", "范围定为只要目录"],
        }
    )
    service = _service(session, user, llm)
    conversation = await service.create()

    turn = await service.confirm(conversation.id)

    assert turn.outcome == "start"
    assert turn.decided_for_you == ["形式定为 PPT", "范围定为只要目录"]
    assert turn.message.decided_for_you == turn.decided_for_you
    # The phrase is the service's own constant, so the button and the prompt cannot drift.
    assert CONFIRM_PHRASE in llm.prompts[-1]


@pytest.mark.asyncio
async def test_speaking_again_answers_the_outstanding_question(
    session: Any, user_token: tuple[str, dict], started: list[uuid.UUID]
) -> None:
    _, user = user_token
    llm = ScriptedLLM(
        {"decision": "clarify", "questions": [{"question": "要什么形式？", "default": "PPT"}]},
        {"decision": "start", "goal": "做一份 PPT", "deliverable_kind": "pptx"},
    )
    service = _service(session, user, llm)
    conversation = await service.create()

    first = await service.send(conversation.id, "做点东西")
    assert first.message.waiting is True

    await service.send(conversation.id, "PPT 就行")

    messages = await service.messages(conversation.id)
    asked = [m for m in messages if m.kind == MessageKind.CLARIFY.value]
    assert asked and asked[0].answered_at is not None
    assert asked[0].waiting is False


# -------------------------------------------------------------------------- plumbing --


@pytest.mark.asyncio
async def test_the_first_thing_said_becomes_the_title_and_the_greeting_comes_once(
    session: Any, user_token: tuple[str, dict], started: list[uuid.UUID]
) -> None:
    _, user = user_token
    llm = ScriptedLLM({"decision": "answer", "reply": "好。"})
    service = _service(session, user, llm)
    conversation = await service.create()

    await service.ensure_greeting(conversation)
    await service.ensure_greeting(conversation)
    await service.send(conversation.id, "帮我整理一份课程报告")

    messages = await service.messages(conversation.id)
    assert len([m for m in messages if m.kind is None and not m.is_user]) == 1
    await session.refresh(conversation)
    assert conversation.title == "帮我整理一份课程报告"


@pytest.mark.asyncio
async def test_another_users_conversation_is_not_found(
    session: Any, user_token: tuple[str, dict], started: list[uuid.UUID]
) -> None:
    """One message for "no such conversation" and "not yours" — no existence disclosure."""
    from app.core.errors import NotFoundError

    _, user = user_token
    llm = ScriptedLLM({"decision": "answer", "reply": "好。"})
    mine = _service(session, user, llm)
    conversation = await mine.create()

    stranger = ConversationService(session, user_id=uuid.uuid4(), llm=llm)
    with pytest.raises(NotFoundError):
        await stranger.get(conversation.id)
    with pytest.raises(NotFoundError):
        await stranger.send(conversation.id, "在吗")


# ------------------------------------------------------------------ the routes --
#
# The service tests above drive the layer directly, and they passed while the *route*
# raised `AttributeError: 'Conversation' object has no attribute 'as_dict'` — because a
# service test never serialises a response. The frontend only ever talks to the routes, so
# the routes get their own tests. This is the gap that let that bug through.


@pytest.fixture
def patched_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the route's own `build_llm_provider` at a scripted model.

    Patched where the service looks it up, not where it is defined, so the route builds the
    service the same way it does in production.
    """
    import app.services.conversation_service as module

    monkeypatch.setattr(
        module,
        "build_llm_provider",
        lambda: (ScriptedLLM({"decision": "answer", "reply": "在的。"}), None),
    )


@pytest.mark.asyncio
async def test_routes_start_list_and_read_a_conversation(
    client: Any, auth_headers: dict[str, str], patched_llm: None
) -> None:
    created = await client.post("/conversations", json={"title": ""}, headers=auth_headers)
    assert created.status_code == 201, created.text
    conversation_id = created.json()["data"]["id"]
    assert conversation_id

    listed = await client.get("/conversations", headers=auth_headers)
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()["data"]["conversations"]] == [conversation_id]

    detail = await client.get(f"/conversations/{conversation_id}", headers=auth_headers)
    assert detail.status_code == 200
    body = detail.json()["data"]
    # It speaks first, so an empty conversation is never a blank page.
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "assistant"
    assert body["deliverables"] == []

    sent = await client.post(
        f"/conversations/{conversation_id}/messages",
        json={"content": "在吗"},
        headers=auth_headers,
    )
    assert sent.status_code == 201, sent.text
    payload = sent.json()["data"]
    assert payload["outcome"] == "answer"
    assert payload["message"]["content"] == "在的。"


@pytest.mark.asyncio
async def test_routes_refuse_another_users_conversation(
    client: Any, auth_headers: dict[str, str], patched_llm: None
) -> None:
    created = await client.post("/conversations", json={}, headers=auth_headers)
    conversation_id = created.json()["data"]["id"]

    other = await client.post(
        "/auth/register",
        json={
            "email": f"user_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
            "username": f"user_{uuid.uuid4().hex[:8]}",
            "password": "test1234",
            "display_name": "Other",
        },
    )
    other_headers = {"Authorization": f"Bearer {other.json()['data']['access_token']}"}

    assert (
        await client.get(f"/conversations/{conversation_id}", headers=other_headers)
    ).status_code == 404
    assert (
        await client.post(
            f"/conversations/{conversation_id}/messages",
            json={"content": "在吗"},
            headers=other_headers,
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_routes_require_a_token(client: Any) -> None:
    assert (await client.get("/conversations")).status_code == 401
    assert (await client.post("/conversations", json={})).status_code == 401

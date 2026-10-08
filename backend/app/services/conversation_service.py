"""The understanding layer: decide how to answer *before* answering.

Why this exists
---------------
The user's complaint, in their words:

    我点开这个软件，就是要和我的数字员工对话，就跟一个老板加了员工微信一样，
    他能对话，能做好文件发我。

A digital employee who starts building the moment you finish typing is not an employee, it
is an executor. The thing that was missing, in their words again:

    他会问我表单还是 PPT、要目录还是要例子 —— 现在完全没有

Before this module, `create_build_run` took a sentence and went to work. There was no step
between "user typed" and "work started", so an underspecified request produced a confidently
wrong artifact instead of one question.

Three outcomes, not two
-----------------------
The old model had two: finished, or failed. Both of them assume work started. This one
decides *whether* to start:

``answer``
    The question needs no file. Answer it in the conversation.
``clarify``
    Not enough to go on. Ask one to three questions, each carrying a default, and wait.
    **No build run exists for this turn** — that is the gate, and it is structural rather
    than a rule someone has to remember.
``start``
    Enough to go on — either the user has just answered the questions, or they said "don't
    ask, you decide".

The criterion is the one the prompt states: *is this request enough for me to make what he
asked for?* Asking is not free, so the prompt also says when **not** to ask: when the
information is already there (asking is then just stalling), when a sensible default exists,
and when the unknown only affects detail.

One call, one implementer
-------------------------
The decision and the response are the same model call. Two calls would be two chances to
disagree about what the user meant, and the second one would have to be told what the first
decided — which is how a system ends up with a decision it cannot point at.

Asking well
-----------
A question without a default hands the whole cost of choosing back to the user. So every
question carries one, and it is *what it will do* if the user says nothing — not "your
call". A question whose default is missing is kept and marked as having no recommendation
rather than dropped: dropping it would silently turn "I should ask" into "I will guess".
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts.registry import get_prompt
from app.ai.providers.budget import reasoning_budget
from app.ai.providers.factory import build_llm_provider
from app.ai.types import ChatMessage
from app.core.errors import AIError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.conversation import Conversation, Message, MessageKind, MessageRole
from app.models.delivery import DELIVERABLE_KINDS
from app.services.delivery_service import DeliveryService

logger = get_logger(__name__)

#: How many earlier messages are shown to the decision. Enough to cover a question and its
#: answer; short enough that a long conversation does not push the actual request out of
#: the model's attention.
HISTORY_MESSAGES = 12

#: The most questions it may ask at once. More than this means it did not understand the
#: request well enough to be asking — it should have narrowed it down itself first.
MAX_QUESTIONS = 3

#: What the user is saying when they press 「就按你说的做」.
#:
#: Server-side, so the phrase the understanding layer is told to recognise and the phrase
#: the button sends cannot drift apart.
CONFIRM_PHRASE = "别问了，就按你说的办，你看着办就行。"

#: The opening line of an empty conversation. It speaks first, briefly, the way a new
#: colleague would — and it does not introduce itself at length or thank anyone.
GREETING = "在的。你想让我做什么？一句话说清就行，不清楚的地方我来问你。"

#: What the assistant says instead of a `start` reply that was actually the deliverable.
START_REPLY_FALLBACK = "好，开始做。"

#: A `start` reply is a status line, not the work.
#:
#: The prompt says so. This is what happens when it does not: measured, on the user's
#: own sentence 「写个300字雨后小故事」, the model returned `decision: start` **and** put
#: the finished ~300-character story in `reply`. The user waited 11.7 seconds and got
#: the same text twice — once in the conversation, once as a file — which is the
#: "答了 + 开工了同时为真" the user reported.
#:
#: 120 characters is the boundary between a sentence and a document, not a tuned
#: constant: measured status lines for `start` run 8–30 characters, and the story that
#: caused this was 312. Anything longer is the deliverable arriving in the wrong
#: channel, and substituting the server's own line is the only option that keeps the
#: content out of a place it does not belong.
#:
#: The substitution is logged rather than silent, so the prompt can be judged on
#: whether it needed to happen.
MAX_START_REPLY = 120


class Outcome(StrEnum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    START = "start"


@dataclass(slots=True)
class Turn:
    """One exchange, as the API returns it."""

    outcome: str
    message: Message
    #: Present only for ``start``.
    build_run_id: str | None = None
    #: What it decided on the user's behalf, when it started without being told.
    decided_for_you: list[str] = field(default_factory=list)
    #: Which rung of the retrieval ladder answered, and how each rung went.
    retrieval: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "message": self.message.as_dict(),
            "build_run_id": self.build_run_id,
            "decided_for_you": self.decided_for_you,
            "retrieval": self.retrieval,
        }


def _loads(text: str) -> dict[str, Any]:
    """Parse the model's JSON, tolerating a fenced block it was told not to write."""
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.split("```")[1] if "```" in body[3:] else body[3:]
        if body.startswith("json"):
            body = body[4:]
    start = body.find("{")
    end = body.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("模型没有返回 JSON 对象")
    payload = json.loads(body[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("模型返回的不是 JSON 对象")
    return payload


def _normalise_questions(raw: Any) -> list[dict[str, str]]:
    """Keep at most :data:`MAX_QUESTIONS` usable questions.

    A question without a `default` is **kept**, with an empty default meaning "it has no
    recommendation here". Dropping it would turn "I should ask" into "I will guess", which
    is the failure this whole module exists to prevent.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for entry in raw:
        if isinstance(entry, str):
            text = entry.strip()
            if text:
                out.append({"question": text, "default": ""})
            continue
        if not isinstance(entry, dict):
            continue
        question = str(entry.get("question") or "").strip()
        if not question:
            continue
        out.append({"question": question, "default": str(entry.get("default") or "").strip()})
    if len(out) > MAX_QUESTIONS:
        logger.warning(
            "conversation_asked_too_many_questions",
            extra={"asked": len(out), "kept": MAX_QUESTIONS},
        )
    return out[:MAX_QUESTIONS]


class ConversationService:
    """Reads and writes conversations, and decides what a new message means."""

    def __init__(
        self, session: AsyncSession, *, user_id: uuid.UUID, llm: Any | None = None
    ) -> None:
        self.session = session
        self.user_id = user_id
        #: Injected by tests, built on demand otherwise — the same shape the memory
        #: evaluator uses, so there is one convention for "give this service a scripted
        #: model" rather than one per service.
        self._llm = llm

    # ------------------------------------------------------------------ reads --
    async def create(self, title: str = "") -> Conversation:
        conversation = Conversation(user_id=self.user_id, title=(title or "").strip()[:120])
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def get(self, conversation_id: uuid.UUID) -> Conversation:
        conversation = await self.session.get(Conversation, conversation_id)
        # One message for "no such conversation" and "not yours", so the endpoint cannot be
        # used to find out whether someone else's id exists.
        if conversation is None or conversation.user_id != self.user_id:
            raise NotFoundError("会话不存在")
        return conversation

    async def messages(self, conversation_id: uuid.UUID) -> list[Message]:
        await self.get(conversation_id)
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at, Message.id)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_conversations(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """The left-hand list: title, when it was last spoken in, how many messages.

        Ordered by the newest message rather than by a stored "last activity" column, so
        there is one source of truth for when a conversation was last used.
        """
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == self.user_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        conversations = list((await self.session.execute(stmt)).scalars())
        if not conversations:
            return []

        ids = [c.id for c in conversations]
        counts: dict[uuid.UUID, int] = {}
        rows = await self.session.execute(
            select(Message.conversation_id).where(Message.conversation_id.in_(ids))
        )
        for (conversation_id,) in rows.all():
            counts[conversation_id] = counts.get(conversation_id, 0) + 1

        # How many files each conversation produced, so the delete confirmation can say
        # 「删掉 3 条对话、5 个产出物」 instead of a number the user has to guess at.
        # Counted through messages → runs → deliverables rather than stored, for the same
        # reason `deliverables()` walks it: a stored counter is a second thing that can
        # disagree with the rows it counts.
        from app.models.delivery import Deliverable

        produced: dict[uuid.UUID, int] = {}
        deliverable_rows = await self.session.execute(
            select(Message.conversation_id, func.count(Deliverable.id))
            .join(Deliverable, Deliverable.build_run_id == Message.build_run_id)
            .where(Message.conversation_id.in_(ids))
            .group_by(Message.conversation_id)
        )
        for conversation_id, total in deliverable_rows.all():
            produced[conversation_id] = int(total)

        return [
            {
                **conversation.as_dict(),
                "message_count": counts.get(conversation.id, 0),
                "deliverable_count": produced.get(conversation.id, 0),
            }
            for conversation in conversations
        ]

    # ----------------------------------------------------------------- deletes --
    async def delete_conversations(self, conversation_ids: list[uuid.UUID]) -> dict[str, Any]:
        """Delete conversations and everything that only existed because of them.

        The user's ask:

            会话列表：现在堆满了，要能删。
            删除要二次确认，并说清删的是什么（几条对话、几个产出物）

        Counts are returned rather than a bare status, because the screen shows them
        **before** the delete (in the confirmation) and again afterwards. A delete that
        cannot say what it removed is one the user has to take on faith.

        Three rules, each of which is a decision rather than an implementation detail:

        * **Only this user's conversations.** The ids come from the client, so they are
          filtered against `user_id` first. An id belonging to somebody else is skipped
          and counted, never deleted — the same guarantee as everywhere else here.
        * **Deliverable files go with them.** The user is shown a file count, so leaving
          the files on disk would make that number a half-truth. Files are removed only
          when they resolve **inside the agent workspace**, so a stored path that escaped
          it cannot be turned into a delete outside.
        * **A file that is already gone is not an error.** The user may have removed it
          themselves; that is not a reason to fail the whole operation, and it is logged
          rather than swallowed.
        """
        from sqlalchemy import delete as sql_delete

        from app.ai.tools.permissions import default_workspace
        from app.models.delivery import BuildRun, Deliverable

        wanted = list(dict.fromkeys(conversation_ids))
        owned = list(
            (
                await self.session.execute(
                    select(Conversation.id).where(
                        Conversation.user_id == self.user_id, Conversation.id.in_(wanted)
                    )
                )
            ).scalars()
        )
        skipped = len(wanted) - len(owned)
        if not owned:
            logger.warning(
                "conversation_delete_nothing_owned",
                extra={"requested": len(wanted), "skipped": skipped},
            )
            return {
                "conversations": 0,
                "messages": 0,
                "build_runs": 0,
                "deliverables": 0,
                "files_removed": 0,
                "skipped": skipped,
            }

        message_count = (
            await self.session.execute(
                select(func.count()).select_from(Message).where(Message.conversation_id.in_(owned))
            )
        ).scalar_one()

        run_ids = [
            run_id
            for run_id in (
                await self.session.execute(
                    select(Message.build_run_id).where(
                        Message.conversation_id.in_(owned), Message.build_run_id.is_not(None)
                    )
                )
            ).scalars()
            if run_id
        ]

        paths: list[str] = []
        deliverable_count = 0
        if run_ids:
            rows = (
                await self.session.execute(
                    select(Deliverable.path).where(Deliverable.build_run_id.in_(run_ids))
                )
            ).scalars()
            paths = [path for path in rows if path]
            deliverable_count = len(paths)
            # Deliverables cascade from the run (`ondelete="CASCADE"`), so this one
            # statement takes both. Messages are not touched: their `build_run_id` is
            # `SET NULL`, which is why they can outlive a run at all.
            await self.session.execute(sql_delete(BuildRun).where(BuildRun.id.in_(run_ids)))

        # Messages cascade from the conversation, so this takes the whole thread.
        await self.session.execute(sql_delete(Conversation).where(Conversation.id.in_(owned)))
        await self.session.flush()

        removed = _remove_workspace_files(paths, default_workspace())

        logger.info(
            "conversations_deleted",
            extra={
                "conversations": len(owned),
                "messages": int(message_count),
                "build_runs": len(run_ids),
                "deliverables": deliverable_count,
                "files_removed": removed,
                "skipped": skipped,
            },
        )
        return {
            "conversations": len(owned),
            "messages": int(message_count),
            "build_runs": len(run_ids),
            "deliverables": deliverable_count,
            "files_removed": removed,
            "skipped": skipped,
        }

    async def deliverables(self, conversation_id: uuid.UUID) -> list[dict[str, Any]]:
        """Every file this conversation produced, oldest first.

        Walked through the messages that started runs rather than stored separately: a
        deliverable already knows its run, and a second list of "deliverables in this
        conversation" is a second thing that can disagree.
        """
        await self.get(conversation_id)
        from app.models.delivery import Deliverable

        stmt = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.build_run_id.is_not(None),
            )
            .order_by(Message.created_at)
        )
        run_ids = [row.build_run_id for row in (await self.session.execute(stmt)).scalars()]
        if not run_ids:
            return []
        items = await self.session.execute(
            select(Deliverable)
            .where(Deliverable.build_run_id.in_(run_ids))
            .order_by(Deliverable.created_at)
        )
        return [item.as_dict() for item in items.scalars()]

    # ------------------------------------------------------------------ writes --
    async def append(
        self,
        conversation: Conversation,
        *,
        role: MessageRole,
        content: str = "",
        kind: MessageKind | None = None,
        questions: list[dict[str, str]] | None = None,
        build_run_id: uuid.UUID | None = None,
        decided_for_you: list[str] | None = None,
        retrieval: dict[str, Any] | None = None,
    ) -> Message:
        """Add a message, and mark the conversation as having been spoken in."""
        message = Message(
            conversation_id=conversation.id,
            user_id=self.user_id,
            role=role.value,
            kind=kind.value if kind else None,
            content=content,
            questions=list(questions or []),
            build_run_id=build_run_id,
            decided_for_you=list(decided_for_you or []),
            retrieval=dict(retrieval or {}),
        )
        self.session.add(message)
        conversation.touch()
        if not conversation.title and role is MessageRole.USER:
            # The first thing they said, so the list is readable. Not rewritten later:
            # a title that moves under the user is worse than one that is stale.
            conversation.title = content.strip().replace("\n", " ")[:120]
        await self.session.flush()
        return message

    async def ensure_greeting(self, conversation: Conversation) -> None:
        """An empty conversation gets one line from it, and only one."""
        existing = await self.session.execute(
            select(Message.id).where(Message.conversation_id == conversation.id).limit(1)
        )
        if existing.first() is None:
            await self.append(conversation, role=MessageRole.ASSISTANT, content=GREETING)

    async def confirm(self, conversation_id: uuid.UUID) -> Turn:
        """The user pressed 「就按你说的做」.

        Not a special code path: it is a message, and the understanding layer already
        knows how to handle being told to stop asking. Keeping it as an ordinary message
        means the record shows what the user actually did.
        """
        return await self.send(conversation_id, CONFIRM_PHRASE)

    # ------------------------------------------------------------------- the turn --
    async def send(self, conversation_id: uuid.UUID, content: str) -> Turn:
        """Say something, and get back what it means."""
        text = (content or "").strip()
        if len(text) < 1:
            raise ValidationError("说点什么")

        conversation = await self.get(conversation_id)
        history = await self.messages(conversation_id)

        # Anything it asked before is answered by the act of the user speaking again.
        from app.models.base import utcnow

        for message in history:
            if message.waiting:
                message.answered_at = utcnow()

        await self.append(conversation, role=MessageRole.USER, content=text)

        decision, retrieval = await self._decide(conversation, history, text)
        return await self._apply(conversation, decision, retrieval)

    # ------------------------------------------------------------------ the layer --
    async def _decide(
        self, conversation: Conversation, history: list[Message], request: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Ask the retrieval ladder what it knows, then the model what to do.

        Both halves are recorded. The ladder's answer goes on the message so 「详情」 can
        say which rung replied — including for an ``answer``, which has no run to hang
        that on.
        """
        from app.ai.rag.tiers import resolve

        ladder = await resolve(self.session, user_id=self.user_id, question=request)

        transcript = []
        for message in history[-HISTORY_MESSAGES:]:
            speaker = "用户" if message.is_user else "你"
            body = message.content.strip()
            if message.kind == MessageKind.CLARIFY.value and message.questions:
                asked = "；".join(str(q.get("question") or "") for q in message.questions)
                body = f"{body}（你问了他：{asked}）".strip()
            if body:
                transcript.append(f"{speaker}：{body}")

        material_parts: list[str] = []
        if ladder.memory_text:
            material_parts.append("【关于他的记忆】\n" + ladder.memory_text)
        for skill in ladder.skills:
            material_parts.append(f"【他的做法：{skill.get('name', '')}】\n{skill.get('summary', '')}")
        for index, chunk in enumerate(ladder.knowledge_chunks, start=1):
            title = chunk.get("document_title") or chunk.get("title") or ""
            material_parts.append(f"【他的资料 {index}：{title}】\n{chunk.get('content', '')}")

        prompt = await get_prompt(self.session, "conversation_turn")
        rendered = prompt.render(
            request=request,
            history="\n".join(transcript) or "（这是第一句）",
            material="\n\n".join(material_parts) or "（没有检索到他的记忆或资料）",
            tier=ladder.tier,
        )

        llm = self._llm
        if llm is None:
            llm, _info = build_llm_provider()
        # Deliberately not wrapped in a try/except that turns a failure into a decision.
        # A model that could not run is not a model that decided "answer" — that collapse
        # is the exact shape this project has already been burned by.
        result = await llm.chat(
            [ChatMessage(role="system", content=rendered)],
            temperature=0.0,
            max_tokens=reasoning_budget(),
            json_mode=True,
        )
        decision = _loads(result.content)
        return decision, ladder.as_dict()

    async def _apply(
        self,
        conversation: Conversation,
        decision: dict[str, Any],
        retrieval: dict[str, Any],
    ) -> Turn:
        """Carry out the decision, and record which one it was."""
        raw = str(decision.get("decision") or "").strip().lower()
        if raw not in {member.value for member in Outcome}:
            raise AIError(f"理解层返回了无法识别的决定：{raw or '(空)'}")

        # Recorded for every turn, not only for the bad ones: `MAX_START_REPLY` is a
        # number chosen against a distribution, and this is the line that shows the
        # distribution. Without it the next person changing that constant is guessing.
        logger.info(
            "conversation_turn_decided",
            extra={
                "outcome": raw,
                "reply_chars": len(str(decision.get("reply") or "").strip()),
                "questions": len(decision.get("questions") or []),
            },
        )

        if raw == Outcome.ANSWER.value:
            return await self._answer(conversation, decision, retrieval)
        if raw == Outcome.CLARIFY.value:
            return await self._clarify(conversation, decision, retrieval)
        return await self._start(conversation, decision, retrieval)

    async def _answer(
        self, conversation: Conversation, decision: dict[str, Any], retrieval: dict[str, Any]
    ) -> Turn:
        reply = str(decision.get("reply") or "").strip()
        if not reply:
            raise AIError("理解层决定直接回答，但没有给出要回答的内容")
        message = await self.append(
            conversation,
            role=MessageRole.ASSISTANT,
            content=reply,
            kind=MessageKind.ANSWER,
            retrieval=retrieval,
        )
        return Turn(outcome=Outcome.ANSWER.value, message=message, retrieval=retrieval)

    async def _clarify(
        self, conversation: Conversation, decision: dict[str, Any], retrieval: dict[str, Any]
    ) -> Turn:
        """Ask, and start nothing.

        The absence of a build run here is the gate the user asked for: 「没经过可以开工
        这一步，不许创建 BuildRun」. It is enforced by this method simply not creating one,
        rather than by a check somewhere that could be forgotten.
        """
        questions = _normalise_questions(decision.get("questions"))
        if not questions:
            # It said "ask" and then had nothing to ask. That is a broken answer, not a
            # reason to start guessing.
            raise AIError("理解层决定先确认，但没有给出任何问题")
        lead = str(decision.get("reply") or "").strip()
        message = await self.append(
            conversation,
            role=MessageRole.ASSISTANT,
            content=lead,
            kind=MessageKind.CLARIFY,
            questions=questions,
            retrieval=retrieval,
        )
        return Turn(outcome=Outcome.CLARIFY.value, message=message, retrieval=retrieval)

    async def _start(
        self, conversation: Conversation, decision: dict[str, Any], retrieval: dict[str, Any]
    ) -> Turn:
        """Create the run, record it on the message, and start it.

        Create and start are one action here, as they are for a person: the user said what
        they wanted, and the next thing they see is work happening.
        """
        goal = str(decision.get("goal") or "").strip()
        if len(goal) < 4:
            raise AIError("理解层决定开工，但没有给出足够具体的目标")

        kind = str(decision.get("deliverable_kind") or "mixed").strip().lower()
        if kind not in DELIVERABLE_KINDS:
            kind = "mixed"

        assumptions = [
            str(item).strip() for item in (decision.get("assumptions") or []) if str(item).strip()
        ]

        reply = str(decision.get("reply") or "").strip()
        if len(reply) > MAX_START_REPLY:
            # It answered and started at the same time. The work goes on either way —
            # refusing here would turn a prompt problem into a failed request — but the
            # text does not go into the conversation, because that is the thing the user
            # asked for and it is about to arrive as a file.
            logger.warning(
                "conversation_start_reply_was_the_work",
                extra={"reply_chars": len(reply), "limit": MAX_START_REPLY, "goal": goal[:120]},
            )
            reply = START_REPLY_FALLBACK

        delivery = DeliveryService(self.session)
        run = await delivery.create_build_run(
            owner_id=self.user_id, goal=goal, deliverable_kind=kind
        )
        message = await self.append(
            conversation,
            role=MessageRole.ASSISTANT,
            content=reply,
            kind=MessageKind.START,
            build_run_id=run.id,
            decided_for_you=assumptions,
            retrieval=retrieval,
        )
        # If starting fails, the message and the run are already recorded — so the failure
        # is a failure to start something the user can see and retry, never a silent no-op.
        await delivery.start_build_run(run)
        return Turn(
            outcome=Outcome.START.value,
            message=message,
            build_run_id=str(run.id),
            decided_for_you=assumptions,
            retrieval=retrieval,
        )


def _remove_workspace_files(paths: list[str], workspace: Path) -> int:
    """Delete deliverable files — but only ones inside the agent workspace.

    The paths come out of the database, and a database is not a trust boundary: a row
    written by a bug, by a migration or by an older version could hold anything at all.
    Resolving each path and requiring the result to be **inside** the workspace is what
    stops this function from becoming a way to delete an arbitrary file. It is the same
    rule `os_open` uses before handing a path to the operating system.

    A file that is already gone is logged, not treated as a failure: the user may have
    deleted it themselves, and that is not a reason to abandon the rest of the delete.
    """
    root = workspace.resolve()
    removed = 0
    for raw in paths:
        try:
            candidate = Path(raw).resolve()
        except OSError as exc:
            logger.warning("deliverable_file_unresolvable", extra={"path": raw, "error": str(exc)})
            continue
        if not candidate.is_relative_to(root):
            logger.warning(
                "deliverable_file_outside_workspace",
                extra={"path": raw, "workspace": str(root)},
            )
            continue
        try:
            candidate.unlink()
            removed += 1
        except FileNotFoundError:
            logger.info("deliverable_file_already_gone", extra={"path": str(candidate)})
        except OSError as exc:
            logger.warning(
                "deliverable_file_not_removed", extra={"path": str(candidate), "error": str(exc)}
            )
    return removed


__all__ = [
    "CONFIRM_PHRASE",
    "GREETING",
    "HISTORY_MESSAGES",
    "MAX_QUESTIONS",
    "MAX_START_REPLY",
    "START_REPLY_FALLBACK",
    "ConversationService",
    "Outcome",
    "Turn",
]

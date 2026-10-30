"""Memory evaluator: deciding what is worth remembering.

This is the part that separates "an assistant that knows you" from "a transcript
with a search index". The rule it is built around:

    **Recording something wrong is worse than recording nothing.**

The pipeline, in order:

    1. rule pre-filter   — obvious noise never reaches the model at all
    2. structured verdict — the model answers specific questions, not "is this
                            interesting?"
    3. dedup             — a restatement bumps an existing memory, never adds one
    4. conflict detection — a contradiction becomes a question, never an overwrite
    5. routing           — high confidence lands active; the middle band becomes a
                            candidate; anything sensitive is dropped outright

Sensitive content never auto-lands, at any confidence. That is not a threshold,
it is a rule.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.memory.similarity import same_thing, similarity
from app.ai.providers.budget import reasoning_budget
from app.ai.types import ChatMessage
from app.core.logging import get_logger
from app.models.context import MemoryCandidate, MemoryEntry, UserPreference

logger = get_logger(__name__)

#: Confidence at or above which a memory is stored without asking.
ACTIVE_THRESHOLD = 0.8
#: Below this, nothing is kept at all.
CANDIDATE_FLOOR = 0.5

#: Facts about the user that must never be stored automatically.
#:
#: Note the absence of ``\b`` around the Chinese alternatives. Python's word
#: boundaries are defined against ASCII word characters, so ``\b密码\b`` never
#: matches "我的密码是…" — the boundary is simply not there. A safety rule that
#: silently does not fire is worse than no rule at all, so the Chinese forms are
#: matched without boundaries while the Latin ones keep them.
SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"密码|口令|password|passwd|pwd", re.I),
    re.compile(r"api[\s_-]?key|secret|token|私钥|密钥|凭据", re.I),
    re.compile(r"\d{15,19}"),  # national ID / long account numbers
    re.compile(r"(?:\d{4}[ -]?){3}\d{4}"),  # card-like digit groups
    re.compile(r"银行卡|信用卡|身份证|护照|社保卡|bank\s*card|credit\s*card", re.I),
    re.compile(r"病历|诊断|抑郁|焦虑|病史|medication|diagnosis", re.I),
)

#: Chit-chat and one-off transactions. Checked before the model is called.
NOISE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(你好|您好|hi|hello|hey|在吗|谢谢|thanks|thank you)\s*[!！。.~]*\s*$", re.I),
    re.compile(r"(今天|昨天|刚刚).{0,6}(吃|喝|睡|累|困)", re.I),
    re.compile(r"^\s*(好的|ok|okay|收到|明白|嗯|哦|行)\s*[!！。.]*\s*$", re.I),
    re.compile(r"(天气|几点|星期几|今天几号)", re.I),
)

#: The kinds a memory can have. Kept small on purpose — a taxonomy nobody can
#: remember is a taxonomy nobody uses.
MEMORY_KINDS = ("identity", "goal", "preference", "skill", "constraint", "fact", "decision")


class Routing(StrEnum):
    """What should happen to a candidate fact."""

    ACTIVE = "active"
    CANDIDATE = "candidate"
    DROP = "drop"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


@dataclass
class Verdict:
    """The evaluator's decision about one statement."""

    routing: Routing
    reason: str
    kind: str = "fact"
    scope: str = "global"
    confidence: float = 0.0
    content: str = ""
    conflicts_with: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "routing": self.routing.value,
            "reason": self.reason,
            "kind": self.kind,
            "scope": self.scope,
            "confidence": round(self.confidence, 3),
            "content": self.content,
            "conflicts_with": self.conflicts_with,
        }


def is_sensitive(text: str) -> bool:
    """True when the text contains anything that must never be auto-stored."""
    return any(pattern.search(text) for pattern in SENSITIVE_PATTERNS)


def is_noise(text: str) -> bool:
    """True when the text is chit-chat or a one-off transaction."""
    stripped = (text or "").strip()
    if len(stripped) < 8:
        return True
    return any(pattern.search(stripped) for pattern in NOISE_PATTERNS)


def _normalise(text: str) -> str:
    """Fold a statement down to its content for comparison."""
    return re.sub(r"[\s，。,.!！?？、；;：:]+", "", (text or "").lower())


#: The similarity decision now lives in `similarity.py`, in one place, with the
#: measurement that chose it. Re-exported under the old name because callers and the
#: sampling script import it from here.
_similarity = similarity


class MemoryEvaluator:
    """Turns conversation and run outcomes into at most a few memory rows."""

    def __init__(self, session: AsyncSession, llm: Any, *, user_id: Any) -> None:
        self.session = session
        self.llm = llm
        self.user_id = user_id

    # ------------------------------------------------------------------ store --
    async def _existing(self, *, limit: int = 60) -> list[MemoryEntry]:
        stmt = (
            select(MemoryEntry)
            .where(MemoryEntry.user_id == self.user_id, MemoryEntry.status == "active")
            .order_by(MemoryEntry.use_count.desc(), MemoryEntry.updated_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def _candidates(self, *, status: str, limit: int = 40) -> list[MemoryCandidate]:
        """Candidates the user has not settled, or has already turned down.

        Both matter, and neither was looked at before this round — which is the whole
        reason four identical candidates queued up waiting for a person to press 确认
        four times. The dedup compared a new statement only against `memory_entries`;
        a restatement of something already *pending* was invisible to it, so every
        rejection the user made added another row saying the same thing.
        """
        stmt = (
            select(MemoryCandidate)
            .where(
                MemoryCandidate.user_id == self.user_id,
                MemoryCandidate.status == status,
            )
            .order_by(MemoryCandidate.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def _judge(
        self,
        statement: str,
        context: str,
        existing: list[MemoryEntry],
        pending: list[MemoryCandidate],
        rejected: list[MemoryCandidate],
    ) -> dict[str, Any]:
        """Ask the model the specific questions, not "is this interesting?".

        Three lists go in, and the third question is the one this round added:

        * **existing** — so it can answer the one question a lexical comparison cannot:
          does this contradict something we already believe?
        * **pending** — so a restatement of something already waiting for the user does
          not become a second row. The user pressing 确认 four times for one fact is the
          complaint this fixes.
        * **rejected** — because a refusal is a decision, not a vote. The same
          information coming back must not be proposed again.

        The duplication question goes to the model rather than to a threshold because
        the measurement in `similarity.py` is unambiguous: 「用户喜欢用 Python 写脚本」
        and 「用户喜欢用 Java 写脚本」 score 0.778, higher than *every* genuine
        rephrasing in the sample. No threshold can separate them; the model can.
        """
        groups: list[str] = []
        if existing:
            groups.append(
                "已有记忆（已生效）：\n"
                + "\n".join(f"  active:{i}. {e.content}" for i, e in enumerate(existing, start=1))
            )
        if pending:
            groups.append(
                "待用户确认的候选（还没生效）：\n"
                + "\n".join(f"  pending:{i}. {c.content}" for i, c in enumerate(pending, start=1))
            )
        if rejected:
            groups.append(
                "用户已经拒绝过的候选（不要再提）：\n"
                + "\n".join(f"  rejected:{i}. {c.content}" for i, c in enumerate(rejected, start=1))
            )
        listing = "\n\n".join(groups) if groups else "（这个用户还没有任何记忆或候选）"

        system = (
            "你在决定一段话是否值得作为「关于用户的长期记忆」保存。\n"
            "标准是：**记错比不记更糟**。宁可漏掉，也不要存进无关内容。\n\n"
            "只回答 JSON，不要任何解释：\n"
            "{\n"
            '  "worth_remembering": true/false,\n'
            '  "reason": "一句话理由",\n'
            f'  "kind": {"|".join(MEMORY_KINDS)},\n'
            '  "scope": "global 或 project",\n'
            '  "confidence": 0.0~1.0,\n'
            '  "content": "改写成的、对未来有用的陈述句",\n'
            '  "contradicts": 已有记忆中与它矛盾的编号（没有则 -1），只填数字\n'
            '  "duplicates": "与下面某一条说的是同一件事就填它的编号，格式 active:3 或 '
            'pending:1 或 rejected:0；没有同一件事就填空字符串"\n'
            "}\n\n"
            "判定要点：\n"
            "- 长期有效的偏好、身份、技能、约束、决定 → 值得\n"
            "- 一次性的、当天的、寒暄的、能从别处查到的事实 → 不值得\n"
            "- 「我正在做毕业设计」属于项目状态，不值得存为记忆\n"
            "- 「我以后所有项目都用 React」是长期偏好，值得\n"
            "- 置信度低于 0.5 就填低分，不要勉强\n"
            "- **duplicates 比措辞重要**：只要说的是同一件事，哪怕用词完全不同、长短差很多，\n"
            "  也要填出来。它和 contradicts 互斥：矛盾填 contradicts，重复填 duplicates。\n"
            "- 如果候选内容与下面某条已有记忆**语义相反**（不是补充，是矛盾），"
            "把那条的编号填进 contradicts；只是相关或更具体，填 -1\n\n"
            f"{listing}"
        )
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(
                role="user", content=f"上下文：{context[:800]}\n\n候选内容：{statement[:1200]}"
            ),
        ]
        # `max_tokens` covers the reasoning model's thinking as well as its answer, and
        # a budget too small for the thinking produces *no* answer — the provider
        # raises rather than truncating. At 500 this call failed every time, and the
        # failure was caught below and returned as `DROP`, i.e. "not worth
        # remembering". The learning loop was not degraded, it was off, and it looked
        # exactly like the filter working.
        chat = await self.llm.chat(
            messages, temperature=0.0, max_tokens=reasoning_budget(), json_mode=True
        )
        try:
            return json.loads(chat.content)
        except json.JSONDecodeError as exc:
            # A parse failure is a real failure too, and is raised rather than
            # converted into a verdict — see `evaluate`.
            raise ValueError(f"判定器返回的不是合法 JSON：{(chat.content or '')[:200]!r}") from exc

    # ---------------------------------------------------------------- evaluate --
    async def evaluate(
        self,
        statement: str,
        *,
        context: str = "",
        source: str = "conversation",
        structured: dict[str, Any] | None = None,
    ) -> Verdict:
        """Decide what to do with one candidate statement.

        `structured` is the provenance: which delivery, which rejection, when. It is
        carried onto whatever row this produces — entry or candidate — because a
        memory with no origin is an assertion from nowhere, and "why does it think
        that about me?" has to be answerable.
        """
        text = (statement or "").strip()
        origin = structured or {}

        if not text:
            return Verdict(Routing.DROP, "空内容")
        if is_sensitive(text):
            return Verdict(
                Routing.DROP,
                "包含敏感信息（凭据 / 证件 / 健康 / 财务），永不自动保存",
                content=text,
            )
        if is_noise(text):
            return Verdict(Routing.DROP, "寒暄或一次性事务，没有长期价值", content=text)

        existing = await self._existing()
        pending = await self._candidates(status="pending")
        rejected = await self._candidates(status="rejected")

        # Deliberately *not* wrapped in a try/except that returns DROP.
        #
        # It was, and the consequence was that a judge that could not run at all was
        # indistinguishable from a judge that ran and said "not worth remembering".
        # Every delivery rejection for a whole round was silently discarded this way,
        # and the only trace was a log line nobody reads. A failure must not be
        # representable as a verdict — the callers already handle exceptions
        # (`_learn_from_decision` logs `delivery_memory_pass_failed`).
        judged = await self._judge(text, context, existing, pending, rejected)

        if not judged.get("worth_remembering"):
            return Verdict(
                Routing.DROP, str(judged.get("reason") or "判定为不值得记忆"), content=text
            )

        content = str(judged.get("content") or text).strip()
        kind = str(judged.get("kind") or "fact").lower()
        if kind not in MEMORY_KINDS:
            kind = "fact"
        try:
            confidence = max(0.0, min(1.0, float(judged.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        scope = (
            "project" if str(judged.get("scope", "")).lower().startswith("project") else "global"
        )

        if confidence < CANDIDATE_FLOOR:
            return Verdict(
                Routing.DROP, "置信度过低", kind=kind, confidence=confidence, content=content
            )

        # ---------------------------------------------------------- duplicate --
        # First, the model's answer. It is asked about the statement it was given, and
        # it can see all three lists, which is the only thing here that can tell
        # 「图表充足、页数精简」 from 「图表充足、页数精简」 said three other ways.
        duplicate = self._resolve_duplicate(judged.get("duplicates"), existing, pending, rejected)
        if duplicate is not None:
            bucket, target = duplicate
            if bucket == "rejected":
                # A refusal is a decision, not a vote. Re-proposing it is the system
                # asking the user to make the same decision twice, which is exactly
                # what they objected to.
                return Verdict(
                    Routing.DUPLICATE,
                    "这条你之前已经拒绝过了，不再提议",
                    kind=kind,
                    confidence=confidence,
                    content=content,
                )
            if bucket == "pending":
                # Bump the candidate that is already waiting instead of queueing a
                # second copy of it. The evidence count is what the UI shows as
                # 「这条我提过 N 次」, so the user sees how often it came up rather
                # than being handed N rows to press 确认 on.
                await self._bump_candidate(target, confidence, origin)
                return Verdict(
                    Routing.DUPLICATE,
                    f"与一条待确认候选重复，已更新它（提到过 {self._evidence_count(target)} 次）",
                    kind=kind,
                    confidence=confidence,
                    content=content,
                )
            # An active memory: it is already in the library. Asking again is wasted
            # effort, so the use count moves instead.
            await self._bump_entry(target, confidence, origin)
            return Verdict(
                Routing.DUPLICATE,
                f"与已有记忆重复，已更新其使用次数（{target.use_count}）",
                kind=kind,
                confidence=confidence,
                content=content,
            )

        # Second, a near-copy guard on the *rewritten* content. The model was shown the
        # raw statement and may have rephrased it into something that is now almost
        # literally an existing row; `same_thing` catches that without a second model
        # call, and refuses to fire when the two name different things.
        for entry in existing:
            if entry.kind == kind and same_thing(content, entry.content):
                await self._bump_entry(entry, confidence, origin)
                return Verdict(
                    Routing.DUPLICATE,
                    f"与已有记忆重复，已更新其使用次数（{entry.use_count}）",
                    kind=kind,
                    confidence=confidence,
                    content=content,
                )

        # Conflict: a contradiction becomes a question, never an overwrite.
        #
        # The model's answer is authoritative when it names a memory. The lexical
        # check stays as a fallback for the near-copy case, where a restatement
        # with a flipped negation is easy to miss semantically.
        try:
            named = int(judged.get("contradicts", -1))
        except (TypeError, ValueError):
            named = -1

        for index, entry in enumerate(existing, start=1):
            contradicted = named == index or (
                entry.kind == kind and _is_contradiction(content, entry.content)
            )
            if contradicted:
                candidate = MemoryCandidate(
                    user_id=self.user_id,
                    proposed_kind=kind,
                    proposed_scope=scope,
                    content=content,
                    reason=f"与已有记忆「{entry.content[:80]}」矛盾，需要你确认以哪条为准",
                    confidence=confidence,
                    structured=origin,
                    status="pending",
                    conflicts_with=str(entry.id),
                )
                self.session.add(candidate)
                await self.session.flush()
                return Verdict(
                    Routing.CONFLICT,
                    "与已有记忆冲突，已生成待确认候选（不覆盖原记忆）",
                    kind=kind,
                    scope=scope,
                    confidence=confidence,
                    content=content,
                    conflicts_with=str(entry.id),
                )

        if confidence >= ACTIVE_THRESHOLD:
            entry = MemoryEntry(
                user_id=self.user_id,
                kind=kind,
                scope=scope,
                content=content,
                structured=origin,
                confidence=confidence,
                source=source,
                status="active",
                confirmed_by_user=False,
            )
            self.session.add(entry)
            await self.session.flush()
            return Verdict(
                Routing.ACTIVE,
                f"置信度 {confidence:.2f} ≥ {ACTIVE_THRESHOLD}，已直接保存",
                kind=kind,
                scope=scope,
                confidence=confidence,
                content=content,
            )

        candidate = MemoryCandidate(
            user_id=self.user_id,
            proposed_kind=kind,
            proposed_scope=scope,
            content=content,
            reason=str(judged.get("reason") or "可能值得记住，等你确认"),
            confidence=confidence,
            structured=origin,
            status="pending",
        )
        self.session.add(candidate)
        await self.session.flush()
        return Verdict(
            Routing.CANDIDATE,
            f"置信度 {confidence:.2f} 处于待确认区间，已生成候选",
            kind=kind,
            scope=scope,
            confidence=confidence,
            content=content,
        )

    # --------------------------------------------------------------- duplicate --
    @staticmethod
    def _resolve_duplicate(
        answer: Any,
        existing: list[MemoryEntry],
        pending: list[MemoryCandidate],
        rejected: list[MemoryCandidate],
    ) -> tuple[str, Any] | None:
        """Turn the judge's `duplicates` string into the row it points at.

        The answer is `active:3` / `pending:1` / `rejected:0`, or empty. Anything that
        does not parse, or that points outside the list it names, resolves to `None` —
        and `None` means "no duplicate", which is the safe direction: the statement
        becomes a candidate and the user decides. An out-of-range index that was
        silently accepted would bump an arbitrary row.
        """
        text = str(answer or "").strip().lower()
        if not text or ":" not in text:
            return None
        bucket, _, raw = text.partition(":")
        try:
            index = int(raw.strip())
        except ValueError:
            logger.warning("memory_duplicate_unparsed", extra={"answer": text})
            return None
        if bucket == "active":
            rows: list[Any] = existing
        elif bucket == "pending":
            rows = pending
        elif bucket == "rejected":
            rows = rejected
        else:
            logger.warning("memory_duplicate_unknown_bucket", extra={"answer": text})
            return None
        # The prompt numbers from 1.
        if not 1 <= index <= len(rows):
            logger.warning(
                "memory_duplicate_out_of_range",
                extra={"answer": text, "size": len(rows)},
            )
            return None
        return bucket, rows[index - 1]

    @staticmethod
    def _evidence_count(candidate: MemoryCandidate) -> int:
        """How many times this candidate has been proposed. 1 for a fresh one."""
        structured = candidate.structured if isinstance(candidate.structured, dict) else {}
        try:
            return max(1, int(structured.get("evidence_count") or 1))
        except (TypeError, ValueError):
            return 1

    async def _bump_candidate(
        self, candidate: MemoryCandidate, confidence: float, origin: dict[str, Any]
    ) -> None:
        """Record that a waiting candidate came up again.

        The count is what lets the UI say 「这条我提过 N 次」 instead of showing N rows.
        Nothing else about the candidate changes: its wording is the one the user will
        read, and rewriting it under them would make the thing they are being asked
        about move while they look at it.
        """
        structured = dict(candidate.structured or {})
        structured["evidence_count"] = self._evidence_count(candidate) + 1
        structured["last_seen_at"] = datetime.now(UTC).isoformat()
        if origin and "first_origin" not in structured:
            structured["first_origin"] = origin
        candidate.structured = structured
        candidate.confidence = max(candidate.confidence, confidence)
        await self.session.flush()

    async def _bump_entry(
        self, entry: MemoryEntry, confidence: float, origin: dict[str, Any]
    ) -> None:
        """Record that an existing memory was stated again."""
        entry.use_count += 1
        entry.last_used_at = datetime.now(UTC)
        entry.confidence = max(entry.confidence, confidence)
        if origin and not entry.structured:
            # The memory already existed and had no recorded origin; this restatement
            # is the evidence of where it came from. Attaching it is accurate — it is
            # the same belief — and it is the only way an older memory becomes
            # reviewable instead of staying an assertion from nowhere forever.
            entry.structured = origin
        await self.session.flush()

    # ---------------------------------------------------------------- confirm --
    async def approve(self, candidate_id: Any) -> MemoryEntry | None:
        candidate = await self.session.get(MemoryCandidate, candidate_id)
        if candidate is None or candidate.user_id != self.user_id:
            return None
        candidate.status = "approved"
        candidate.resolved_at = datetime.now(UTC)

        entry = MemoryEntry(
            user_id=self.user_id,
            kind=candidate.proposed_kind,
            scope=candidate.proposed_scope,
            content=candidate.content,
            # The origin travels with the memory. Losing it at the approval step
            # would leave the memory page unable to say where the memory came from,
            # which is the whole point of recording it.
            structured=candidate.structured or {},
            confidence=max(candidate.confidence, ACTIVE_THRESHOLD),
            # An approved candidate keeps the source it was proposed from, so a
            # preference learned from a rejection still reads as "delivery" rather
            # than being flattened into "conversation" on the way in.
            source=str(
                (candidate.structured or {}).get("origin", {}).get("kind") or "conversation"
            ),
            status="active",
            confirmed_by_user=True,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def reject(self, candidate_id: Any) -> bool:
        candidate = await self.session.get(MemoryCandidate, candidate_id)
        if candidate is None or candidate.user_id != self.user_id:
            return False
        candidate.status = "rejected"
        candidate.resolved_at = datetime.now(UTC)
        await self.session.flush()
        return True

    # ------------------------------------------------------------------ stats --
    async def stats(self) -> dict[str, Any]:
        """The purity numbers worth quoting: they show the filter is doing work."""
        total = await self.session.scalar(
            select(func.count()).select_from(MemoryEntry).where(MemoryEntry.user_id == self.user_id)
        )
        active = await self.session.scalar(
            select(func.count())
            .select_from(MemoryEntry)
            .where(MemoryEntry.user_id == self.user_id, MemoryEntry.status == "active")
        )
        archived = await self.session.scalar(
            select(func.count())
            .select_from(MemoryEntry)
            .where(MemoryEntry.user_id == self.user_id, MemoryEntry.status == "archived")
        )
        approved = await self.session.scalar(
            select(func.count())
            .select_from(MemoryCandidate)
            .where(MemoryCandidate.user_id == self.user_id, MemoryCandidate.status == "approved")
        )
        rejected = await self.session.scalar(
            select(func.count())
            .select_from(MemoryCandidate)
            .where(MemoryCandidate.user_id == self.user_id, MemoryCandidate.status == "rejected")
        )
        pending = await self.session.scalar(
            select(func.count())
            .select_from(MemoryCandidate)
            .where(MemoryCandidate.user_id == self.user_id, MemoryCandidate.status == "pending")
        )
        avg_use = await self.session.scalar(
            select(func.avg(MemoryEntry.use_count)).where(
                MemoryEntry.user_id == self.user_id, MemoryEntry.status == "active"
            )
        )
        decided = (approved or 0) + (rejected or 0)
        return {
            "total_entries": total or 0,
            "active": active or 0,
            "archived": archived or 0,
            "candidates_pending": pending or 0,
            "candidates_approved": approved or 0,
            "candidates_rejected": rejected or 0,
            "rejection_rate": round((rejected or 0) / decided, 3) if decided else 0.0,
            "avg_use_count": round(float(avg_use or 0), 2),
        }


def _is_contradiction(left: str, right: str) -> bool:
    """True when two statements look like they disagree about the same thing.

    Lexical and therefore conservative: it fires on an explicit negation flip or
    on a "从 X 改成 Y" restatement of a similar subject. A false negative costs a
    duplicate row; a false positive costs the user one question. The asymmetry is
    why the bar is low rather than high.
    """
    a, b = _normalise(left), _normalise(right)
    if not a or not b or _similarity(a, b) < 0.45:
        return False

    negations = ("不", "别", "无需", "不要", "no", "not", "never", "dont")
    a_neg = any(token in a for token in negations)
    b_neg = any(token in b for token in negations)
    if a_neg != b_neg:
        return True

    switches = ("改成", "改为", "换成", "不再", "现在用", "switch to", "instead")
    return any(token in b for token in switches)


async def record_preference(
    session: AsyncSession,
    *,
    user_id: Any,
    key: str,
    value: Any,
    scope: str = "global",
    source: str = "user",
) -> UserPreference:
    """Upsert one preference. Preferences are how personalisation reaches behaviour."""
    stmt = select(UserPreference).where(
        UserPreference.user_id == user_id,
        UserPreference.key == key,
        UserPreference.scope == scope,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        existing.value = value
        existing.source = source
        await session.flush()
        return existing

    pref = UserPreference(user_id=user_id, key=key, value=value, scope=scope, source=source)
    session.add(pref)
    await session.flush()
    return pref


__all__ = [
    "ACTIVE_THRESHOLD",
    "CANDIDATE_FLOOR",
    "MEMORY_KINDS",
    "MemoryEvaluator",
    "Routing",
    "Verdict",
    "is_noise",
    "is_sensitive",
    "record_preference",
]

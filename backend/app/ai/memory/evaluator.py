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


def _similarity(left: str, right: str) -> float:
    """Character-level overlap between two normalised statements.

    Deliberately cheap and deterministic. A semantic comparison would need an
    embedding call per candidate, and the thing being detected here — "the user
    said the same preference twice in slightly different words" — is mostly a
    lexical phenomenon.
    """
    a, b = _normalise(left), _normalise(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter in longer:
        return len(shorter) / len(longer)
    overlap = len(set(shorter) & set(longer))
    return overlap / max(len(set(shorter)), len(set(longer)))


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

    async def _judge(
        self, statement: str, context: str, existing: list[MemoryEntry]
    ) -> dict[str, Any]:
        """Ask the model the specific questions, not "is this interesting?".

        The existing memories are included so the model can answer the one
        question a lexical comparison cannot: does this contradict something we
        already believe? It replies with the index of the memory it conflicts
        with, or -1.
        """
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
            '  "contradicts": 已有记忆中与它矛盾的编号（没有则 -1）\n'
            "}\n\n"
            "判定要点：\n"
            "- 长期有效的偏好、身份、技能、约束、决定 → 值得\n"
            "- 一次性的、当天的、寒暄的、能从别处查到的事实 → 不值得\n"
            "- 「我正在做毕业设计」属于项目状态，不值得存为记忆\n"
            "- 「我以后所有项目都用 React」是长期偏好，值得\n"
            "- 置信度低于 0.5 就填低分，不要勉强\n"
            "- 如果候选内容与下面某条已有记忆**语义相反**（不是补充，是矛盾），"
            "把那条的编号填进 contradicts；只是相关或更具体，填 -1\n"
            + (
                "\n已有记忆：\n"
                + "\n".join(f"{i}. {entry.content}" for i, entry in enumerate(existing, start=1))
                if existing
                else "\n（还没有任何已有记忆）"
            )
        )
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=f"上下文：{context[:800]}\n\n候选内容：{statement[:1200]}"),
        ]
        chat = await self.llm.chat(messages, temperature=0.0, max_tokens=500, json_mode=True)
        try:
            return json.loads(chat.content)
        except json.JSONDecodeError:
            return {"worth_remembering": False, "reason": "模型返回不可解析", "confidence": 0.0}

    # ---------------------------------------------------------------- evaluate --
    async def evaluate(self, statement: str, *, context: str = "", source: str = "conversation") -> Verdict:
        """Decide what to do with one candidate statement."""
        text = (statement or "").strip()

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

        try:
            judged = await self._judge(text, context, existing)
        except Exception as exc:
            logger.warning("memory_judge_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
            return Verdict(Routing.DROP, f"判定失败：{type(exc).__name__}", content=text)

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
        scope = "project" if str(judged.get("scope", "")).lower().startswith("project") else "global"

        if confidence < CANDIDATE_FLOOR:
            return Verdict(Routing.DROP, "置信度过低", kind=kind, confidence=confidence, content=content)

        # Dedup: a restatement bumps the existing row rather than adding one.
        for entry in existing:
            if entry.kind == kind and _similarity(content, entry.content) >= 0.82:
                entry.use_count += 1
                entry.last_used_at = datetime.now(UTC)
                entry.confidence = max(entry.confidence, confidence)
                await self.session.flush()
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
            confidence=max(candidate.confidence, ACTIVE_THRESHOLD),
            source="conversation",
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

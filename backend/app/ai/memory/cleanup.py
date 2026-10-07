"""One-time cleanup of a library that grew before the dedup existed.

The user's library, measured before anything was written here:

    13 条 active
      ├─ 7 条  「这是一条浏览器测试写入的记忆」        测试垃圾
      ├─ 4 条  「用户偏好图表充足、页数精简…」         同一件事的四种说法
      └─ 2 条  封面信息 / 报告完整性                  真正不同的两件事

Fixing the mechanism that produced them does not remove the ones already there. This
is the removal, and it follows the rule the product holds everywhere else:

**先给用户看、再删。** `plan()` computes the whole thing and writes nothing; `apply()`
executes a plan the user has already seen. Nothing is deleted silently, and the plan
names every row with the reason it is on the list.

Two operations, with deliberately different meanings:

* a **duplicate group** — the other rows are marked `merged` and point at the survivor.
  Nothing is destroyed; the fact that the user phrased it four ways is itself
  information. `use_count` is summed onto the survivor, because all of that usage was
  usage of the same belief.
* **litter** — deleted, because there is nothing to keep. A test string is not a belief
  the user holds.

Which rows group together is decided by the model, not by a threshold. The measurement
in `similarity.py` is why: 「用户喜欢用 Python 写脚本」 and 「用户喜欢用 Java 写脚本」
score higher than every genuine rephrasing in the sample, so any threshold either
merges them or misses the real duplicates.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.memory.lifecycle import purge
from app.ai.providers.budget import reasoning_budget
from app.ai.types import ChatMessage
from app.core.logging import get_logger
from app.models.context import MemoryCandidate, MemoryEntry

logger = get_logger(__name__)

#: A hard ceiling on what one plan will touch. A cleanup that proposes merging two
#: hundred rows is not a cleanup, it is a rewrite of the library, and nobody can review
#: that in a list.
MAX_ROWS = 200


@dataclass
class DuplicateGroup:
    keep_id: str
    keep_content: str
    keep_use_count: int
    merged: list[dict[str, Any]] = field(default_factory=list)
    why: str = ""

    @property
    def merged_use_count(self) -> int:
        """The survivor's count once the duplicates' usage is added to it.

        The prompt asks for exactly this number: 「合并后的那条要保留：最完整的那份措辞
        + 累加的 use_count」. Usage of a duplicate is usage of the same belief, so it
        moves rather than disappearing with the row.
        """
        return self.keep_use_count + sum(int(row.get("use_count") or 0) for row in self.merged)

    def as_dict(self) -> dict[str, Any]:
        return {
            "keep_id": self.keep_id,
            "keep_content": self.keep_content,
            "keep_use_count": self.keep_use_count,
            "merged": self.merged,
            "merged_use_count": self.merged_use_count,
            "why": self.why,
        }


@dataclass
class CandidateGroup:
    """Pending candidates that are the same fact, waiting to be asked about once."""

    keep_id: str
    keep_content: str
    merged: list[dict[str, Any]] = field(default_factory=list)
    why: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "keep_id": self.keep_id,
            "keep_content": self.keep_content,
            "merged": self.merged,
            "why": self.why,
        }


@dataclass
class CleanupPlan:
    total_before: int
    duplicates: list[DuplicateGroup] = field(default_factory=list)
    litter: list[dict[str, Any]] = field(default_factory=list)
    candidate_groups: list[CandidateGroup] = field(default_factory=list)
    candidates_before: int = 0
    ungrouped: int = 0
    note: str = ""

    @property
    def rows_removed_from_retrieval(self) -> int:
        return sum(len(group.merged) for group in self.duplicates) + len(self.litter)

    @property
    def candidates_folded(self) -> int:
        return sum(len(group.merged) for group in self.candidate_groups)

    @property
    def candidates_after(self) -> int:
        return self.candidates_before - self.candidates_folded

    @property
    def total_after(self) -> int:
        return self.total_before - self.rows_removed_from_retrieval

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_before": self.total_before,
            "total_after": self.total_after,
            "duplicate_groups": [group.as_dict() for group in self.duplicates],
            "litter": self.litter,
            "candidate_groups": [group.as_dict() for group in self.candidate_groups],
            "candidates_before": self.candidates_before,
            "candidates_after": self.candidates_after,
            "ungrouped": self.ungrouped,
            "rows_removed_from_retrieval": self.rows_removed_from_retrieval,
            "note": self.note,
        }


PLAN_PROMPT = """你在帮用户清理他的长期记忆库。

**已生效的记忆**（编号 A1、A2…）：

{memories}

**还等他确认的候选**（编号 C1、C2…）：

{candidates}

请找出：

1. **duplicates**：已生效记忆里说的是同一件事的若干条。每组指定保留哪一条 ——
   保留**措辞最完整、信息最全**的那条（不要保留最短的）。
2. **litter**：明显不是关于这个人的记忆，而是测试或调试留下的文本。
3. **candidate_duplicates**：候选里说的是同一件事的若干条。同样保留最完整的那条。

只有真的同一件事才算。仅仅是相关、或者一个更具体，都不算。
注意：换了具体名词的是**不同**的事，例如「用 Python」和「用 Java」不是同一件事。

只回答 JSON，不要解释：
{
  "duplicates": [{"keep": 编号, "merge": [编号, ...], "why": "一句话"}],
  "litter": [{"index": 编号, "why": "一句话"}],
  "candidate_duplicates": [{"keep": 编号, "merge": [编号, ...], "why": "一句话"}]
}

编号写数字（A3 写 3，C2 写 2）。没有就填空数组。
宁可漏掉，也不要把不同的东西合在一起。
"""


def _parse_index(value: Any, size: int) -> int | None:
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if 1 <= index <= size else None


async def plan_cleanup(
    session: AsyncSession, *, user_id: uuid.UUID, llm: Any
) -> CleanupPlan:
    """Work out what a cleanup would do, and write nothing.

    Returns a plan whose every entry names a row and says why it is on the list, so the
    user can read it before anything happens. `apply_cleanup` executes exactly this.
    """
    rows = list(
        (
            await session.execute(
                select(MemoryEntry)
                .where(MemoryEntry.user_id == user_id, MemoryEntry.status == "active")
                .order_by(MemoryEntry.use_count.desc())
                .limit(MAX_ROWS)
            )
        ).scalars()
    )
    waiting = list(
        (
            await session.execute(
                select(MemoryCandidate)
                .where(MemoryCandidate.user_id == user_id, MemoryCandidate.status == "pending")
                .order_by(MemoryCandidate.created_at.desc())
                .limit(MAX_ROWS)
            )
        ).scalars()
    )

    plan = CleanupPlan(total_before=len(rows), candidates_before=len(waiting))
    if len(rows) + len(waiting) < 2:
        plan.note = "记忆太少，没有可合并的东西。"
        plan.ungrouped = len(rows)
        return plan

    messages = [
        ChatMessage(
            role="system",
            # `replace`, not `str.format`: the prompt contains a JSON example, and
            # `format` reads its braces as placeholders and raises. Escaping them would
            # work and would have to be remembered every time the example changes.
            content=PLAN_PROMPT.replace(
                "{memories}",
                "\n".join(
                    f"{i}. {row.content}（用过 {row.use_count} 次）"
                    for i, row in enumerate(rows, start=1)
                )
                or "（没有）",
            ).replace(
                "{candidates}",
                "\n".join(f"{i}. {row.content}" for i, row in enumerate(waiting, start=1))
                or "（没有）",
            ),
        ),
        ChatMessage(role="user", content="请给出清理计划。"),
    ]
    # Not wrapped in a try/except that returns an empty plan. An empty plan is
    # indistinguishable from "nothing to clean", and the user would be told their
    # library is fine when the check never ran. The caller surfaces the exception.
    #
    # `reasoning_budget()`, not a literal. 2000 was a literal, and it is the same
    # mistake this project has already paid for once (see `evaluator._judge`): the
    # default provider is a reasoning model whose thinking is covered by the same
    # budget, so a budget too small for the thinking produces *no answer at all* —
    # the provider raises `ai_reasoning_budget_exhausted` rather than truncating.
    # Measured here: 6,212 characters of reasoning against a 2,000-token ceiling, and
    # the endpoint answered 502. Raising it is the fix; retrying with the same budget
    # is the same failure again.
    chat = await llm.chat(
        messages, temperature=0.0, max_tokens=reasoning_budget(), json_mode=True
    )
    try:
        parsed = json.loads(chat.content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"清理计划不是合法 JSON：{(chat.content or '')[:200]!r}") from exc

    claimed: set[int] = set()

    for group in parsed.get("duplicates") or []:
        keep_index = _parse_index(group.get("keep"), len(rows))
        if keep_index is None or keep_index in claimed:
            logger.warning("cleanup_bad_keep_index", extra={"group": group})
            continue
        merge_indexes = [
            index
            for index in (_parse_index(v, len(rows)) for v in group.get("merge") or [])
            if index is not None and index != keep_index and index not in claimed
        ]
        if not merge_indexes:
            continue
        survivor = rows[keep_index - 1]
        claimed.add(keep_index)
        claimed.update(merge_indexes)
        plan.duplicates.append(
            DuplicateGroup(
                keep_id=str(survivor.id),
                keep_content=survivor.content,
                keep_use_count=survivor.use_count,
                merged=[
                    {
                        "id": str(rows[index - 1].id),
                        "content": rows[index - 1].content,
                        "use_count": rows[index - 1].use_count,
                    }
                    for index in merge_indexes
                ],
                why=str(group.get("why") or "说的是同一件事"),
            )
        )

    for entry in parsed.get("litter") or []:
        index = _parse_index(entry.get("index"), len(rows))
        if index is None or index in claimed:
            logger.warning("cleanup_bad_litter_index", extra={"entry": entry})
            continue
        claimed.add(index)
        row = rows[index - 1]
        plan.litter.append(
            {
                "id": str(row.id),
                "content": row.content,
                "use_count": row.use_count,
                "why": str(entry.get("why") or "不是关于用户的记忆"),
            }
        )

    # Candidates that say the same thing: fold them into one, so the user answers
    # once. This is the second half of the complaint — four rows in 待确认 all saying
    # the same thing is the same defect as four memories saying it.
    claimed_candidates: set[int] = set()
    for group in parsed.get("candidate_duplicates") or []:
        keep_index = _parse_index(group.get("keep"), len(waiting))
        if keep_index is None or keep_index in claimed_candidates:
            logger.warning("cleanup_bad_candidate_keep", extra={"group": group})
            continue
        merge_indexes = [
            index
            for index in (_parse_index(v, len(waiting)) for v in group.get("merge") or [])
            if index is not None and index != keep_index and index not in claimed_candidates
        ]
        if not merge_indexes:
            continue
        survivor = waiting[keep_index - 1]
        claimed_candidates.add(keep_index)
        claimed_candidates.update(merge_indexes)
        plan.candidate_groups.append(
            CandidateGroup(
                keep_id=str(survivor.id),
                keep_content=survivor.content,
                merged=[
                    {"id": str(waiting[index - 1].id), "content": waiting[index - 1].content}
                    for index in merge_indexes
                ],
                why=str(group.get("why") or "说的是同一件事"),
            )
        )

    plan.ungrouped = len(rows) - len(claimed)
    parts = [
        f"清理前 {plan.total_before} 条已生效、{plan.candidates_before} 条待确认",
        f"清理后 {plan.total_after} 条已生效、{plan.candidates_after} 条待确认",
    ]
    if plan.duplicates:
        parts.append(f"合并 {sum(len(g.merged) for g in plan.duplicates)} 条重复记忆")
    if plan.litter:
        parts.append(f"删除 {len(plan.litter)} 条测试垃圾")
    if plan.candidate_groups:
        parts.append(f"待确认里合并 {plan.candidates_folded} 条重复")
    plan.note = "，".join(parts) + "。"
    return plan


@dataclass
class CleanupResult:
    merged: int = 0
    deleted: int = 0
    kept: int = 0
    candidates_folded: int = 0
    details: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "merged": self.merged,
            "deleted": self.deleted,
            "kept": self.kept,
            "candidates_folded": self.candidates_folded,
            "details": self.details,
        }


async def apply_cleanup(
    session: AsyncSession, *, user_id: uuid.UUID, plan: CleanupPlan
) -> CleanupResult:
    """Execute a plan the user has seen.

    Duplicates are **marked** `merged`, not deleted, and carry `merged_into` so the
    history of "they said this four ways" survives. Litter is deleted, because there is
    nothing to keep. Both are listed in the plan first — nothing here decides anything.
    """
    result = CleanupResult()

    for group in plan.duplicates:
        survivor = await session.get(MemoryEntry, uuid.UUID(group.keep_id))
        if survivor is None or survivor.user_id != user_id:
            result.details.append(f"跳过：保留项已不存在（{group.keep_id}）")
            continue
        for row in group.merged:
            other = await session.get(MemoryEntry, uuid.UUID(str(row["id"])))
            if other is None or other.user_id != user_id:
                result.details.append(f"跳过：{row['content'][:20]} 已不存在")
                continue
            survivor.use_count += other.use_count
            survivor.confidence = max(survivor.confidence, other.confidence)
            other.status = "merged"
            structured = dict(other.structured or {})
            structured["merged_into"] = group.keep_id
            other.structured = structured
            result.merged += 1
            result.details.append(
                f"合并：「{other.content[:28]}」并入「{survivor.content[:28]}」"
                f"（累加 {other.use_count} 次）"
            )
        result.kept += 1
        result.details.append(
            f"保留：「{survivor.content[:36]}」现在的使用次数是 {survivor.use_count}"
        )

    for group in plan.candidate_groups:
        keeper = await session.get(MemoryCandidate, uuid.UUID(group.keep_id))
        if keeper is None or keeper.user_id != user_id:
            result.details.append(f"跳过：保留的候选已不存在（{group.keep_id}）")
            continue
        highest = int((keeper.structured or {}).get("evidence_count") or 1)
        for row in group.merged:
            other = await session.get(MemoryCandidate, uuid.UUID(str(row["id"])))
            if other is None or other.user_id != user_id:
                continue
            highest = max(highest, int((other.structured or {}).get("evidence_count") or 1))
            # `superseded`, not `rejected`: the user never said no to this one — the
            # system folded it into a fuller statement of the same fact. Calling that a
            # rejection would record a decision they did not make, and it would feed the
            # "you already refused this" rule with refusals that never happened,
            # silencing the fact for good.
            other.status = "superseded"
            other.resolved_at = datetime.now(UTC)
            structured = dict(other.structured or {})
            structured["superseded_by"] = group.keep_id
            structured["superseded_reason"] = group.why
            other.structured = structured
            result.candidates_folded += 1
            result.details.append(f"待确认合并：「{other.content[:28]}」并入更完整的一条")
        # The count travels, so 「这条我提过 N 次」 keeps telling the truth after the
        # rows are folded together.
        if highest > 1:
            structured = dict(keeper.structured or {})
            structured["evidence_count"] = highest
            keeper.structured = structured

    for row in plan.litter:
        removed = await purge(session, user_id=user_id, memory_id=uuid.UUID(str(row["id"])))
        if removed:
            result.deleted += 1
            result.details.append(f"删除：「{row['content'][:32]}」（{row['why']}）")
        else:
            result.details.append(f"跳过：{row['content'][:20]} 已不存在")

    await session.flush()
    logger.info(
        "memory_cleanup_applied",
        extra={"user": str(user_id), "merged": result.merged, "deleted": result.deleted},
    )
    return result


__all__ = [
    "CandidateGroup",
    "CleanupPlan",
    "CleanupResult",
    "DuplicateGroup",
    "apply_cleanup",
    "plan_cleanup",
]

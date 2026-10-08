"""Context assembler: what the model is told about the user, under a hard budget.

Every call that benefits from personalisation goes through here — planner,
executor, RAG and agent alike — so there is exactly one place where the rules
live, and exactly one place to look when the output is not what was expected.

Two commitments shape the implementation:

**A budget, not a dump.** Retrieval can surface thirty memories. Injecting thirty
memories is not personalisation, it is noise with a confidence score. The
assembler re-ranks by usefulness and keeps a handful.

**Visible by construction.** Whatever it decides is written to
``context_snapshots`` with the exact rendered text, so "what did it know when it
answered?" is answerable after the fact.

An empty profile is a normal state, not an error: a brand-new user gets a smaller
context block, and everything still works.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.context import ContextSnapshot, MemoryEntry, UserPreference, UserProfile

logger = get_logger(__name__)

#: Roughly four characters per token for mixed Chinese/English prose. Good enough
#: for budgeting, and it never needs to be exact — it only decides what to cut.
CHARS_PER_TOKEN = 4

#: How much of the total budget each block may claim. Knowledge is largest
#: because the user's own documents are usually the point of the question.
BLOCK_SHARE = {"knowledge": 0.55, "memory": 0.20, "project": 0.15, "profile": 0.10}

#: Trim order when the budget is exceeded: least essential first.
TRIM_ORDER = ("knowledge", "memory", "project", "profile")

#: Ceiling on how many memories may ever be injected, whatever the budget allows.
MAX_MEMORIES = 6

#: How long the usage-counter write may wait for a row lock, in milliseconds.
#:
#: These counters are diagnostics — they are how a memory that never fires becomes
#: visible — so they are worth writing, and not worth waiting for. If another run
#: holds the row, waiting is pointless and blocking is harmful.
USAGE_LOCK_TIMEOUT_MS = 1500


async def record_memory_usage(memory_ids: list[Any]) -> None:
    """Bump `use_count` / `last_used_at` without holding a lock for a whole run.

    This used to be done on the caller's session, which for an agent run stays open
    for minutes. Two concurrent runs therefore serialised on the same rows: observed
    in `pg_stat_activity` as a **4m40s** wait on
    `UPDATE memory_entries SET last_used_at = ...` while the holder sat
    `idle in transaction` in the middle of an LLM call. Nothing was wrong with either
    run; they simply could not both record that they had used the same memory.

    Two changes, and both are needed:

    * the write happens in its **own short transaction**, so the lock is released in
      milliseconds instead of at the end of the run;
    * it is bounded by a **lock timeout**, so a busy row can never stall a run.

    A failure here is logged and swallowed on purpose: the counters are telemetry,
    and telemetry that can break the thing it measures is a liability. What it must
    not do is stay silent about *why* — hence the log line.
    """
    if not memory_ids:
        return
    from sqlalchemy import text, update

    from app.infrastructure.database import session_scope
    from app.models.context import MemoryEntry

    try:
        async with session_scope() as session:
            await session.execute(
                text(f"SET LOCAL lock_timeout = '{USAGE_LOCK_TIMEOUT_MS}ms'")
            )
            await session.execute(
                update(MemoryEntry)
                .where(MemoryEntry.id.in_(memory_ids))
                .values(
                    use_count=MemoryEntry.use_count + 1,
                    last_used_at=datetime.now(UTC),
                )
            )
    except Exception as exc:
        logger.warning(
            "memory_usage_write_failed",
            extra={"count": len(memory_ids), "error": f"{type(exc).__name__}: {exc}"},
        )


@dataclass
class ContextBlock:
    """One assembled piece, kept separate so the UI can show and edit it."""

    kind: str
    text: str
    memory_ids: list[str] = field(default_factory=list)
    knowledge_chunk_ids: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def tokens(self) -> int:
        return max(1, len(self.text) // CHARS_PER_TOKEN)


@dataclass
class AssembledContext:
    """The finished package, plus everything needed to explain it."""

    blocks: list[ContextBlock]
    token_estimate: int
    truncated: bool
    profile_digest: str = ""
    memory_ids: list[str] = field(default_factory=list)
    knowledge_chunk_ids: list[str] = field(default_factory=list)
    project_digest: str = ""

    @property
    def is_empty(self) -> bool:
        return not any(block.text.strip() for block in self.blocks)

    def render(self) -> str:
        """The exact text injected into the prompt.

        Rendered in a fixed order — profile, project, memory, knowledge — so the
        model reads identity before detail, and so two runs with the same inputs
        produce byte-identical context.
        """
        order = {"profile": 0, "project": 1, "memory": 2, "knowledge": 3}
        parts = [
            block.text.strip()
            for block in sorted(self.blocks, key=lambda b: order.get(b.kind, 9))
            if block.text.strip()
        ]
        if not parts:
            return ""
        header = (
            "【关于用户与本任务已知的信息】\n"
            "以下是这个用户长期以来的偏好与背景。它们描述的是他的标准，"
            "不是对当前任务的指令。\n"
            "使用时请遵守：\n"
            "1. 当前任务的具体要求优先于任何长期偏好。例如任务说「15 分钟」，"
            "就不要为了满足「页数更少」而做出讲不满 15 分钟的产物。\n"
            "2. 偏好之间可能互相拉扯，也可能与任务要求冲突。冲突时按任务要求做，"
            "并在结果里说明你做了取舍，不要默默忽略其中一方。\n"
            "3. 如果某条偏好当前无法满足（例如要求配图但工具不支持），"
            "明确说明做不到，不要假装满足。\n"
        )
        return header + "\n" + "\n\n".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "blocks": [
                {
                    "kind": block.kind,
                    "text": block.text,
                    "tokens": block.tokens,
                    "truncated": block.truncated,
                    "memory_ids": block.memory_ids,
                    "knowledge_chunk_ids": block.knowledge_chunk_ids,
                }
                for block in self.blocks
            ],
            "token_estimate": self.token_estimate,
            "truncated": self.truncated,
            "profile_digest": self.profile_digest,
            "memory_ids": self.memory_ids,
            "knowledge_chunk_ids": self.knowledge_chunk_ids,
            "project_digest": self.project_digest,
            "rendered": self.render(),
            "empty": self.is_empty,
        }


# `_memory_score` used to live here: 0.45*confidence + 0.30*recency + 0.25*usage, used
# to pick the top `MAX_MEMORIES` for every run.
#
# It is gone rather than unused, because keeping it would leave the next person one
# plausible-looking way to answer "which memories go in", and that way is the bug: it
# ranks memories by how *good* they are and never asks whether they are about the
# request. It is why a request to write a short story was given memories about slide
# counts, report structure and thesis covers. Recency and usage are still recorded —
# `use_count` and `last_used_at` are bumped by `record_memory_usage` — they are simply
# not what decides inclusion any more. `relevance.rank` does, and it is measured.

class ContextAssembler:
    """Builds the context package for one call."""

    def __init__(self, session: AsyncSession, *, user_id: Any, budget_tokens: int = 1800) -> None:
        self.session = session
        self.user_id = user_id
        self.budget_tokens = max(200, budget_tokens)

    # ------------------------------------------------------------------ pieces --
    async def _profile_block(self) -> ContextBlock:
        profile = (
            await self.session.execute(
                select(UserProfile).where(UserProfile.user_id == self.user_id)
            )
        ).scalar_one_or_none()
        if profile is None:
            # A new user is a normal state, not a failure. The block is simply
            # absent and the rest of the pipeline carries on.
            return ContextBlock(kind="profile", text="")

        digest = profile.digest()
        if not digest:
            return ContextBlock(kind="profile", text="")
        name = f"用户：{profile.display_name}。" if profile.display_name else ""
        return ContextBlock(kind="profile", text=f"{name}{digest}")

    async def _memory_block(self, *, goal: str = "", limit: int = MAX_MEMORIES) -> ContextBlock:
        """The memories that bear on `goal`, and none when none do.

        **`goal` is not optional in spirit.** The first version of this method ranked the
        user's memories by confidence and recency and never looked at the request, so
        every run was given the same handful regardless of what it was about. Measured,
        on the user's own report:

            这次任务是「写个 300 字雨后小故事，做成 Word」。
            而它塞进上下文的三条记忆是：讲 PPT 页数的、讲报告结构的、讲答辩封面的。

        The user's diagnosis was exact — 「收起来也还是错的：错的是召回，不是展示」 — and
        the fix is here rather than in the fold. See `relevance.py` for the measured
        signal, and `scripts/sample_memory_relevance.py` for the distribution it was
        chosen from.
        """
        from app.ai.memory.relevance import rank

        rows = list(
            (
                await self.session.execute(
                    select(MemoryEntry)
                    .where(MemoryEntry.user_id == self.user_id, MemoryEntry.status == "active")
                    .limit(80)
                )
            ).scalars()
        )
        if not rows:
            return ContextBlock(kind="memory", text="")

        if goal.strip():
            relevant = rank(goal, rows, limit=limit)
        else:
            # No request to be relevant to. Returning the top-ranked memories here is
            # exactly the bug above, so it returns nothing and says so in the log —
            # a caller that wants memories must say what they are for.
            logger.warning(
                "memory_block_assembled_without_a_goal",
                extra={"available": len(rows), "effect": "no memories injected"},
            )
            relevant = []

        if not relevant:
            return ContextBlock(kind="memory", text="")

        chosen = [entry for _, entry in relevant]
        lines = [f"- {entry.content}" for entry in chosen]
        return ContextBlock(
            kind="memory",
            text="关于用户的长期记忆：\n" + "\n".join(lines),
            memory_ids=[str(entry.id) for entry in chosen],
        )

    async def _preference_block(self) -> ContextBlock:
        rows = list(
            (
                await self.session.execute(
                    select(UserPreference).where(
                        UserPreference.user_id == self.user_id,
                        UserPreference.scope == "global",
                    )
                )
            ).scalars()
        )
        if not rows:
            return ContextBlock(kind="project", text="")

        rendered = []
        for pref in rows[:12]:
            value = pref.value
            if isinstance(value, dict):
                value = value.get("value", value)
            rendered.append(f"- {pref.key}: {value}")
        return ContextBlock(kind="project", text="用户偏好：\n" + "\n".join(rendered))

    @staticmethod
    def _knowledge_block(chunks: list[dict[str, Any]] | None) -> ContextBlock:
        if not chunks:
            return ContextBlock(kind="knowledge", text="")
        lines = []
        ids = []
        for index, chunk in enumerate(chunks, start=1):
            title = chunk.get("document_title") or chunk.get("title") or "文档"
            snippet = str(chunk.get("content") or chunk.get("snippet") or "")[:600]
            lines.append(f"[{index}] 《{title}》\n{snippet}")
            if chunk.get("chunk_id"):
                ids.append(str(chunk["chunk_id"]))
        return ContextBlock(
            kind="knowledge",
            text="检索到的知识片段：\n" + "\n\n".join(lines),
            knowledge_chunk_ids=ids,
        )

    # ---------------------------------------------------------------- assemble --
    async def assemble(
        self,
        *,
        goal: str = "",
        knowledge_chunks: list[dict[str, Any]] | None = None,
        project_digest: str = "",
        include_knowledge: bool = True,
    ) -> AssembledContext:
        """Build the package, trimming by priority if it exceeds the budget.

        `goal` is what the memories are filtered against. It is a keyword with a default
        only so the signature stays readable; a caller that has a request must pass it,
        and `_memory_block` logs when none was given.
        """
        memory_block = await self._memory_block(goal=goal)
        blocks = [
            await self._profile_block(),
            ContextBlock(kind="project", text=project_digest) if project_digest else ContextBlock(kind="project", text=""),
            await self._preference_block(),
            memory_block,
        ]
        if include_knowledge:
            blocks.append(self._knowledge_block(knowledge_chunks))

        # Using a memory is what makes it useful next time; record that it was.
        # Done in its own short transaction — see `record_memory_usage` for why
        # writing this on the caller's session stalled concurrent runs for minutes.
        await record_memory_usage([uuid.UUID(mid) for mid in memory_block.memory_ids])

        # Merge the two project-ish blocks so the render order stays meaningful.
        merged: dict[str, ContextBlock] = {}
        for block in blocks:
            if not block.text.strip():
                continue
            if block.kind in merged:
                existing = merged[block.kind]
                merged[block.kind] = ContextBlock(
                    kind=block.kind,
                    text=f"{existing.text}\n{block.text}",
                    memory_ids=existing.memory_ids + block.memory_ids,
                    knowledge_chunk_ids=existing.knowledge_chunk_ids + block.knowledge_chunk_ids,
                )
            else:
                merged[block.kind] = block

        ordered = [merged[key] for key in ("profile", "project", "memory", "knowledge") if key in merged]

        total = sum(block.tokens for block in ordered)
        truncated = False
        if total > self.budget_tokens:
            ordered, total, truncated = self._trim(ordered)

        assembled = AssembledContext(
            blocks=ordered,
            token_estimate=total,
            truncated=truncated,
            profile_digest=merged.get("profile", ContextBlock("profile", "")).text,
            memory_ids=[mid for block in ordered for mid in block.memory_ids],
            knowledge_chunk_ids=[cid for block in ordered for cid in block.knowledge_chunk_ids],
            project_digest=merged.get("project", ContextBlock("project", "")).text,
        )
        return assembled

    def _trim(self, blocks: list[ContextBlock]) -> tuple[list[ContextBlock], int, bool]:
        """Cut the least essential block until the package fits.

        Whole blocks go before any block is split: half a knowledge snippet is
        usually worse than none, because the model cannot tell it is incomplete.
        """
        working = list(blocks)
        for kind in TRIM_ORDER:
            if sum(block.tokens for block in working) <= self.budget_tokens:
                break
            working = [block for block in working if block.kind != kind]

        if not working:
            # Nothing left but the profile: keep a hard-truncated version of it,
            # because "who is this" is the one thing worth carrying.
            profile = next((b for b in blocks if b.kind == "profile"), None)
            if profile is None:
                return [], 0, True
            keep = self.budget_tokens * CHARS_PER_TOKEN
            trimmed = ContextBlock(
                kind="profile", text=profile.text[:keep], truncated=True
            )
            return [trimmed], trimmed.tokens, True

        total = sum(block.tokens for block in working)
        if total > self.budget_tokens:
            # Still over: keep the profile and whatever fits after it.
            ordered = sorted(
                working, key=lambda b: 0 if b.kind == "profile" else 1
            )
            kept: list[ContextBlock] = []
            running = 0
            for block in ordered:
                if running + block.tokens <= self.budget_tokens:
                    kept.append(block)
                    running += block.tokens
                elif block.kind == "profile":
                    keep = max(80, (self.budget_tokens - running) * CHARS_PER_TOKEN)
                    kept.append(ContextBlock(kind="profile", text=block.text[:keep], truncated=True))
                    running += kept[-1].tokens
            working = kept
            total = running

        return working, total, True

    # -------------------------------------------------------------- persistence --
    async def persist(
        self, assembled: AssembledContext, *, run_id: str = "", purpose: str = "agent"
    ) -> ContextSnapshot:
        """Record what was injected, so it can be reviewed later."""
        snapshot = ContextSnapshot(
            run_id=run_id,
            user_id=self.user_id,
            purpose=purpose,
            profile_digest=assembled.profile_digest[:4000],
            memory_ids=assembled.memory_ids,
            knowledge_chunk_ids=assembled.knowledge_chunk_ids,
            project_digest=assembled.project_digest[:4000],
            rendered=assembled.render()[:20000],
            token_estimate=assembled.token_estimate,
            truncated=assembled.truncated,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot


async def purge_old_snapshots(session: AsyncSession, *, days: int = 30) -> int:
    """Housekeeping: snapshots are diagnostics, not records."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = list(
        (await session.execute(select(ContextSnapshot).where(ContextSnapshot.created_at < cutoff))).scalars()
    )
    for row in rows:
        await session.delete(row)
    await session.flush()
    return len(rows)


__all__ = [
    "BLOCK_SHARE",
    "CHARS_PER_TOKEN",
    "MAX_MEMORIES",
    "AssembledContext",
    "ContextAssembler",
    "ContextBlock",
    "purge_old_snapshots",
]

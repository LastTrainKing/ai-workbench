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
        return "【关于用户与本任务已知的信息】\n" + "\n\n".join(parts)

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


def _memory_score(entry: MemoryEntry, *, now: datetime) -> float:
    """Rank a memory by how useful it is likely to be right now.

    Relevance is approximated by recency and by how often the memory has actually
    been used. ``use_count`` matters because a memory that has never influenced
    anything is, empirically, not useful — and a store that keeps growing while
    its entries never fire is the failure mode this whole layer exists to avoid.
    """
    age_days = 0.0
    reference = entry.last_used_at or entry.created_at or now
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    age_days = max(0.0, (now - reference).total_seconds() / 86400)

    recency = 1.0 / (1.0 + age_days / 30.0)
    usage = min(1.0, entry.use_count / 5.0)
    return 0.45 * entry.confidence + 0.30 * recency + 0.25 * usage


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

    async def _memory_block(self, *, limit: int = MAX_MEMORIES) -> ContextBlock:
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

        now = datetime.now(UTC)
        ranked = sorted(rows, key=lambda entry: _memory_score(entry, now=now), reverse=True)
        chosen = ranked[:limit]

        # Using a memory is what makes it useful next time; record that it was.
        for entry in chosen:
            entry.use_count += 1
            entry.last_used_at = now
        await self.session.flush()

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
        knowledge_chunks: list[dict[str, Any]] | None = None,
        project_digest: str = "",
        include_knowledge: bool = True,
    ) -> AssembledContext:
        """Build the package, trimming by priority if it exceeds the budget."""
        blocks = [
            await self._profile_block(),
            ContextBlock(kind="project", text=project_digest) if project_digest else ContextBlock(kind="project", text=""),
            await self._preference_block(),
            await self._memory_block(),
        ]
        if include_knowledge:
            blocks.append(self._knowledge_block(knowledge_chunks))

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

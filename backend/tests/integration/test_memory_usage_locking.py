"""Two runs must be able to record that they used the same memory.

The bug this pins down was found in `pg_stat_activity` rather than in code: one
session waiting **4m40s** on `UPDATE memory_entries SET last_used_at = ...` while the
holder of the row sat `idle in transaction` in the middle of an LLM call.

Both runs were correct. They simply could not both write the usage counter, because
the write was happening on a session that stays open for the whole run. A run is
minutes long; a row lock held for minutes serialises every other run that happens to
consult the same memory.

The counters are telemetry — they are how a memory that never fires becomes visible —
so they are worth writing and not worth waiting for. These tests assert exactly that:
the write releases immediately, and a contended row cannot stall a run.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from sqlalchemy import select, text, update

from app.ai.memory.assembler import USAGE_LOCK_TIMEOUT_MS, record_memory_usage
from app.infrastructure.database import session_scope
from app.models.context import MemoryEntry
from app.models.user import User


async def _a_user_id() -> uuid.UUID:
    async with session_scope() as session:
        existing = (await session.execute(select(User).limit(1))).scalar_one_or_none()
        if existing is not None:
            return existing.id
        from app.core.security import hash_password

        suffix = uuid.uuid4().hex[:8]
        user = User(
            email=f"lock_{suffix}@aiworkbench.dev",
            username=f"lock_{suffix}",
            display_name="Lock Test",
            hashed_password=hash_password("test1234"),
        )
        session.add(user)
        await session.flush()
        return user.id


async def _a_memory(user_id: uuid.UUID) -> uuid.UUID:
    async with session_scope() as session:
        entry = MemoryEntry(
            user_id=user_id,
            kind="preference",
            scope="global",
            content=f"[lock-test] {uuid.uuid4().hex}",
            confidence=0.9,
            source="test",
            status="active",
        )
        session.add(entry)
        await session.flush()
        return entry.id


@pytest.mark.asyncio
async def test_usage_is_recorded():
    user_id = await _a_user_id()
    memory_id = await _a_memory(user_id)

    await record_memory_usage([memory_id])

    async with session_scope() as session:
        row = await session.get(MemoryEntry, memory_id)
        assert row is not None
        assert row.use_count == 1
        assert row.last_used_at is not None


@pytest.mark.asyncio
async def test_a_contended_row_does_not_stall_the_caller():
    """The regression: a held row must cost a bounded wait, not an unbounded one."""
    user_id = await _a_user_id()
    memory_id = await _a_memory(user_id)

    holder_ready = asyncio.Event()
    release = asyncio.Event()

    async def hold_the_row() -> None:
        async with session_scope() as session:
            # Take the row lock and keep the transaction open, the way a run does
            # while it is waiting on the model.
            await session.execute(
                update(MemoryEntry)
                .where(MemoryEntry.id == memory_id)
                .values(use_count=MemoryEntry.use_count + 1)
            )
            holder_ready.set()
            await release.wait()

    holder = asyncio.create_task(hold_the_row())
    await holder_ready.wait()

    started = time.monotonic()
    await record_memory_usage([memory_id])
    elapsed = time.monotonic() - started

    release.set()
    await holder

    # Bounded by the lock timeout, not by how long the other run takes.
    assert elapsed < (USAGE_LOCK_TIMEOUT_MS / 1000) + 2.0, f"waited {elapsed:.1f}s for a held row"


@pytest.mark.asyncio
async def test_the_counter_is_written_after_the_holder_releases():
    """Giving up on the lock loses one increment, not the run — and it is logged."""
    user_id = await _a_user_id()
    memory_id = await _a_memory(user_id)

    holder_ready = asyncio.Event()
    release = asyncio.Event()

    async def hold_the_row() -> None:
        async with session_scope() as session:
            await session.execute(
                update(MemoryEntry)
                .where(MemoryEntry.id == memory_id)
                .values(use_count=MemoryEntry.use_count + 1)
            )
            holder_ready.set()
            await release.wait()

    holder = asyncio.create_task(hold_the_row())
    await holder_ready.wait()
    await record_memory_usage([memory_id])  # times out, is logged, returns
    release.set()
    await holder

    # And a later, uncontended write works normally.
    await record_memory_usage([memory_id])
    async with session_scope() as session:
        row = await session.get(MemoryEntry, memory_id)
        assert row is not None
        assert row.use_count >= 2  # one from the holder, one from the successful write


@pytest.mark.asyncio
async def test_two_concurrent_writers_both_finish():
    """The actual scenario: two runs consulting the same memory at the same time."""
    user_id = await _a_user_id()
    memory_id = await _a_memory(user_id)

    started = time.monotonic()
    await asyncio.gather(
        record_memory_usage([memory_id]),
        record_memory_usage([memory_id]),
        record_memory_usage([memory_id]),
    )
    elapsed = time.monotonic() - started
    assert elapsed < 10.0, f"three concurrent writers took {elapsed:.1f}s"

    async with session_scope() as session:
        count = await session.scalar(
            text("select use_count from memory_entries where id = :id"), {"id": str(memory_id)}
        )
    assert count >= 1

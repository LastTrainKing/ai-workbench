"""A prompt edit in the code has to actually take effect.

The registry is versioned, and the version that runs is the one in the database. That
is right for reproducibility and it was wrong in one direction: `ensure_seeded` only
inserted templates with **no** database row, so a built-in that had been seeded once
could never be changed by editing `builtin.py` again.

Measured, before this test existed: `conversation_turn` was rewritten to make `answer`
and `start` mutually exclusive, the module-level tests passed, and the running
application kept sending the old text — because version 1 had been written to the
database on the first run. `agent_system` had been in the same state since an earlier
round. A prompt edit that silently does not apply is worse than no registry at all: the
code says one thing and the product does another.

These tests pin both halves of the rule: the code wins for a system-seeded version, and
a person's edit is never touched.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.ai.prompts.builtin import TEMPLATES_BY_NAME
from app.ai.prompts.registry import ensure_seeded
from app.models.ai import Prompt, PromptVersion

NAME = "conversation_turn"


async def _current(session: Any, name: str = NAME) -> PromptVersion:
    row = (
        await session.execute(
            select(PromptVersion)
            .join(Prompt, Prompt.id == PromptVersion.prompt_id)
            .where(Prompt.name == name, PromptVersion.is_current.is_(True))
        )
    ).scalar_one()
    return row


@pytest.mark.asyncio
async def test_a_stale_system_seeded_prompt_is_upgraded(session: Any) -> None:
    """The exact shape of the bug: the row exists, and its text is not the code's."""
    await ensure_seeded(session)
    await session.flush()

    current = await _current(session)
    assert current.content == TEMPLATES_BY_NAME[NAME].content, "seeded content should match"

    # Pretend the code moved on without the database: rewrite the *seeded* version's
    # content to something else, exactly as an older build would have left it.
    current.content = "旧的提示词，和代码里的不一样"
    await session.flush()

    changed = await ensure_seeded(session)
    await session.flush()

    assert changed >= 1, "a system-seeded prompt whose text differs must be upgraded"
    after = await _current(session)
    assert after.content == TEMPLATES_BY_NAME[NAME].content
    assert after.version == current.version + 1, "a new version, not an edit of the old one"


@pytest.mark.asyncio
async def test_the_old_version_is_kept(session: Any) -> None:
    """Runs record the version that produced them; rewriting one makes that a lie."""
    await ensure_seeded(session)
    await session.flush()

    current = await _current(session)
    original = current.content
    original_version = current.version
    current.content = "旧的提示词，和代码里的不一样"
    await session.flush()

    await ensure_seeded(session)
    await session.flush()

    rows = (
        (
            await session.execute(
                select(PromptVersion)
                .join(Prompt, Prompt.id == PromptVersion.prompt_id)
                .where(Prompt.name == NAME)
                .order_by(PromptVersion.version)
            )
        )
        .scalars()
        .all()
    )
    versions = {row.version: row for row in rows}
    assert original_version in versions
    assert versions[original_version].content == "旧的提示词，和代码里的不一样"
    assert versions[original_version].is_current is False
    assert max(versions) == original_version + 1
    assert original  # the seeded text is still what the new version carries


@pytest.mark.asyncio
async def test_a_prompt_a_person_edited_is_left_alone(session: Any) -> None:
    """The reason this can be automatic at all.

    If the code overwrote a hand-edited prompt, nobody could customise one without it
    being reverted on the next restart — so `created_by` is load-bearing, not decoration.
    """
    await ensure_seeded(session)
    await session.flush()

    current = await _current(session)
    current.content = "我自己改过的提示词，不许覆盖"
    current.created_by = "demo"
    await session.flush()

    await ensure_seeded(session)
    await session.flush()

    after = await _current(session)
    assert after.content == "我自己改过的提示词，不许覆盖"
    assert after.version == current.version


@pytest.mark.asyncio
async def test_a_template_that_is_unchanged_is_not_versioned_again(session: Any) -> None:
    """Otherwise every restart would add a version to every prompt."""
    await ensure_seeded(session)
    await session.flush()
    before = await _current(session)

    assert await ensure_seeded(session) == 0
    await session.flush()

    after = await _current(session)
    assert after.version == before.version
    assert after.id == before.id


@pytest.mark.asyncio
async def test_every_builtin_template_is_seeded_and_current(session: Any) -> None:
    """No template may be left un-seeded: `get_prompt` would fall back silently."""
    await ensure_seeded(session)
    await session.flush()

    names = set((await session.execute(select(Prompt.name))).scalars().all())
    missing = set(TEMPLATES_BY_NAME) - names
    assert not missing, f"built-in templates never seeded: {sorted(missing)}"

    for name, template in TEMPLATES_BY_NAME.items():
        current = await _current(session, name)
        assert current.content == template.content, f"{name} is out of date in the database"


@pytest.mark.asyncio
async def test_an_unknown_name_is_not_an_error(session: Any) -> None:
    """A prompt row with no built-in counterpart must not break the sync."""
    await ensure_seeded(session)
    await session.flush()

    orphan = Prompt(name=f"orphan-{uuid.uuid4()}", description="", category="agent")
    session.add(orphan)
    await session.flush()
    session.add(
        PromptVersion(
            prompt_id=orphan.id,
            version=1,
            content="x",
            variables=[],
            is_current=True,
            created_by="system",
        )
    )
    await session.flush()

    # Only the two templates that differ from their seed may be counted; the orphan
    # contributes nothing and must not raise.
    assert await ensure_seeded(session) == 0

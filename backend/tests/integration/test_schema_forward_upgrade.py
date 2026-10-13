"""A release that adds a migration must be installable over the previous one.

**This is a regression test for a defect that shipped.** `ensure_schema` refused to start
when the database's recorded revision differed from head. The reasoning was sound — do not
stamp `head` onto a database nobody migrated, because that makes the record and the
structure disagree. But the same branch caught the ordinary case: 2.4.0 installed over
2.3.0, where the database is simply one revision behind. Measured on the installed copy
after the upgrade: `alembic_version` still said `3f1c9d7a52be`, the `employees` table did
not exist, and the window could not open its own data. A desktop user has no terminal to
run `alembic upgrade head` in.

The fix distinguishes the two: a recorded revision **on the path to head** is a forward
upgrade and is applied (after a restorable copy); anything else is still refused.

These tests use a scratch database so they cannot disturb the suite's own schema.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core import schema_guard
from app.core.schema_guard import (
    SchemaOutOfDateError,
    backup_database,
    find_pg_dump,
    revisions_reachable_from_head,
)

SCRATCH = f"aiworkbench_upgrade_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def scratch_url() -> str:
    return f"postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/{SCRATCH}"


async def _admin(statement: str, *args: object) -> None:
    conn = await asyncpg.connect(
        user="aiworkbench", password="aiworkbench", host="127.0.0.1", port=5432, database="postgres"
    )
    try:
        await conn.execute(statement, *args)
    finally:
        await conn.close()


@pytest.fixture
async def scratch_database(scratch_url: str):
    """A database migrated to the revision **before** the employees table."""
    await _admin(f'CREATE DATABASE "{SCRATCH}"')
    original = schema_guard.settings.database_url
    schema_guard.settings.database_url = scratch_url  # type: ignore[misc]
    try:
        import asyncio

        from alembic import command

        config = schema_guard._alembic_config()
        # One revision behind head, which is exactly the state an installed 2.3.0 is in.
        await asyncio.to_thread(command.upgrade, config, "3f1c9d7a52be")
        yield scratch_url
    finally:
        schema_guard.settings.database_url = original  # type: ignore[misc]
        await _admin(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')


# --------------------------------------------------------------------------- #
# the pieces
# --------------------------------------------------------------------------- #
def test_pg_dump_is_findable_in_this_checkout() -> None:
    """Without it the upgrade is refused, so a missing binary is a real failure."""
    assert find_pg_dump() is not None


def test_the_previous_head_is_on_the_path_to_the_new_one() -> None:
    """The property the fix turns on: 3f1c9d7a52be → a1e5c7b9d204 is a forward step."""
    reachable = revisions_reachable_from_head()
    assert "3f1c9d7a52be" in reachable, "上一个 head 必须仍在 head 的历史路径上"
    assert "a1e5c7b9d204" in reachable


# --------------------------------------------------------------------------- #
# the whole thing
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_database_one_revision_behind_is_upgraded_not_refused(
    scratch_database: str,
) -> None:
    """The defect, as a test: this used to raise."""
    import asyncio

    from sqlalchemy import inspect, text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(scratch_database)
    try:
        async with engine.begin() as conn:
            before = await schema_guard.current_revision(conn)
            assert before == "3f1c9d7a52be"
            assert "employees" not in await schema_guard.table_names(conn)

            outcome = await schema_guard.ensure_schema(conn)

            assert outcome == "upgraded"
            assert "employees" in await schema_guard.table_names(conn)
            assert await schema_guard.current_revision(conn) == schema_guard.head_revision()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_upgrade_takes_a_restorable_copy_first(scratch_database: str) -> None:
    """The copy is taken before, and it is a real dump rather than an empty file."""
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import runtime_root

    engine = create_async_engine(scratch_database)
    try:
        async with engine.begin() as conn:
            await schema_guard.ensure_schema(conn)
    finally:
        await engine.dispose()

    backups = sorted((runtime_root() / "backups").glob("schema-3f1c9d7a52be-to-*.dump"))
    assert backups, "升级前必须留下备份"
    assert backups[-1].stat().st_size > 0, "备份文件不能是空的"


@pytest.mark.asyncio
async def test_a_database_that_is_not_behind_is_still_refused(scratch_database: str) -> None:
    """The original safety property survives: an unknown revision is not guessed at.

    `deadbeef` is not on any path from head, so `upgrade head` could not move it forward —
    it would either do nothing or move it backwards. That is the case the refusal is for.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(scratch_database)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("update alembic_version set version_num = 'deadbeef'"))
        async with engine.begin() as conn:
            with pytest.raises(SchemaOutOfDateError) as caught:
                await schema_guard.ensure_schema(conn)
            assert "历史路径" in str(caught.value)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_refused_upgrade_leaves_the_database_alone(scratch_database: str) -> None:
    """Refusing must not have half-migrated on the way out."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(scratch_database)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("update alembic_version set version_num = 'deadbeef'"))
        async with engine.begin() as conn:
            with pytest.raises(SchemaOutOfDateError):
                await schema_guard.ensure_schema(conn)
        async with engine.begin() as conn:
            assert await schema_guard.current_revision(conn) == "deadbeef"
            assert "employees" not in await schema_guard.table_names(conn)
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# the backup itself
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_backup_produces_a_file_that_is_not_empty(scratch_database: str) -> None:
    path = await __import__("asyncio").to_thread(backup_database, "test")
    assert path.is_file()
    # `-Fc` is the custom format, which starts with the magic bytes "PGDMP".
    assert path.read_bytes()[:5] == b"PGDMP"

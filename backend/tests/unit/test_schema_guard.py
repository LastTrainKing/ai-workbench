"""The schema guard: build a fresh database, verify an existing one, never stamp.

The bug being replaced: startup called ``Base.metadata.create_all`` for missing tables
while Alembic migrations existed and were never run. Four tables were created only that
way, so a database rebuilt from migrations alone did not have them — while
``alembic_version`` reported the head. "The version says up to date" and "the schema is
what the migrations describe" had stopped being the same statement.

The assertions here are about the decisions, not about Alembic:

* a fresh database is built by running the migrations, not by ``create_all``;
* an existing database whose revision differs from head is refused, with both revisions
  named, and **nothing is stamped** — recording a revision nobody applied is the bug;
* an existing database with no ``alembic_version`` at all is refused rather than assumed
  current;
* the real test database, which is built from the models and stamped, verifies.
"""

from __future__ import annotations

import pytest

from app.core import schema_guard
from app.core.schema_guard import SchemaOutOfDateError, ensure_schema, head_revision


def test_there_is_exactly_one_migration_head() -> None:
    """Two heads means `upgrade head` is ambiguous, and the guard must say so rather than pick."""
    head = head_revision()
    assert isinstance(head, str) and head


@pytest.mark.asyncio
async def test_a_fresh_database_is_built_by_running_the_migrations(monkeypatch) -> None:
    """Not by `create_all`. The first thing a new database has must be a real history."""
    called = {"upgrade": 0}

    async def no_tables(_conn):
        return set()

    def fake_upgrade() -> None:
        called["upgrade"] += 1

    monkeypatch.setattr(schema_guard, "table_names", no_tables)
    monkeypatch.setattr(schema_guard, "_upgrade_to_head", fake_upgrade)

    outcome = await ensure_schema(object())
    assert outcome == "created"
    assert called["upgrade"] == 1, "a fresh database was not built from the migrations"


@pytest.mark.asyncio
async def test_a_version_mismatch_is_refused_and_nothing_is_stamped(monkeypatch) -> None:
    """The refusal must not "fix" the version. That is how the drift started."""
    stamped = {"count": 0}

    async def some_tables(_conn):
        return {"users", "tasks"}

    async def old_revision(_conn):
        return "deadbeefdead"

    monkeypatch.setattr(schema_guard, "table_names", some_tables)
    monkeypatch.setattr(schema_guard, "current_revision", old_revision)
    monkeypatch.setattr(
        schema_guard, "_upgrade_to_head", lambda: stamped.__setitem__("count", stamped["count"] + 1)
    )

    with pytest.raises(SchemaOutOfDateError) as excinfo:
        await ensure_schema(object())

    message = str(excinfo.value)
    assert "deadbeefdead" in message, "the recorded revision is not named"
    assert head_revision() in message, "the head revision is not named"
    assert "alembic upgrade head" in message, "no actionable command is given"
    assert stamped["count"] == 0, "the guard tried to upgrade instead of refusing"


@pytest.mark.asyncio
async def test_a_database_without_a_version_is_refused_not_assumed_current(monkeypatch) -> None:
    """Tables but no `alembic_version` is the pre-migration state, and it is not "current"."""

    async def some_tables(_conn):
        return {"users", "tasks", "agent_runs"}

    async def no_version(_conn):
        return None

    monkeypatch.setattr(schema_guard, "table_names", some_tables)
    monkeypatch.setattr(schema_guard, "current_revision", no_version)

    with pytest.raises(SchemaOutOfDateError) as excinfo:
        await ensure_schema(object())

    message = str(excinfo.value)
    assert "alembic_version" in message
    assert "不会自动 stamp" in message


@pytest.mark.asyncio
async def test_a_database_holding_only_alembic_version_counts_as_fresh(monkeypatch) -> None:
    """A lone `alembic_version` table is not a schema, so it must not block the fresh path."""
    built = {"count": 0}

    async def only_version_table(_conn):
        return {"alembic_version"}

    monkeypatch.setattr(schema_guard, "table_names", only_version_table)
    monkeypatch.setattr(
        schema_guard, "_upgrade_to_head", lambda: built.__setitem__("count", built["count"] + 1)
    )

    outcome = await ensure_schema(object())
    assert outcome == "created"
    assert built["count"] == 1


@pytest.mark.asyncio
async def test_the_real_test_database_verifies() -> None:
    """The database the suite runs against is built from the models and stamped at head.

    This is the one case that touches a real connection: if the stamp were missing or the
    head had moved, every API test would fail at startup with a schema error, and it is
    better to find that out here with a message than as a wall of 500s.
    """
    from app.infrastructure.database import get_engine

    async with get_engine().connect() as conn:
        outcome = await ensure_schema(conn)
    assert outcome == "verified"

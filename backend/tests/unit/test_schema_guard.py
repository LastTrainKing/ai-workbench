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
* an existing database with no ``alembic_version`` at all is **adopted** when the only
  difference from the migrations is what is missing, and **refused** when anything else
  differs — "add what is missing" cannot fix a disagreement about something already there;
* the real test database, which is built from the models and stamped, verifies.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table

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
    # The message has to end in something the reader can run.
    #
    # This used to require the literal string `alembic upgrade head`, which was the right
    # advice when every mismatch took the same path. It is the wrong advice for *this*
    # branch — the revision is not reachable from head, so `upgrade head` is exactly what
    # was just refused, and running it against a database ahead of this build either does
    # nothing or moves it backwards. The requirement is "names a command", and the command
    # for an unknown revision is the one that shows you where it sits.
    assert "alembic current" in message, "no actionable command is given"
    assert stamped["count"] == 0, "the guard tried to upgrade instead of refusing"


@pytest.mark.asyncio
async def test_a_database_without_a_version_is_adopted_when_only_things_are_missing(
    monkeypatch,
) -> None:
    """Tables but no `alembic_version` is the pre-migration state, and it can be brought forward.

    A database built before migrations existed is a strict subset of what the migrations
    describe. Refusing it left the user unable to open the product at all; adopting it is
    safe precisely because "add what is missing" cannot destroy anything.
    """
    adopted: dict[str, object] = {}

    async def some_tables(_conn):
        return {"users", "tasks", "agent_runs"}

    async def no_version(_conn):
        return None

    async def fake_adopt(_conn, head):
        adopted["head"] = head
        return {"revision": head, "applied": []}

    monkeypatch.setattr(schema_guard, "table_names", some_tables)
    monkeypatch.setattr(schema_guard, "current_revision", no_version)
    monkeypatch.setattr(schema_guard, "adopt_pre_migration_database", fake_adopt)

    outcome = await ensure_schema(object())
    assert outcome == "adopted"
    assert adopted["head"] == head_revision(), "the revision recorded is not the head"


def test_a_pre_migration_database_is_refused_when_something_disagrees() -> None:
    """A column nobody declares is a disagreement, not a gap, and adoption must not paper over it.

    This is the property that makes the adoption safe to run at startup: it may only ever add.
    A database with an extra column is one where the code and the data have already diverged,
    and guessing which side is right is a decision for a person with a backup.
    """
    metadata = MetaData()
    Table("users", metadata, Column("id", Integer, primary_key=True), Column("email", String(255)))

    reflected = {
        "tables": ["users"],
        "columns": {
            "users": {
                "id": {"name": "id", "type": Integer(), "nullable": False},
                "email": {"name": "email", "type": String(255), "nullable": True},
                "legacy_only": {"name": "legacy_only", "type": String(50), "nullable": True},
            }
        },
    }

    plan = schema_guard._plan_adoption(reflected, metadata)
    assert plan["ok"] is False
    assert any("legacy_only" in problem for problem in plan["problems"]), plan["problems"]


def test_a_column_of_the_wrong_type_is_refused() -> None:
    """`Integer` where the model says `String` is not something "add what is missing" can fix."""
    metadata = MetaData()
    Table("users", metadata, Column("id", Integer, primary_key=True), Column("email", String(255)))

    reflected = {
        "tables": ["users"],
        "columns": {
            "users": {
                "id": {"name": "id", "type": Integer(), "nullable": False},
                "email": {"name": "email", "type": Integer(), "nullable": True},
            }
        },
    }

    plan = schema_guard._plan_adoption(reflected, metadata)
    assert plan["ok"] is False
    assert any("类型不一致" in problem for problem in plan["problems"]), plan["problems"]


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

    **It skips when the infrastructure is not running.** A clean clone has a fresh venv and
    no engines; there is nothing there to verify, and failing this test there made
    `pytest tests/unit` — which the README documents as needing no services — report a red
    suite on a machine that never had the services. Found by running the unit tests in a
    clean clone: 807 passed, 1 failed, and the failure was this test dialling a database
    that did not exist.

    Same shape as `test_embedding_isolation`, which skips on an unreachable Qdrant: three
    states, not two. **"I could not check" is not "it failed".**
    """
    from app.infrastructure.database import get_engine

    try:
        engine = get_engine()
        async with engine.connect() as conn:
            outcome = await ensure_schema(conn)
    except OSError as exc:
        # asyncpg maps a refused connection to `ConnectionRefusedError`, an OSError.
        pytest.skip(f"基础设施没在跑（{type(exc).__name__}）；这一条只在有数据库时才有意义")

    assert outcome == "verified"

"""Migration verification, on a scratch database, with data in it.

Four scenarios, because "the migrations run" is not the same statement as "a database can
be upgraded without losing anything":

1. **Fresh** — an empty database is built by `ensure_schema` and ends up with every table
   the models define. This is the check that would have caught the drift: four tables
   (`conversations`, `messages`, `installed_skills`, `tool_audit_log`) existed only because
   `create_all` made them, so a database built from migrations alone was missing them.
2. **Upgrade** — a database sitting at the *previous* head, with rows in it, is upgraded
   and the rows are still there. The new revision creates four tables that already exist
   on a real installation, so this is also the test that the migration skips rather than
   failing with `DuplicateTable`.
3. **Repeated start** — `ensure_schema` twice in a row both report `verified`. Startup runs
   this every time, so a second run that errored would mean an application that starts once.
4. **Drift** — a table missing a column is *refused*, not silently accepted. Without this
   the guard would be a rubber stamp.

Every scenario uses a scratch database. The development database is never touched:

    .venv/Scripts/python.exe scripts/verify_migration.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PSQL = ROOT / "vendor" / "runtime" / "pgsql" / "pgsql" / "bin" / "psql.exe"

SCRATCH_DB = "aiworkbench_migverify"
SCRATCH_URL = f"postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/{SCRATCH_DB}"
PREVIOUS_HEAD = "90407e6e4bd7"

os.environ["DATABASE_URL"] = SCRATCH_URL
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/1"
os.environ["APP_ENV"] = "test"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["TEST_NO_LLM"] = "1"

sys.path.insert(0, str(BACKEND))

RESULTS: list[tuple[bool, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((bool(ok), label, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def psql(sql: str, *, database: str = "postgres") -> str:
    env = {**os.environ, "PGPASSWORD": "aiworkbench"}
    run = subprocess.run(
        [str(PSQL), "-h", "127.0.0.1", "-U", "aiworkbench", "-d", database, "-tAc", sql],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    return (run.stdout or "").strip()


def reset_scratch() -> None:
    psql(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
    psql(f'CREATE DATABASE "{SCRATCH_DB}"')


def alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PYTHON), "-m", "alembic", *args],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "DATABASE_URL": SCRATCH_URL},
        check=False,
    )


def _model_tables() -> set[str]:
    from app.models import Base

    return set(Base.metadata.tables)


def _db_tables() -> set[str]:
    rows = psql(
        "select table_name from information_schema.tables where table_schema='public'", database=SCRATCH_DB
    )
    return {line.strip() for line in rows.splitlines() if line.strip()} - {"alembic_version"}


async def _ensure() -> str:
    from app.core.schema_guard import ensure_schema
    from app.infrastructure.database import dispose_engine, get_engine

    try:
        async with get_engine().begin() as conn:
            return await ensure_schema(conn)
    finally:
        # In a `finally`, because `ensure_schema` raising is one of the expected outcomes
        # here. Leaving the engine bound to a loop that `asyncio.run` then closes makes the
        # *next* call fail with "Event loop is closed" — an error about the test harness
        # that looks like an error about the migration.
        await dispose_engine()


def main() -> int:
    print("=== 1. fresh database, built by the migrations ===")
    reset_scratch()
    outcome = asyncio.run(_ensure())
    check("ensure_schema built it from the migrations", outcome == "created", outcome)

    model_tables = _model_tables()
    db_tables = _db_tables()
    missing = sorted(model_tables - db_tables)
    check(
        "**every model table exists after a fresh migration**",
        not missing,
        f"missing={missing}" if missing else f"{len(db_tables)} tables",
    )
    check(
        "the four previously-drifting tables are present",
        {"conversations", "messages", "installed_skills", "tool_audit_log"} <= db_tables,
    )
    version = psql("select version_num from alembic_version", database=SCRATCH_DB)
    from app.core.schema_guard import head_revision

    check("the recorded revision is the head", version == head_revision(), f"{version}")

    print("\n=== 2. a database at the previous head, with data, upgraded ===")
    reset_scratch()
    run = alembic("upgrade", PREVIOUS_HEAD)
    before_tables = _db_tables()
    check(
        "the previous head really is missing those four tables",
        not {"conversations", "messages", "installed_skills", "tool_audit_log"} & before_tables,
    )

    psql(
        "insert into users (id, email, username, display_name, hashed_password, is_active, "
        "is_superuser, avatar_color, created_at, updated_at) values "
        "(gen_random_uuid(), 'mig@aiworkbench.dev', 'migverify', 'Mig', 'x', true, false, "
        "'#000', now(), now())",
        database=SCRATCH_DB,
    )
    users_before = psql("select count(*) from users", database=SCRATCH_DB)
    check("a row was written before the upgrade", users_before == "1", f"users={users_before}")

    # The guard must refuse rather than upgrade. This is the whole point of it: an
    # application that silently changes the schema it is about to read is an application
    # whose schema nobody can state. Upgrading is an operator action.
    try:
        asyncio.run(_ensure())
        check("**an out-of-date database is refused at startup**", False, "it was accepted")
    except Exception as exc:  # noqa: BLE001 - the type is the point
        message = str(exc)
        check(
            "**an out-of-date database is refused at startup**",
            PREVIOUS_HEAD in message and "alembic upgrade head" in message,
            f"{type(exc).__name__}",
        )
    check(
        "the refusal did not modify the data",
        psql("select count(*) from users", database=SCRATCH_DB) == "1",
    )

    # The documented path: run the migration, then start.
    run = alembic("upgrade", "head")
    check("`alembic upgrade head` succeeds", run.returncode == 0, run.stderr.strip()[-160:])

    outcome = asyncio.run(_ensure())
    check("the application now starts against it", outcome == "verified", outcome)

    db_tables = _db_tables()
    check(
        "**the four tables were created by the upgrade**",
        {"conversations", "messages", "installed_skills", "tool_audit_log"} <= db_tables,
    )
    users_after = psql("select count(*) from users", database=SCRATCH_DB)
    check("**the row written before the upgrade is still there**", users_after == "1", f"users={users_after}")
    check("the schema now matches the models", not (_model_tables() - db_tables), sorted(_model_tables() - db_tables))

    print("\n=== 3. starting again ===")
    outcome = asyncio.run(_ensure())
    check("a second start reports verified, not an error", outcome == "verified", outcome)
    outcome = asyncio.run(_ensure())
    check("a third start too", outcome == "verified", outcome)

    print("\n=== 4. drift is refused, not accepted ===")
    # The migration's own check, reached by running the migration — not by starting the
    # application, which refuses earlier on the version alone and never gets here.
    psql("alter table installed_skills drop column rendered_body", database=SCRATCH_DB)
    psql(f"update alembic_version set version_num='{PREVIOUS_HEAD}'", database=SCRATCH_DB)
    run = alembic("upgrade", "head")
    output = (run.stdout or "") + (run.stderr or "")
    check("the migration refuses to run", run.returncode != 0, f"exit={run.returncode}")
    check(
        "**it names the table and the missing column**",
        "installed_skills" in output and "rendered_body" in output,
        "the error has to be actionable, not a DuplicateTable",
    )
    check(
        "it did not stamp the version anyway",
        psql("select version_num from alembic_version", database=SCRATCH_DB) == PREVIOUS_HEAD,
        psql("select version_num from alembic_version", database=SCRATCH_DB),
    )

    print("\n=== 5. clean up the scratch database ===")
    psql(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
    check("the scratch database was removed", psql(f"select 1 from pg_database where datname='{SCRATCH_DB}'") == "")

    passed = sum(1 for ok, _, _ in RESULTS if ok)
    print(f"\n=== {passed}/{len(RESULTS)} passed ===")
    for ok, label, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {label} — {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())

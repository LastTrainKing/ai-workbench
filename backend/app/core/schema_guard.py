"""Database schema state: build it from migrations, or refuse to guess.

Two mechanisms were managing the same schema. Alembic migrations existed and were never
run by the application; startup instead called ``Base.metadata.create_all`` for any table
it could not find. The result, measured on a database built from migrations alone:

    model tables 33, alembic-created 29
    missing: conversations, installed_skills, messages, tool_audit_log

Four tables lived outside the migration history while ``alembic_version`` reported the
head. "The version says we are up to date" and "the schema is what the migrations
describe" had stopped being the same statement.

What this module does about it:

* **A database with no tables** is a fresh install. It is built by running
  ``alembic upgrade head`` — not ``create_all`` — so the first thing a new database ever
  has is a migration history that matches it.
* **A database with tables** is checked, not repaired. Its recorded revision must equal
  the head revision. If it does not, startup fails with the two revisions and the command
  that would fix it. It is never stamped: recording a revision nobody applied is how a
  database ends up claiming a schema it does not have, which is the bug this replaces.
* **A database with tables but no ``alembic_version``** is the pre-migration state. It is
  reported as such rather than assumed to be current — the tables may or may not match
  any revision, and that is a question for a person with a backup, not for startup code.

``create_all`` remains in the tests, where the database is dropped and rebuilt on every
run and there is no history to be consistent with.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"


class SchemaOutOfDateError(RuntimeError):
    """The database's schema is not the one the migrations describe.

    Deliberately not a `SystemExit` or a warning: an application that starts against a
    schema it does not understand will fail later, in a place where the cause is much
    harder to see.
    """


def head_revision() -> str:
    """The revision the migration scripts end at."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    if len(heads) != 1:
        raise SchemaOutOfDateError(
            f"迁移脚本有 {len(heads)} 个 head（{sorted(heads)}），无法判断该升到哪个版本。"
            "分支迁移需要先合并。"
        )
    return heads[0]


async def current_revision(conn: Any) -> str | None:
    """The revision the database records, or None if it has never been migrated."""
    tables = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
    if "alembic_version" not in tables:
        return None
    result = await conn.execute(text("select version_num from alembic_version"))
    rows = result.scalars().all()
    if not rows:
        return None
    if len(rows) > 1:
        raise SchemaOutOfDateError(
            f"alembic_version 有 {len(rows)} 行（{sorted(rows)}），数据库同时处于多个版本，"
            "需要人工核对。"
        )
    return str(rows[0])


async def table_names(conn: Any) -> set[str]:
    return await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))


def _upgrade_to_head() -> None:
    """Run the migrations. Blocking, so it is called from a worker thread."""
    from alembic.config import Config

    from alembic import command

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


async def ensure_schema(conn: Any) -> str:
    """Bring a fresh database up to head, or verify an existing one. Returns what happened.

    Returns one of ``"created"``, ``"verified"`` or ``"empty"`` so the caller can log the
    outcome without re-deriving it.
    """
    import asyncio

    existing = await table_names(conn)
    # `alembic_version` alone does not count as a schema — a database that has only that
    # table has no user tables and is treated as fresh.
    user_tables = existing - {"alembic_version"}

    if not user_tables:
        logger.info("schema_fresh_database", extra={"alembic_head": head_revision()})
        await asyncio.to_thread(_upgrade_to_head)
        return "created"

    recorded = await current_revision(conn)
    head = head_revision()

    if recorded is None:
        raise SchemaOutOfDateError(
            "数据库里已经有 "
            f"{len(user_tables)} 张表，但没有 alembic_version 记录，"
            "说明它是在引入迁移之前建的。无法判断它与哪一版迁移一致，"
            "因此不会自动 stamp。请先备份，再人工确认后执行 "
            "`alembic stamp <正确的版本>`，然后 `alembic upgrade head`。"
        )

    if recorded != head:
        raise SchemaOutOfDateError(
            f"数据库结构版本是 {recorded}，迁移脚本的 head 是 {head}，两者不一致。"
            f"请先执行 `alembic upgrade head`（当前库：{settings.database_url.split('@')[-1]}）。"
            "启动不会自动升级，也不会把版本号直接改成 head —— 那样只会让记录和实际结构继续分家。"
        )

    logger.info("schema_verified", extra={"revision": recorded, "tables": len(user_tables)})
    return "verified"


__all__ = [
    "ALEMBIC_INI",
    "SchemaOutOfDateError",
    "current_revision",
    "ensure_schema",
    "head_revision",
    "table_names",
]

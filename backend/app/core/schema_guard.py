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
  **adopted** if — and only if — every difference from the migrations is an *addition*:
  tables that do not exist yet, columns that do not exist yet. The missing pieces are
  created from the model metadata, the revision is recorded, and the database is at head
  because it genuinely has everything head describes. If anything else differs — a column
  of the wrong type, a table nobody declares, a nullability that disagrees — it is refused
  with the specific list, because "add what is missing" cannot fix a disagreement about
  something that is already there.

  This is not stamping a revision nobody applied. It is applying the revision's *effect*
  and then recording it, which is the only order in which the record is true.

``create_all`` remains in the tests, where the database is dropped and rebuilt on every
run and there is no history to be consistent with.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text

from app.core.config import PROJECT_ROOT, runtime_root, settings
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


def _alembic_config() -> Any:
    from alembic.config import Config

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


def _upgrade_to_head() -> None:
    """Run the migrations. Blocking, so it is called from a worker thread."""
    from alembic import command

    command.upgrade(_alembic_config(), "head")


def revisions_reachable_from_head() -> set[str]:
    """Every revision on the path from head down to base.

    Used to tell a **forward** upgrade — the database is behind, and running the migrations
    moves it forward — from a database on another branch or ahead of this build. The second
    must still be refused: `upgrade head` against it would either do nothing or move it
    backwards.
    """
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    return {revision.revision for revision in script.walk_revisions()}


def find_pg_dump() -> Path | None:
    """`pg_dump` from this copy's own runtime, or from PATH. None if there is neither."""
    import shutil

    candidates = [
        # The frozen layout: `_internal` sits beside `vendor/`, and PROJECT_ROOT is the
        # directory holding both.
        PROJECT_ROOT / "vendor" / "runtime" / "pgsql" / "pgsql" / "bin" / "pg_dump.exe",
        PROJECT_ROOT / "vendor" / "runtime" / "pgsql" / "bin" / "pg_dump.exe",
        PROJECT_ROOT / "vendor" / "pgsql" / "pgsql" / "bin" / "pg_dump.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which("pg_dump")
    return Path(found) if found else None


def backup_database(reason: str) -> Path:
    """Take a restorable copy of the database **before** changing its structure.

    Before, not after. A migration that goes wrong and a migration whose backup was taken
    afterwards are the same event with different amounts of recovery available, and the
    point of a local-first product is that the copy is the user's.

    Raises when no `pg_dump` can be found rather than proceeding without a copy: an app
    that will not start is recoverable, a database migrated with no way back is not.
    """
    import subprocess
    import time
    from urllib.parse import urlparse

    binary = find_pg_dump()
    if binary is None:
        raise SchemaOutOfDateError(
            "需要先升级数据库结构，但找不到 pg_dump，无法在改动前留下可恢复的副本。"
            "请确认安装目录下 vendor 中的 pgsql 带有 pg_dump，或把它加入 PATH，然后重新启动。"
        )

    target_dir = runtime_root() / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"schema-{reason}-{time.strftime('%Y%m%d-%H%M%S')}.dump"

    parsed = urlparse(settings.database_url.replace("+asyncpg", ""))
    completed = subprocess.run(
        [
            str(binary),
            "-h", parsed.hostname or "127.0.0.1",
            "-p", str(parsed.port or 5432),
            "-U", parsed.username or "aiworkbench",
            "-d", (parsed.path or "/aiworkbench").lstrip("/"),
            "-Fc",
            "-f", str(target),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PGPASSWORD": parsed.password or ""},
        timeout=600,
        check=False,
    )
    if completed.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
        raise SchemaOutOfDateError(
            f"备份数据库失败（pg_dump 退出码 {completed.returncode}），因此没有升级结构。"
            f"数据库保持原样。诊断信息：{(completed.stderr or '').strip()[:300]}"
        )
    logger.info("schema_backup_taken", extra={"path": str(target), "bytes": target.stat().st_size})
    return target


#: Names PostgreSQL treats as synonyms. Compared after normalisation, because the two sides
#: arrive by different routes: metadata declares `Float`, which compiles to `FLOAT`;
#: reflection reports what PostgreSQL calls it, which is `DOUBLE PRECISION`. They are the
#: same type, and reporting a difference would refuse a database that is fine.
_TYPE_ALIASES = {
    "FLOAT": "DOUBLE PRECISION",
    "FLOAT8": "DOUBLE PRECISION",
    "INT": "INTEGER",
    "INT4": "INTEGER",
    "BOOL": "BOOLEAN",
    "DECIMAL": "NUMERIC",
}


def _type_signature(sqlalchemy_type: Any) -> str:
    """What the type is called in DDL, with PostgreSQL's synonyms folded together.

    Compared as DDL rather than as Python classes because the two sides arrive differently:
    metadata declares `DateTime(timezone=True)`, reflection reports `TIMESTAMP(timezone=True)`,
    and both compile to `TIMESTAMP WITH TIME ZONE`. Comparing the classes would report a
    difference that PostgreSQL does not have.
    """
    from sqlalchemy.dialects import postgresql

    try:
        signature = str(sqlalchemy_type.compile(dialect=postgresql.dialect())).upper().strip()
    except Exception:
        signature = str(sqlalchemy_type).upper().strip()
    return _TYPE_ALIASES.get(signature, signature)


def _default_literal(column: Any) -> tuple[str | None, str]:
    """The SQL default to give a column that is being added to a table with rows in it.

    Returns ``(literal, "")`` or ``(None, why_not)``.

    The model metadata is not always a faithful description of what the migrations built:
    `tool_calls.mutates_state` is `default=False` in the model and `server_default=sa.false()`
    in the migration. A column added to a table that already has rows needs the *database-side*
    default, so this falls back to the model's own scalar default and refuses only when the
    default is a callable with no SQL form.
    """
    from sqlalchemy import literal
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.sql.elements import TextClause

    dialect = postgresql.dialect()
    server = getattr(column, "server_default", None)
    if server is not None and getattr(server, "arg", None) is not None:
        if isinstance(server.arg, TextClause):
            return str(server.arg), ""
        try:
            return (
                str(server.arg.compile(dialect=dialect, compile_kwargs={"literal_binds": True})),
                "",
            )
        except Exception:
            return str(server.arg), ""

    default = getattr(column, "default", None)
    if default is None:
        return None, "模型里没有声明默认值"
    if getattr(default, "is_callable", False) or callable(getattr(default, "arg", None)):
        return None, "默认值是一个函数，没有可直接写进 DDL 的形式"
    try:
        return (
            str(literal(default.arg).compile(dialect=dialect, compile_kwargs={"literal_binds": True})),
            "",
        )
    except Exception as exc:
        return None, f"默认值无法写成 SQL 字面量（{type(exc).__name__}）"


def _add_column_sql(column: Any) -> tuple[str | None, str]:
    """The ``ADD COLUMN`` clause for a missing column, or why it cannot be added."""
    from sqlalchemy.dialects import postgresql

    dialect = postgresql.dialect()
    parts = [f'"{column.name}" {column.type.compile(dialect=dialect)}']
    if not column.nullable:
        parts.append("NOT NULL")
    default, why = _default_literal(column)
    if default is not None:
        parts.append(f"DEFAULT {default}")
    elif not column.nullable:
        # A NOT NULL column with no usable default cannot be added to a table that has rows.
        # Saying which column and why beats letting PostgreSQL report it three layers down.
        return None, f"{column.name} 不可空但没有可用的默认值（{why}），无法加到已有数据的表上"
    return " ".join(parts), ""


def _plan_adoption(reflected: dict[str, Any], metadata: Any) -> dict[str, Any]:
    """What adoption would do, or why it will not.

    Pure: it reads two descriptions and returns a plan. Everything that decides whether a
    database may be touched is here, so it can be tested without a database and read without
    a transaction.
    """
    db_tables: set[str] = set(reflected["tables"])
    db_columns: dict[str, dict[str, Any]] = reflected["columns"]
    declared = set(metadata.tables)

    undeclared_tables = sorted(db_tables - declared - {"alembic_version"})
    missing_tables = sorted(declared - db_tables)

    problems: list[str] = []
    if undeclared_tables:
        problems.append(
            "这些表在数据库里存在，但模型里没有声明：" + ", ".join(undeclared_tables)
        )

    missing_columns: list[tuple[str, str, str]] = []
    for table in sorted(db_tables & declared):
        for column in metadata.tables[table].columns:
            found = db_columns.get(table, {}).get(column.name)
            if found is None:
                clause, why = _add_column_sql(column)
                if clause is None:
                    problems.append(f"{table}：{why}")
                else:
                    missing_columns.append((table, column.name, clause))
                continue
            if _type_signature(found["type"]) != _type_signature(column.type):
                problems.append(
                    f"{table}.{column.name} 类型不一致：数据库是 "
                    f"{_type_signature(found['type'])}，模型是 {_type_signature(column.type)}"
                )
            elif bool(found["nullable"]) != bool(column.nullable):
                problems.append(
                    f"{table}.{column.name} 可空性不一致：数据库 "
                    f"{'可空' if found['nullable'] else '不可空'}，模型 "
                    f"{'可空' if column.nullable else '不可空'}"
                )

    for table in sorted(db_tables & declared):
        for name in sorted(set(db_columns.get(table, {})) - {c.name for c in metadata.tables[table].columns}):
            problems.append(f"{table}.{name} 在数据库里存在，但模型里没有声明")

    return {
        "ok": not problems,
        "problems": problems,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
    }


async def adopt_pre_migration_database(conn: Any, head: str) -> dict[str, Any]:
    """Bring a pre-migration database to head when the only difference is what is missing.

    Called by ``ensure_schema`` when the database has tables and no ``alembic_version``.
    Raises ``SchemaOutOfDateError`` listing every disagreement if the difference is anything
    other than an addition.
    """
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable

    from app.models import Base

    metadata = Base.metadata
    dialect = postgresql.dialect()

    def reflect(sync_conn: Any) -> dict[str, Any]:
        inspector = inspect(sync_conn)
        tables = inspector.get_table_names()
        return {
            "tables": tables,
            "columns": {t: {c["name"]: c for c in inspector.get_columns(t)} for t in tables},
            "indexes": {t: {i["name"] for i in inspector.get_indexes(t)} for t in tables},
        }

    reflected = await conn.run_sync(reflect)
    plan = _plan_adoption(reflected, metadata)

    if not plan["ok"]:
        raise SchemaOutOfDateError(
            "这个数据库建于迁移机制之前，而且它与模型之间的差异不只是「缺东西」，"
            "因此不会自动改动。逐项如下：\n  - "
            + "\n  - ".join(plan["problems"])
            + "\n\n请先备份，再人工核对。"
        )

    applied: list[str] = []

    # `exec_driver_sql` rather than `text`: this is DDL, and `text()` would look for bind
    # parameters in default expressions and quoted identifiers.
    for table in plan["missing_tables"]:
        await conn.exec_driver_sql(str(CreateTable(metadata.tables[table]).compile(dialect=dialect)))
        applied.append(f"建表 {table}")

    for table, column, clause in plan["missing_columns"]:
        await conn.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS {clause}')
        applied.append(f"加列 {table}.{column}")

    # Indexes are separate DDL and are not emitted by `CreateTable`. Compared by name, which
    # is how SQLAlchemy names them and how the migrations create them.
    for table in sorted(set(metadata.tables) & (set(reflected["tables"]) | set(plan["missing_tables"]))):
        present = reflected["indexes"].get(table, set())
        for index in sorted(metadata.tables[table].indexes, key=lambda i: i.name or ""):
            if index.name and index.name not in present:
                await conn.exec_driver_sql(str(CreateIndex(index).compile(dialect=dialect)))
                applied.append(f"建索引 {index.name}")

    # The revision is recorded last, in the same transaction as the changes above. If any of
    # them failed, this never runs and the database keeps saying "no revision" — which is
    # true, and is what makes the next attempt retry rather than skip.
    await conn.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS alembic_version ("
        "version_num VARCHAR(32) NOT NULL, "
        "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
    )
    await conn.exec_driver_sql("DELETE FROM alembic_version")
    await conn.exec_driver_sql(f"INSERT INTO alembic_version (version_num) VALUES ('{head}')")
    applied.append(f"记录版本 {head}")

    logger.warning(
        "schema_adopted_pre_migration_database",
        extra={"revision": head, "applied": applied},
    )
    return {"revision": head, "applied": applied}


async def ensure_schema(conn: Any) -> str:
    """Bring a fresh database up to head, adopt a pre-migration one, or verify an existing one.

    Returns one of ``"created"``, ``"adopted"``, ``"verified"`` or ``"empty"`` so the caller
    can log the outcome without re-deriving it.
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
        await adopt_pre_migration_database(conn, head)
        return "adopted"

    if recorded != head:
        # **A forward upgrade is not the thing this used to refuse.**
        #
        # The refusal exists so the recorded revision and the actual structure cannot drift
        # apart — stamping `head` onto a database that was never migrated is exactly the
        # lie it prevents. But it also caught the ordinary case: a release that adds a
        # migration, installed over a database one revision behind. A desktop user has no
        # terminal to run `alembic upgrade head` in, and the first release to add a
        # migration left them with an app that refused to open its own data. Measured:
        # after installing 2.4.0 over 2.3.0, `alembic_version` still said `3f1c9d7a52be`
        # and the new table did not exist.
        #
        # So: if the recorded revision is on the path to head, run the real migrations —
        # with a restorable copy taken first. If it is not (another branch, or a database
        # ahead of this build), the original refusal stands unchanged.
        reachable = await asyncio.to_thread(revisions_reachable_from_head)
        if recorded not in reachable:
            # **The message has to end in a command.** It used to say "请人工核对" and stop,
            # which tells the reader that something is wrong and nothing about what to do —
            # and this is the branch where the reader has no idea which side is behind.
            # `alembic upgrade head` is deliberately *not* the advice here: it is what was
            # just refused, and running it against a database ahead of this build either
            # does nothing or moves it backwards.
            raise SchemaOutOfDateError(
                f"数据库结构版本是 {recorded}，迁移脚本的 head 是 {head}，"
                "且前者不在后者的历史路径上——可能来自另一条分支，或比这份程序更新。"
                f"先跑 `alembic current` 和 `alembic history` 看清它在哪条线上"
                f"（当前库：{settings.database_url.split('@')[-1]}）；"
                "如果这份程序才是落后的那一边，升级程序，而不是把数据库降级。"
            )

        backup = await asyncio.to_thread(backup_database, f"{recorded}-to-{head}")
        logger.info(
            "schema_forward_upgrade",
            extra={"from_revision": recorded, "to_revision": head, "backup": str(backup)},
        )
        await asyncio.to_thread(_upgrade_to_head)

        # Read it back rather than assume. If the upgrade did not land, the caller hears
        # about it now rather than on the first query that needs the new table.
        after = await current_revision(conn)
        if after != head:
            raise SchemaOutOfDateError(
                f"迁移执行后版本号仍是 {after}，期望 {head}。"
                f"数据库已备份到 {backup}，请人工核对。"
            )
        return "upgraded"

    logger.info("schema_verified", extra={"revision": recorded, "tables": len(user_tables)})
    return "verified"


__all__ = [
    "ALEMBIC_INI",
    "SchemaOutOfDateError",
    "adopt_pre_migration_database",
    "current_revision",
    "ensure_schema",
    "head_revision",
    "table_names",
]

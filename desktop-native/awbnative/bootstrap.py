"""First-run initialisation, and an honest report of what the machine is missing.

A packaged desktop app has one job before it shows anything: make sure the things it needs
exist. Two of them are its own — the data directory, and a database schema built from the
migrations — and those it can create. The rest are services the product runs against
(PostgreSQL, Redis, Qdrant), and those it cannot create; it can only say so clearly.

**Why this is a separate module and not three lines in `main`.** The distinction between
"this application is broken" and "this machine is missing a service" is the difference
between a bug report and an installation instruction, and it is the distinction an
acceptance run has to be able to make. Collapsing both into "startup failed" produces a
window that does not open and a user with nothing to act on.

So the result is structured, with three states rather than two:

* **ok** — the thing works.
* **blocked** — the thing is missing from the environment. Not a defect in this build.
* **failed** — the thing is present and did not work. This is a defect.

`main` treats `blocked` as reportable and continues; `failed` stops.
"""

from __future__ import annotations

from typing import Any

from awbnative import resources


def _describe(exc: BaseException) -> str:
    """An exception and its whole cause chain, as one line.

    The top exception is often the least informative one. `SSLError: [SSL] system lib` says
    nothing about *why* the TLS handshake failed; the OpenSSL error that caused it is one or
    two `__context__` links further down. A packaged application has no console and no
    traceback, so this is the only place that detail can surface.
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)


async def _probe_tls(host: str, port: int, user: str, password: str, database: str) -> dict[str, Any]:
    """Try a raw connection with and without TLS, and say which worked.

    A packaged build failed to reach PostgreSQL with `SSLError` while the same code from a
    checkout connected, and the top-level error said only "system lib". Rather than guess,
    this asks the question directly: does this process's TLS stack complete a handshake
    against this server at all?

    Kept in the report because it is the difference between "the database is down" and "this
    build cannot negotiate TLS with the database", and those need different answers from
    whoever is looking at it.
    """
    import asyncpg

    results: dict[str, Any] = {}
    for label, ssl_arg in (("plain", False), ("tls", "prefer")):
        try:
            conn = await asyncpg.connect(
                host=host, port=port, user=user, password=password, database=database,
                ssl=ssl_arg, timeout=8,
            )
            await conn.execute("select 1")
            await conn.close()
            results[label] = "ok"
        except Exception as exc:  # noqa: BLE001
            results[label] = _describe(exc)[:200]
    return results


async def _check_database() -> dict[str, Any]:
    """Reach PostgreSQL and bring the schema to head.

    `ensure_schema` is the right entry point and not `create_all`: a fresh database is
    built by running the migrations, so the first thing it ever has is a migration history
    that matches it. An existing one is verified against the head revision and refused if
    it disagrees. A database built before migrations existed is **adopted** when the only
    difference is what it is missing — otherwise the user cannot open their own data, which
    is what the refusal meant in practice.

    The reported `schema=` value is one of `created`, `adopted`, `verified`, so a support
    conversation can tell which of the three happened.
    """
    from app.core.schema_guard import SchemaOutOfDateError, ensure_schema
    from app.infrastructure.database import get_engine, session_scope
    from sqlalchemy import text

    try:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.execute(text("select 1"))
            schema = await ensure_schema(conn)
    except SchemaOutOfDateError as exc:
        return {"state": "failed", "detail": _describe(exc), "schema": ""}
    except Exception as exc:  # noqa: BLE001 - the message is the report
        detail = {
            "state": "blocked",
            "detail": (
                f"无法连接数据库（{_describe(exc)}）。"
                "原生版需要 PostgreSQL；请先启动它，或在 .env 中设置 DATABASE_URL。"
            ),
            "schema": "",
        }
        # Only when the configured URL is loopback: probing a remote host with TLS disabled
        # is not something a diagnostic should do on its own.
        from urllib.parse import urlparse

        from app.core.config import settings

        parsed = urlparse(settings.database_url.replace("+asyncpg", ""))
        if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
            detail["tls_probe"] = await _probe_tls(
                parsed.hostname or "127.0.0.1",
                parsed.port or 5432,
                parsed.username or "aiworkbench",
                parsed.password or "",
                (parsed.path or "/aiworkbench").lstrip("/") or "aiworkbench",
            )
        return detail

    try:
        from app.models.user import User
        from sqlalchemy import func, select

        async with session_scope() as session:
            accounts = int(
                (await session.execute(select(func.count()).select_from(User))).scalar()
                or 0
            )
    except Exception as exc:  # noqa: BLE001
        return {
            "state": "failed",
            "detail": f"数据库可用但查询失败：{exc}",
            "schema": schema,
        }

    return {
        "state": "ok",
        "detail": f"schema={schema}, accounts={accounts}",
        "schema": schema,
        "accounts": accounts,
    }


async def _check_redis() -> dict[str, Any]:
    from app.core.cache import get_redis
    from app.core.config import settings

    if not settings.cache_enabled:
        return {"state": "ok", "detail": "缓存已在配置中关闭"}
    try:
        await get_redis().ping()
    except Exception as exc:  # noqa: BLE001
        return {
            "state": "blocked",
            "detail": f"Redis 不可用（{type(exc).__name__}）：{settings.redis_url}。"
            "没有它应用仍可启动，但缓存与后台任务不可用。",
        }
    return {"state": "ok", "detail": settings.redis_url.split("@")[-1]}


async def _check_qdrant() -> dict[str, Any]:
    """A reachability probe, deliberately not a client.

    Building the real client here would make a missing vector store a startup failure. It
    is not one: the product degrades to keyword search, and that degradation is the
    product's decision to make, not this module's.

    The API key is sent when one is configured. Without it a secured Qdrant answers `401`,
    and reporting that as "Qdrant 不可达" would be wrong in the way that matters: it would
    tell the user to go and start a service that is already running.
    """
    import httpx
    from app.core.config import settings

    url = settings.qdrant_url.rstrip("/") + "/collections"
    headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url, headers=headers)
        if response.status_code in (401, 403):
            return {
                "state": "blocked",
                "detail": f"{url} -> HTTP {response.status_code}：Qdrant 在运行，但本次请求被拒绝"
                "（未配置 API key 或 key 不匹配）",
            }
        if response.status_code >= 400:
            return {
                "state": "blocked",
                "detail": f"{url} -> HTTP {response.status_code}",
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "state": "blocked",
            "detail": f"Qdrant 不可达（{type(exc).__name__}）：{settings.qdrant_url}。"
            "语义检索将退化为关键词检索。",
        }
    return {"state": "ok", "detail": settings.qdrant_url}


def _runtime_facts() -> dict[str, Any]:
    """Which interpreter and which TLS library this process is actually running.

    Added because a packaged build failed to reach PostgreSQL with
    `SSLError: [SSL] system lib` while the same code from a checkout connected fine — and
    there was nothing in the report that could tell the two apart. "Which OpenSSL is this
    build using?" is the first question any TLS problem asks, and a desktop application has
    no console to answer it from. It costs two lines and one call.
    """
    import ssl
    import sys

    facts: dict[str, Any] = {
        "python": sys.version.split()[0],
        "openssl": ssl.OPENSSL_VERSION,
        "executable": sys.executable,
    }
    try:
        ssl.create_default_context()
        facts["default_tls_context"] = "ok"
    except Exception as exc:  # noqa: BLE001
        facts["default_tls_context"] = f"{type(exc).__name__}: {exc}"
    return facts


async def _reconcile() -> dict[str, Any]:
    """Close what the last process left open. **Nothing is re-run.**

    Imported lazily and by name: the reconciliation lives beside the HTTP application's
    startup because that is where it was written, and importing the whole application
    module here would pull the web stack into a build that deliberately does not serve
    anything.

    A failure is reported, not raised. The interface has a database and a model and can
    be used; refusing to open because housekeeping failed would trade a small unknown
    for a large one.
    """
    try:
        from app.main import _reconcile_interrupted_runs

        await _reconcile_interrupted_runs()
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - the message is the report
        # 不写日志，写进报告 —— 这个模块里每一条失败都是这么处理的，`ensure_ready` 的返回
        # 值就是它唯一的出口，多一条旁路只会让"发生了什么"有两个地方可查。
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def ensure_ready() -> dict[str, Any]:
    """Run every first-run check and return one structured report.

    The data directory is created here rather than assumed. In a packaged build the user's
    data lives outside the program directory, and on a first run that directory does not
    exist yet — an application that assumes otherwise fails on exactly the machines that
    have never run it.
    """
    report: dict[str, Any] = {
        "paths": {
            "frozen": resources.is_frozen(),
            "bundle_root": str(resources.bundle_root()),
            "data_dir": str(resources.data_dir()),
            "version": resources.version(),
        },
        "runtime": _runtime_facts(),
    }

    target = resources.data_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
        report["paths"]["data_dir_ready"] = True
    except OSError as exc:
        report["paths"]["data_dir_ready"] = False
        report["paths"]["data_dir_error"] = f"{type(exc).__name__}: {exc}"

    report["database"] = await _check_database()
    report["redis"] = await _check_redis()
    report["qdrant"] = await _check_qdrant()

    # 收尾上一次进程留下的中间状态：没结束的运行、卡在「工作中」的员工。
    #
    # **这一段此前不存在，而原因是结构性的，不是疏忽。** 对账逻辑写在
    # `app.main.lifespan` 里 —— 那是 FastAPI 的启动钩子。桌面版故意不起 HTTP 服务
    # （bridge 直接进程内调服务，见 `bridge.py` 开头），所以 lifespan 在产品里从不执行。
    # 开发时它跑（那里起 API），测试里它跑，产品里不跑。于是这类修复看起来"已经做了"。
    #
    # 放在 `ensure_ready` 里，因为它是每次启动的必经之路，而且它已经在做同一类事情 ——
    # "把上一次留下的状态和这一次对齐"。
    report["reconcile"] = await _reconcile()

    report["blocked"] = [
        name
        for name in ("database", "redis", "qdrant")
        if report[name]["state"] == "blocked"
    ]
    report["failed"] = [
        name
        for name in ("database", "redis", "qdrant")
        if report[name]["state"] == "failed"
    ]
    if not report["paths"]["data_dir_ready"]:
        report["failed"].append("data_dir")
    report["ready"] = not report["failed"]
    return report


def summarise(report: dict[str, Any]) -> list[str]:
    """The report as human-readable lines, in the order a person should read them."""
    lines = [
        f"版本 {report['paths']['version']}",
        f"数据目录 {report['paths']['data_dir']}"
        + ("" if report["paths"].get("data_dir_ready") else "（无法创建）"),
    ]
    labels = {"database": "数据库", "redis": "Redis", "qdrant": "Qdrant"}
    for name, label in labels.items():
        entry = report.get(name) or {}
        lines.append(f"{label}: {entry.get('state', '?')} — {entry.get('detail', '')}")
    for name in report.get("blocked", []):
        lines.append(
            f"  [BLOCKED] {labels.get(name, name)} 在本次环境中不可用，应用会降级运行"
        )
    for name in report.get("failed", []):
        lines.append(f"  [FAIL] {labels.get(name, name)} 存在但不可用")
    return lines


__all__ = ["ensure_ready", "summarise"]

"""Command line entry point.

Usage::

    python -m app.cli serve [--reload] [--port 8000]
    python -m app.cli worker [--concurrency 2]
    python -m app.cli seed
    python -m app.cli check
    python -m app.cli reindex --knowledge-base <uuid>
    python -m app.cli stats
    python -m app.cli create-user --username alice --email a@b.com --password secret123
    python -m app.cli init-env
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


async def _seed() -> None:
    from app.ai.prompts.registry import ensure_seeded
    from app.infrastructure.database import session_scope
    from app.services.auth_service import AuthService

    async with session_scope() as session:
        created = await ensure_seeded(session)
        user = await AuthService(session).ensure_demo_user()
    print(f"[+] prompt templates seeded: {created}")
    print(f"[+] demo account ready: {user.username} / demo1234")


async def _check() -> int:
    from app.ai.providers.factory import check_provider_health
    from app.core.cache import ping as redis_ping
    from app.infrastructure.database import healthcheck
    from app.infrastructure.vector_store import get_vector_store

    db_ok, db_detail = await healthcheck()
    redis_ok = await redis_ping()
    qdrant_ok, qdrant_detail = await get_vector_store().ping()
    providers = await check_provider_health()

    print(f"[{'OK' if db_ok else 'FAIL'}] PostgreSQL  {db_detail[:80]}")
    print(f"[{'OK' if redis_ok else 'FAIL'}] Redis")
    print(f"[{'OK' if qdrant_ok else 'FAIL'}] Qdrant      {qdrant_detail}")
    llm = providers.get("llm", {})
    emb = providers.get("embedding", {})
    print(f"[{'OK' if not llm.get('degraded') else 'WARN'}] LLM         "
          f"provider={llm.get('kind')} model={llm.get('model')}")
    if llm.get("degraded"):
        print(f"        reason: {llm.get('reason')}")
    print(f"[{'OK' if not emb.get('degraded') else 'WARN'}] Embedding   "
          f"provider={emb.get('kind')} model={emb.get('model')}")
    if emb.get("degraded"):
        print(f"        reason: {emb.get('reason')}")
    return 0 if (db_ok and redis_ok and qdrant_ok) else 1


async def _stats() -> None:
    from sqlalchemy import func, select

    from app.infrastructure.database import session_scope
    from app.models.ai import AIRun, Prompt
    from app.models.knowledge import Document, DocumentChunk, KnowledgeBase
    from app.models.project import Project
    from app.models.task import Task
    from app.models.user import User

    async with session_scope() as session:
        rows = [
            ("users", User),
            ("projects", Project),
            ("knowledge_bases", KnowledgeBase),
            ("documents", Document),
            ("document_chunks", DocumentChunk),
            ("tasks", Task),
            ("prompts", Prompt),
            ("ai_runs", AIRun),
        ]
        print(f"{'table':<18}{'rows':>8}")
        print("-" * 26)
        for name, model in rows:
            count = (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
            print(f"{name:<18}{int(count):>8}")


async def _reindex(kb_id: str) -> None:
    from app.domain.enums import JobType
    from app.infrastructure.database import session_scope
    from app.repositories.ai_repo import JobRepository

    async with session_scope() as session:
        job = await JobRepository(session).enqueue(
            job_type=JobType.REINDEX_KNOWLEDGE_BASE.value,
            payload={"knowledge_base_id": kb_id},
            priority=20,
            max_attempts=2,
        )
        print(f"[+] reindex job queued: {job.id}")
        print("    the running worker will pick it up within a second")


async def _create_user(username: str, email: str, password: str, display_name: str) -> None:
    from app.infrastructure.database import session_scope
    from app.schemas.auth import UserCreate
    from app.services.auth_service import AuthService

    async with session_scope() as session:
        user, _, _ = await AuthService(session).register(
            UserCreate(
                username=username,
                email=email,
                password=password,
                display_name=display_name or username,
            )
        )
    print(f"[+] user created: {user.username} <{user.email}> id={user.id}")


def _init_env(root: Path | None) -> int:
    """Create the production environment file, or leave an existing one alone.

    Runs on every launch, so "already there" must mean "untouched" — rotating the key
    each time would sign the user out of their own machine.
    """
    from app.core.config import PROJECT_ROOT
    from app.core.production_env import ensure_production_env, env_problems

    target_root = root or PROJECT_ROOT
    written = ensure_production_env(target_root)
    if written is not None:
        print(f"[+] wrote {written} with a new random SECRET_KEY (APP_ENV=prod, DEBUG=false)")
        return 0

    env_path = target_root / ".env"
    problems = env_problems(env_path)
    print(f"[=] {env_path} already exists; left untouched")
    if problems:
        # Not a failure: this is a development checkout or a hand-edited install, and
        # stopping the launcher over it would be worse than saying so.
        print(f"[!] it is not production-safe: {', '.join(problems)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="AI Workbench CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    worker = sub.add_parser("worker", help="run the background worker only")
    worker.add_argument("--concurrency", type=int, default=None)

    sub.add_parser("seed", help="seed prompt templates and the demo account")
    sub.add_parser("check", help="verify every external dependency")
    sub.add_parser("stats", help="print row counts per table")

    reindex = sub.add_parser("reindex", help="queue a full re-index of a knowledge base")
    reindex.add_argument("--knowledge-base", required=True)

    create = sub.add_parser("create-user", help="create an account")
    create.add_argument("--username", required=True)
    create.add_argument("--email", required=True)
    create.add_argument("--password", required=True)
    create.add_argument("--display-name", default="")

    init_env = sub.add_parser(
        "init-env",
        help="write a production .env on first run; never overwrites an existing one",
    )
    init_env.add_argument(
        "--root", default="", help="project root to write into; defaults to this checkout"
    )

    args = parser.parse_args(argv)
    configure_logging("cli")

    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "app.main:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
            log_config=None,
            access_log=False,
        )
        return 0

    if args.command == "worker":
        from app.worker.runner import run_worker_blocking

        run_worker_blocking(args.concurrency)
        return 0

    if args.command == "seed":
        asyncio.run(_seed())
        return 0
    if args.command == "check":
        return asyncio.run(_check())
    if args.command == "stats":
        asyncio.run(_stats())
        return 0
    if args.command == "reindex":
        try:
            uuid.UUID(args.knowledge_base)
        except ValueError:
            print(f"[x] '{args.knowledge_base}' is not a valid UUID", file=sys.stderr)
            return 2
        asyncio.run(_reindex(args.knowledge_base))
        return 0
    if args.command == "create-user":
        asyncio.run(
            _create_user(args.username, args.email, args.password, args.display_name)
        )
        return 0
    if args.command == "init-env":
        return _init_env(Path(args.root) if args.root else None)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

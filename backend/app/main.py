"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger, new_request_id, request_id_var
from app.infrastructure.database import dispose_engine, get_engine, session_scope
from app.infrastructure.vector_store import close_vector_store

logger = get_logger(__name__)

DESCRIPTION = """
**AI Workbench** — a local-first AI knowledge & task execution platform.

The product closes one loop:

`资料 → 知识 → 任务 → 执行 → 结果 → 再沉淀为知识`

Key capabilities
* **Knowledge ingestion** — Markdown / TXT / PDF → parse → normalise → chunk → embed → Qdrant
* **Grounded RAG** — query rewrite → hybrid retrieval (dense + BM25-style sparse) → lexical rerank
  → citation-enforced answer. The model is never allowed to answer without sources.
* **Task planner** — goal → phase → task → subtask tree with estimates, dependencies and
  verifiable completion criteria.
* **Tool-calling agent** — a bounded ReAct loop over a typed tool registry; the agent can only
  reach the database through tools.
* **AI execution trace** — every run records prompt name *and version*, retrieved sources,
  tool calls, token usage, latency, retries and errors.
* **Provider abstraction** — OpenAI-compatible, Ollama (fully local) or a deterministic offline
  engine; switching is a configuration change, never a code change.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("api")
    settings.ensure_dirs()
    logger.info(
        "application_starting",
        extra={
            "env": settings.app_env,
            "ai_provider": settings.ai_provider,
            "embedding_provider": settings.embedding_provider,
            "debug": settings.debug,
        },
    )

    # Fail fast with a clear message if the schema is missing.
    await _bootstrap_schema()

    worker_task: asyncio.Task[None] | None = None
    if settings.worker_enabled:
        from app.worker.runner import Worker

        worker = Worker()
        worker_task = asyncio.create_task(worker.start())
        app.state.worker = worker
        logger.info("in_process_worker_started")

    try:
        yield
    finally:
        if worker_task is not None:
            app.state.worker.request_stop()
            try:
                await asyncio.wait_for(worker_task, timeout=20)
            except (TimeoutError, asyncio.CancelledError):
                worker_task.cancel()
                logger.warning("worker_shutdown_timeout")
        await close_vector_store()
        await dispose_engine()
        logger.info("application_stopped")


async def _bootstrap_schema() -> None:
    """Create tables and seed reference data (prompts, demo account).

    Alembic owns schema *evolution*; this bootstrap keeps a fresh clone usable
    without an extra migration step, and is idempotent.
    """
    from sqlalchemy import inspect

    from app.ai.prompts.registry import ensure_seeded
    from app.models import Base
    from app.services.auth_service import AuthService

    try:
        async with get_engine().begin() as conn:
            existing = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
            missing = set(Base.metadata.tables) - existing
            if missing:
                logger.info(
                    "creating_missing_tables", extra={"count": len(missing), "tables": sorted(missing)}
                )
                await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:
        logger.error("schema_bootstrap_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        raise

    try:
        async with session_scope() as session:
            seeded = await ensure_seeded(session)
            if seeded:
                logger.info("prompt_templates_seeded", extra={"count": seeded})
            auth = AuthService(session)
            if await auth.user_count() == 0:
                await auth.ensure_demo_user()
    except Exception as exc:
        logger.error("seed_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        raise


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        contact={"name": "AI Workbench", "url": "https://github.com/"},
        license_info={"name": "MIT"},
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Process-Time-Ms"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def observability(request: Request, call_next):
        """Attach a request id and log one structured line per request."""
        incoming = request.headers.get("X-Request-ID")
        request_id = incoming or new_request_id()
        request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = int((time.perf_counter() - started) * 1000)
            logger.exception(
                "request_failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration,
                },
            )
            raise
        duration = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-Ms"] = str(duration)
        level = logger.warning if response.status_code >= 400 else logger.info
        level(
            "request_completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration,
                "client": request.client.host if request.client else "-",
            },
        )
        request_id_var.set("-")
        return response

    register_exception_handlers(app)

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "name": settings.app_name,
                "version": settings.app_version,
                "docs": "/docs",
                "api": settings.api_v1_prefix,
                "health": f"{settings.api_v1_prefix}/health",
            }
        )

    return app


app = create_app()

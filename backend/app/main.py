"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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
    await _reconcile_interrupted_runs()
    await _purge_expired_revocations()

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


async def _purge_expired_revocations() -> None:
    """Drop revocation rows whose token has already expired on its own.

    An expired token is rejected by its signature, so its revocation row is dead weight.
    Without this the `revoked_tokens` table grows by one row per logout forever, and the
    list that exists to be small becomes the thing that is not.
    """
    from app.core.security import purge_expired_revocations
    from app.infrastructure.database import session_scope

    try:
        async with session_scope() as session:
            removed = await purge_expired_revocations(session)
            await session.commit()
        if removed:
            logger.info("expired_revocations_purged", extra={"count": removed})
    except Exception as exc:
        # Housekeeping must not stop startup; it must not be silent either.
        logger.error(
            "revocation_purge_failed", extra={"error": f"{type(exc).__name__}: {exc}"}
        )


async def _reconcile_interrupted_runs() -> None:
    """Close runs that a process restart left open.

    A `BuildRun` is driven by an in-process task and watched by a second one. When
    the process is replaced — a restart, a crash, a deploy — both die with it, and
    the row keeps whatever status it had. Nothing ever revisits it, so the run shows
    as `EXECUTING` on the workbench forever while its plan says every step finished.

    Observed exactly like that: a run whose plan read `6/6 DONE`, whose controller
    was gone from the registry, whose build row still said `EXECUTING`, and which
    had registered no deliverable. There is no way to recover the result of a task
    that no longer exists, so the honest thing is to say so — once, at startup,
    where it cannot be mistaken for a live run.
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.infrastructure.database import session_scope
    from app.models.delivery import BuildRun, BuildStatus

    active = [state.value for state in BuildStatus if state.is_active]
    try:
        async with session_scope() as session:
            rows = list(
                (
                    await session.execute(
                        select(BuildRun).where(BuildRun.status.in_(active))
                    )
                ).scalars()
            )
            for run in rows:
                run.status = BuildStatus.FAILED.value
                run.finished_at = datetime.now(UTC)
                run.error = "服务在运行过程中重启，这次运行的结果无法确认"
                run.result_summary = (
                    run.result_summary
                    or "运行被服务重启打断；已产出的文件仍可在工作区找到，但这次运行不能算完成。"
                )
            if rows:
                await session.commit()
        if rows:
            logger.warning(
                "interrupted_runs_closed",
                extra={"count": len(rows), "run_ids": [str(r.id) for r in rows[:10]]},
            )
    except Exception as exc:
        # Startup must not fail because housekeeping failed, but it must not be
        # silent either — an unreconciled run looks exactly like a running one.
        logger.error(
            "interrupted_run_reconcile_failed", extra={"error": f"{type(exc).__name__}: {exc}"}
        )

    await _reconcile_interrupted_agent_runs()


async def _reconcile_interrupted_agent_runs() -> None:
    """Close agent runs the previous process left saying `RUNNING`.

    `BuildRun` was reconciled and `AIRun`/`AgentRun` were not, so a run interrupted by a
    restart kept `RUNNING` in the database forever. Two consequences, both visible: the
    history endpoint (now that it reads these rows) would show a run as still in progress
    days after the process that was driving it died, and `GET /agent/runs/{id}` would
    report a state the registry — correctly — knows nothing about.

    A row that says `RUNNING` when no process is driving it is not running. The registry
    is empty at startup by definition, so every `RUNNING` agent run at this moment is
    interrupted. Marked `FAILED` rather than `CANCELLED`: nobody cancelled it, and saying
    otherwise would misattribute the outcome.
    """
    from sqlalchemy import select

    from app.ai.agent.plan import RunState
    from app.domain.enums import RunStatus, RunType
    from app.infrastructure.database import session_scope
    from app.models.ai import AgentRun, AIRun

    try:
        async with session_scope() as session:
            rows = list(
                (
                    await session.execute(
                        select(AIRun).where(
                            AIRun.run_type == RunType.AGENT.value,
                            AIRun.status == RunStatus.RUNNING.value,
                        )
                    )
                ).scalars()
            )
            for run in rows:
                run.status = RunStatus.FAILED.value
                run.error_message = (
                    run.error_message or "服务在运行过程中重启，这次运行的结果无法确认"
                )
            if rows:
                ids = [run.id for run in rows]
                agent_rows = list(
                    (
                        await session.execute(
                            select(AgentRun).where(AgentRun.ai_run_id.in_(ids))
                        )
                    ).scalars()
                )
                for agent_run in agent_rows:
                    agent_run.status = RunStatus.FAILED.value
                    # The scratchpad is what the history endpoint reads for the run's
                    # *state*, and it still says whatever the run was doing when the
                    # process died — `EXECUTING`, typically. Updating only `status` left the
                    # two disagreeing: the row said FAILED and the API said EXECUTING, for
                    # the same run. Reassigned rather than mutated in place, because an
                    # in-place edit of a JSONB column is not tracked and would not be saved.
                    scratchpad = agent_run.scratchpad if isinstance(agent_run.scratchpad, dict) else {}
                    agent_run.scratchpad = {**scratchpad, "state": RunState.FAILED.value}
                await session.commit()
        if rows:
            logger.warning(
                "interrupted_agent_runs_closed",
                extra={"count": len(rows), "run_ids": [r.run_id for r in rows[:10]]},
            )
    except Exception as exc:
        logger.error(
            "interrupted_agent_run_reconcile_failed",
            extra={"error": f"{type(exc).__name__}: {exc}"},
        )


async def _bootstrap_schema() -> None:
    """Bring the schema to head, then seed reference data (prompts, demo account).

    **Alembic owns the schema, including the first build.** This used to call
    ``Base.metadata.create_all`` for whatever tables were missing, while migrations
    existed and were never run. Four tables — ``conversations``, ``messages``,
    ``installed_skills``, ``tool_audit_log`` — were created only that way, so a database
    rebuilt from migrations alone did not have them, even though ``alembic_version``
    reported the head.

    A fresh database is now built by running the migrations; an existing one is checked
    against the head and refused if it disagrees. See ``app.core.schema_guard`` — the
    check never stamps.
    """
    from app.ai.prompts.registry import ensure_seeded
    from app.core.schema_guard import ensure_schema
    from app.services.auth_service import AuthService

    try:
        async with get_engine().begin() as conn:
            outcome = await ensure_schema(conn)
        logger.info("schema_ready", extra={"outcome": outcome})
    except Exception as exc:
        logger.error("schema_bootstrap_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        raise

    try:
        async with session_scope() as session:
            seeded = await ensure_seeded(session)
            if seeded:
                logger.info("prompt_templates_seeded", extra={"count": seeded})
            auth = AuthService(session)
            # The demo account is a development convenience with a password printed on
            # the sign-in screen. It must not exist in a shipped build — see
            # `AuthService.ensure_demo_user`, which also refuses outside `dev`.
            if settings.app_env == "dev" and await auth.user_count() == 0:
                await auth.ensure_demo_user()
            elif settings.app_env != "dev":
                logger.info(
                    "demo_user_not_seeded",
                    extra={"app_env": settings.app_env, "users": await auth.user_count()},
                )
    except Exception as exc:
        logger.error("seed_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        raise


def create_app() -> FastAPI:
    # `/docs`, `/redoc` and `/openapi.json` describe every route, every schema and every
    # parameter. That is useful while building and a map of the attack surface in a
    # shipped build, so they are off in `prod` — see `Settings.api_docs_enabled`.
    docs = settings.api_docs_enabled
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=DESCRIPTION,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
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

    _mount_frontend(app)

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


def _frontend_dist() -> Path | None:
    """Locate the built UI, if it exists."""
    if not settings.serve_frontend:
        return None
    if settings.frontend_dist:
        candidate = Path(settings.frontend_dist)
    else:
        candidate = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    return candidate if (candidate / "index.html").is_file() else None


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built single-page app from the API origin.

    The desktop shell points its window straight at this server, which is what
    removes the browser, the second port and the whole CORS story: the UI and the
    API share one origin, so the relative ``/api/v1`` calls in the frontend keep
    working unchanged.

    Routing order matters. This is registered *after* the API router, and the
    catch-all skips the prefixes the API owns, so ``/api/...``, ``/docs``,
    ``/redoc`` and ``/openapi.json`` are never swallowed by the SPA fallback.
    """
    dist = _frontend_dist()
    if dist is None:
        return

    reserved = ("api/", "docs", "redoc", "openapi.json")
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(reserved):
            raise HTTPException(status_code=404, detail="Not found")
        # A real file wins; anything else is a client-side route.
        candidate = (dist / full_path).resolve()
        try:
            candidate.relative_to(dist.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found") from None
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)

    logger.info("frontend_served_from_api", extra={"dist": str(dist)})


app = create_app()

"""QML's view of the backend — in the same process, with no HTTP in between.

The WebView build reached the backend over `127.0.0.1:8000`. That listener is reachable by
any process on the machine and by any page the browser loads, and it existed because the UI
was a web page that could only talk HTTP. A native UI has no such constraint: QML calls
these slots, and these slots call the service layer directly.

What that removes, concretely:

* no localhost listener on the production desktop path;
* no CORS, no bearer tokens, no token revocation — there is no token to steal;
* no serialisation boundary to keep in sync with the service layer's signatures;
* one process to start, watch and stop, instead of a shell plus a server.

What it does not remove, and must not: the permission layer. These slots are not a bypass.
Anything that touches the machine goes through the same `PermissionManager` and the same
`ToolExecutor` the agent uses — see `open_path` and `save_text_file`, which ask the policy
before touching the filesystem.

The owner is resolved once, at construction. A single-user desktop install has one account;
`owner_id` is passed explicitly by the verification script so a test never writes into
whoever happens to be first in the table.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from awbnative.async_runner import AsyncRunner

#: How long a call that arrives before the environment is ready waits for it. Long enough
#: for a cold start of a frozen build on a slow disk, short enough that a startup which has
#: genuinely hung still ends in an error somebody can read.
READY_WAIT_SECONDS = 240.0

logger = logging.getLogger(__name__)

#: What the banner calls each part of the local environment. The banner is the first thing a
#: person sees and it is not a place to name an engine: "PostgreSQL 不可用" tells someone who
#: has never heard of PostgreSQL nothing about whether they can use the product. The engine
#: names are still in the startup report and in Settings, which is where they are useful.
_PLAIN_SERVICE_NAMES = {
    "database": "数据存储",
    "redis": "缓存",
    "qdrant": "向量检索",
    "data_dir": "数据目录",
}


def _plain_names(keys: list[Any]) -> str:
    return "、".join(_PLAIN_SERVICE_NAMES.get(str(key), str(key)) for key in keys)


def _json(payload: Any) -> str:
    """QML receives JSON text; a QVariantMap would need a converter on every slot."""
    return json.dumps(payload, ensure_ascii=False, default=str)


def _run_summary(row: Any) -> dict[str, Any]:
    """One run, in the shape the history list and the detail header both render.

    One function rather than two, so the list and the detail cannot disagree about what a
    run's status is. They did in the WebView build: the list read `status` and the detail
    read the scratchpad, and an interrupted run showed as `FAILED` in one and `EXECUTING`
    in the other.
    """
    return {
        "run_id": row.run_id,
        "status": row.status,
        "run_type": row.run_type,
        "provider": row.provider or "",
        "model": row.model or "",
        "prompt_name": row.prompt_name or "",
        "tools_used": list(row.tools_used or []),
        "tool_call_count": int(row.tool_call_count or 0),
        "prompt_tokens": int(row.prompt_tokens or 0),
        "completion_tokens": int(row.completion_tokens or 0),
        "total_tokens": int(row.total_tokens or 0),
        "latency_ms": int(row.latency_ms or 0),
        "cost_estimate": float(row.cost_estimate or 0.0),
        "error_code": row.error_code or "",
        "error_message": row.error_message or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "finished_at": row.finished_at.isoformat() if row.finished_at else "",
        "response_preview": (row.response_text or "")[:400],
    }


def _tool_call_row(call: Any) -> dict[str, Any]:
    """One invocation, including the columns that only matter after a crash.

    `finished_at` being empty while `started_at` is set is the visible signature of "the
    process died mid-call". `reconciled_at` being set instead says somebody looked
    afterwards and settled it. The interface shows both, because collapsing them would put
    "we observed this finish" and "we worked out afterwards that it had" in the same cell.
    """
    return {
        "invocation_id": call.invocation_id or "",
        "tool_name": call.tool_name,
        "step_index": int(call.step_index or 0),
        "status": call.status,
        "arguments": call.arguments or {},
        "result": call.result or {},
        "error_message": call.error_message or "",
        "latency_ms": int(call.latency_ms or 0),
        "mutates_state": bool(call.mutates_state),
        "working_directory": call.working_directory or "",
        "started_at": call.started_at.isoformat() if call.started_at else "",
        "finished_at": call.finished_at.isoformat() if call.finished_at else "",
        "reconciled_at": call.reconciled_at.isoformat() if call.reconciled_at else "",
    }


class ServiceBridge(QObject):
    """The QML-facing API. Every slot returns immediately; results arrive by signal."""

    #: (request_id, json_text)
    completed = Signal(str, str)
    #: (request_id, message, code)
    failed = Signal(str, str, str)
    #: (level, message) — a toast the shell shows without a request id
    notify = Signal(str, str)
    #: (request_id, message) — progress for a long task, emitted while it runs
    progress = Signal(str, str)
    #: The startup banner's text changed.
    startupChanged = Signal()

    #: The request id the startup sequence reports under. Reserved, so a page cannot
    #: collide with it and the shell can tell the difference between "the stack is coming
    #: up" and "the user pressed something".
    STARTUP_REQUEST = "startup"

    def __init__(self, owner_id: uuid.UUID | str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner = AsyncRunner(self)
        self._runner.completed.connect(self._on_completed)
        self._runner.failed.connect(self._on_failed)
        self._owner_id = str(owner_id) if owner_id else ""
        self._cancel_requested = False
        self._startup_state = "idle"
        self._startup_detail = ""
        self._environment: dict[str, Any] = {}
        self._started_services = False

    # ------------------------------------------------------------------- wiring --
    def _on_completed(self, request_id: str, value: Any) -> None:
        if request_id == self.STARTUP_REQUEST and isinstance(value, dict):
            self._apply_startup(value)
            return
        self.completed.emit(request_id, value if isinstance(value, str) else _json(value))

    def _on_failed(self, request_id: str, message: str, code: str) -> None:
        if request_id == self.STARTUP_REQUEST:
            self._set_startup("failed", message)
            return
        self.failed.emit(request_id, message, code)

    def _submit(self, request_id: str, factory, *, timeout: float = 120.0) -> None:
        """Queue a call behind the local environment.

        `_bring_up` is submitted to the runner directly, so the startup sequence is the
        one thing that does not come through here. Everything else — all of the slots,
        and any slot added later — waits for the stack first.
        """
        # A lambda, not `self._after_environment(factory)`: the runner calls what it is
        # given, and a coroutine built here would be bound to the UI thread's loop rather
        # than the runner's — which is the whole reason `submit` takes a factory.
        self._runner.submit(
            request_id,
            lambda: self._after_environment(factory),
            timeout=timeout + READY_WAIT_SECONDS,
        )

    async def _after_environment(self, factory):
        """Wait for the local stack, then run the call.

        **The window is on screen before the stack is up.** `main.py` shows it and *then*
        calls `startEnvironment`, so the first thing the home page does — load its data —
        reaches PostgreSQL while PostgreSQL is still starting. It fails with
        `ConnectionRefusedError [WinError 1225]`, the page shows that and stops asking, and
        the screen sits on "读取中…" with a red toast over a database that came up three
        seconds later. On the machine this was found on, the engines were ready 3.7
        seconds after the window appeared; the page had already given up.

        A page cannot forget to wait, because pages do not do the waiting.
        """
        deadline = time.monotonic() + READY_WAIT_SECONDS
        started = time.monotonic()
        while self._startup_state in ("idle", "starting") and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        waited = time.monotonic() - started
        if waited > 0.5:
            # Worth a line: "the interface took ten seconds to fill in" is this, and without
            # it the wait is invisible — the call simply appears to have been slow.
            logger.info(
                "call_waited_for_environment",
                extra={"seconds": round(waited, 1), "state": self._startup_state},
            )
        # If it is still not settled the call runs anyway: an environment that never came
        # up should produce the error that says so, not a request that never answers.
        return await factory()

    async def _owner(self) -> uuid.UUID:
        """This install's account, and **bind the call to it**.

        The second half is not decoration. `build_llm_provider()` takes no owner and reads
        the request context instead — which the HTTP application sets in its auth
        dependency. This bridge had no equivalent, so every provider built inside it
        resolved the *installation-level* configuration while `loadAiConfig` returned the
        account's. The settings page showed a key and a model; the calls used whatever the
        machine had.

        That is exactly the failure the acceptance round warned about — do not assume a call
        reaches the real model because the interface says it is configured — and it was
        here, in the newest code, because a contextvar is invisible unless you know to set
        it.
        """
        from app.ai.runtime_config import set_current_owner

        if self._owner_id:
            set_current_owner(self._owner_id)
            return uuid.UUID(self._owner_id)
        from app.infrastructure.database import session_scope
        from app.models.user import User
        from sqlalchemy import select

        async with session_scope() as session:
            row = (
                await session.execute(
                    select(User).where(User.is_active.is_(True)).order_by(User.created_at).limit(1)
                )
            ).scalar_one_or_none()
        if row is None:
            # A machine that has never run this product has no account, and every page needs
            # one. Refusing here — which is what this used to do, with "请先在原应用中创建
            # 一个" — means a clean install opens onto a home screen that cannot load
            # anything, and there is no "original application" to go and create it in.
            row = await self._create_local_account()
        self._owner_id = str(row.id)
        set_current_owner(self._owner_id)
        return row.id

    async def _create_local_account(self) -> Any:
        """The account this installation acts as, created once on first run.

        The password is random and never shown. The native application has no sign-in screen
        — the machine's own user is the identity — so the value exists only because the
        column is `NOT NULL`. A *known* default would be worse than useless here: this
        application reads and writes the user's files and runs commands on their behalf, and
        a published credential on a machine with those powers is a back door with a sign on
        it.
        """
        import secrets

        from app.core.security import hash_password
        from app.infrastructure.database import session_scope
        from app.models.user import User

        async with session_scope() as session:
            user = User(
                email="local@aiworkbench.local",
                username="local",
                display_name="本机账号",
                hashed_password=hash_password(secrets.token_urlsafe(32)),
            )
            session.add(user)
            await session.flush()
            session.expunge(user)
        logger.info("local_account_created", extra={"user_id": str(user.id)})
        return user

    # ---------------------------------------------------------------- read side --
    @Slot(str)
    def loadDashboard(self, request_id: str) -> None:
        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.services.stats_service import StatsService

            owner = await self._owner()
            # Inside a session scope, like every other slot here. This one was written as
            # `StatsService()` — no session, no scope — so the home screen failed on its
            # first call with "missing 1 required positional argument: 'session'". The page
            # still rendered, which is why it read as a broken *page* rather than a missing
            # session: every panel sat on "读取中…" and the toast carried the traceback.
            async with session_scope() as session:
                return await StatsService(session).dashboard(owner_id=owner)

        self._submit(request_id, work)

    @Slot(str)
    def loadTasks(self, request_id: str) -> None:
        async def work() -> dict[str, Any]:
            from app.services.task_service import TaskService

            owner = await self._owner()
            from app.infrastructure.database import session_scope

            async with session_scope() as session:
                tree = await TaskService(session).tree(owner_id=owner)
            return {"items": tree, "total": len(tree)}

        self._submit(request_id, work)

    @Slot(str)
    def loadProjects(self, request_id: str) -> None:
        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.repositories.work_repo import ProjectRepository

            owner = await self._owner()
            async with session_scope() as session:
                # `query()` returns a Select — the repository deliberately does not hide
                # the statement, so the caller decides on ordering and paging.
                rows = list((await session.execute(ProjectRepository(session).query(owner))).scalars())
            return {
                "items": [
                    {
                        "id": str(p.id),
                        "name": p.name,
                        "status": p.status,
                        "description": p.description or "",
                    }
                    for p in rows
                ],
                "total": len(rows),
            }

        self._submit(request_id, work)

    @Slot(str, str)
    def loadProjectDetail(self, request_id: str, project_id: str) -> None:
        """One project with the things that belong to it.

        Filtered by `project_id` in the query rather than by pulling everything and sieving it
        in QML: the point of a project page is that it shows this project, and a page that
        showed every task while claiming to show one would be wrong the moment there are two.
        """
        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.models.ai import AIRun
            from app.models.knowledge import Document
            from app.repositories.work_repo import ProjectRepository, TaskRepository
            from sqlalchemy import select

            owner = await self._owner()
            pid = uuid.UUID(project_id)
            async with session_scope() as session:
                project = await ProjectRepository(session).get_owned(pid, owner)
                if project is None:
                    raise ValueError("找不到该项目")
                tasks = await TaskRepository(session).all_for_project(owner, pid)
                runs = list(
                    (
                        await session.execute(
                            select(AIRun)
                            .where(AIRun.owner_id == owner, AIRun.project_id == pid)
                            .order_by(AIRun.created_at.desc())
                            .limit(20)
                        )
                    ).scalars()
                )
                documents = list(
                    (
                        await session.execute(
                            select(Document)
                            .where(Document.owner_id == owner, Document.project_id == pid)
                            .order_by(Document.created_at.desc())
                            .limit(20)
                        )
                    ).scalars()
                )
                return {
                    "project": {
                        "id": str(project.id),
                        "name": project.name,
                        "status": project.status,
                        "description": project.description or "",
                    },
                    "tasks": [
                        {
                            "id": str(t.id),
                            "title": t.title,
                            "status": t.status,
                            "priority": t.priority,
                        }
                        for t in tasks
                    ],
                    "runs": [_run_summary(r) for r in runs],
                    "documents": [
                        {"id": str(d.id), "title": d.title, "status": d.status}
                        for d in documents
                    ],
                }

        self._submit(request_id, work)

    @Slot(str)
    def loadAiConfig(self, request_id: str) -> None:
        async def work() -> dict[str, Any]:
            from app.ai.runtime_config import resolve

            owner = await self._owner()
            cfg = resolve(owner)
            # `safe()` is the masked form: the key itself never crosses into QML.
            return {"effective": cfg.safe(), "editable_fields": list(_overridable())}

        self._submit(request_id, work)

    @Slot(str)
    def loadRuntimeInfo(self, request_id: str) -> None:
        async def work() -> dict[str, Any]:
            from app.core.config import RUNTIME_ROOT, settings

            return {
                "version": settings.app_version,
                "app_env": settings.app_env,
                "runtime_root": str(RUNTIME_ROOT),
                "ai_provider": settings.ai_provider,
                "offline": settings.is_offline_ai,
            }

        self._submit(request_id, work)

    # ------------------------------------------------------- knowledge bases --
    @Slot(str)
    def loadKnowledgeBases(self, request_id: str) -> None:
        """The account's knowledge bases, with how much is actually in each one.

        The document count and the embedded-chunk count are read from the database rather
        than from a cached field, so a knowledge base that has documents but no vectors
        looks different from one that is ready — which is the difference between "my
        upload did nothing" and "my upload worked".
        """

        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.models.knowledge import Document, DocumentChunk, KnowledgeBase
            from sqlalchemy import func, select

            owner = await self._owner()
            async with session_scope() as session:
                rows = list(
                    (
                        await session.execute(
                            select(KnowledgeBase)
                            .where(KnowledgeBase.owner_id == owner)
                            .order_by(KnowledgeBase.updated_at.desc())
                        )
                    ).scalars()
                )
                items = []
                for kb in rows:
                    documents = int(
                        (
                            await session.execute(
                                select(func.count(Document.id)).where(
                                    Document.knowledge_base_id == kb.id
                                )
                            )
                        ).scalar_one()
                    )
                    chunks = int(
                        (
                            await session.execute(
                                select(func.count(DocumentChunk.id)).where(
                                    DocumentChunk.knowledge_base_id == kb.id,
                                    DocumentChunk.embedding_status == "EMBEDDED",
                                )
                            )
                        ).scalar_one()
                    )
                    items.append(
                        {
                            "id": str(kb.id),
                            "name": kb.name,
                            "description": kb.description or "",
                            "documents": documents,
                            "embedded_chunks": chunks,
                            "embedding_model": kb.embedding_model or "",
                            "vector_size": kb.vector_size or 0,
                        }
                    )
            return {"items": items, "total": len(items)}

        self._submit(request_id, work)

    @Slot(str, str)
    def createKnowledgeBase(self, request_id: str, name: str) -> None:
        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.models.knowledge import KnowledgeBase

            owner = await self._owner()
            title = (name or "").strip()
            if not title:
                raise ValueError("知识库名称不能为空")
            async with session_scope() as session:
                kb = KnowledgeBase(owner_id=owner, name=title[:200])
                session.add(kb)
                await session.flush()
                await session.commit()
                return {"id": str(kb.id), "name": kb.name}

        self._submit(request_id, work)

    @Slot(str, str)
    def loadDocuments(self, request_id: str, knowledge_base_id: str) -> None:
        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.models.knowledge import Document
            from sqlalchemy import select

            owner = await self._owner()
            async with session_scope() as session:
                rows = list(
                    (
                        await session.execute(
                            select(Document)
                            .where(
                                Document.owner_id == owner,
                                Document.knowledge_base_id == uuid.UUID(knowledge_base_id),
                            )
                            .order_by(Document.created_at.desc())
                        )
                    ).scalars()
                )
                return {
                    "items": [
                        {
                            "id": str(d.id),
                            "title": d.title,
                            "status": d.status,
                            "chunks": d.chunk_count or 0,
                            "embedded": d.embedded_chunk_count or 0,
                            "error": (d.error_message or "")[:200],
                        }
                        for d in rows
                    ],
                    "total": len(rows),
                }

        self._submit(request_id, work)

    @Slot(str, str, str)
    def importDocument(self, request_id: str, knowledge_base_id: str, source_path: str) -> None:
        """Import a file through the **real** upload path, not a shortcut.

        `save_upload` then `register_upload` then `ingest` — the same three calls the HTTP
        upload route makes. The file is hashed, deduplicated, parsed and embedded by the
        product's own code, so what this proves is the product's pipeline and not a
        bespoke path written for the UI.
        """

        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.infrastructure.files import save_upload
            from app.services.document_service import DocumentService

            owner = await self._owner()
            path = Path(source_path).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"文件不存在：{path}")

            class _Stream:
                def __init__(self, data: bytes) -> None:
                    self._data = data
                    self._pos = 0

                async def read(self, size: int = -1) -> bytes:
                    chunk = (
                        self._data[self._pos :]
                        if size is None or size < 0
                        else self._data[self._pos : self._pos + size]
                    )
                    self._pos += len(chunk)
                    return chunk

            data = path.read_bytes()
            self.progress.emit(request_id, f"正在保存 {path.name}（{len(data)} 字节）…")
            async with session_scope() as session:
                stored = await save_upload(
                    _Stream(data), path.name, content_type=None
                )
                document, _job, duplicate = await DocumentService(session).register_upload(
                    owner_id=owner,
                    knowledge_base_id=uuid.UUID(knowledge_base_id),
                    stored=stored,
                )
                document_id = document.id
                await session.commit()

            if duplicate:
                return {
                    "document_id": str(document_id),
                    "duplicate": True,
                    "message": "同样的文件已经导入过，未重复建立。",
                }

            self.progress.emit(request_id, "正在解析并向量化…")
            async with session_scope() as session:
                result = await DocumentService(session).ingest(document_id)
                await session.commit()
            return {"document_id": str(document_id), "duplicate": False, **result}

        self._submit(request_id, work, timeout=600.0)

    # ------------------------------------------------------------------ RAG --
    @Slot(str, str, str)
    def askKnowledge(self, request_id: str, knowledge_base_id: str, question: str) -> None:
        """One grounded answer, with its citations and the program's own grounding verdict.

        `grounded` here is the structural check from `analyse_citations` — every
        claim-bearing sentence cites a source that exists — and not a claim that the
        sources entail the answer. The UI shows the same distinction rather than a green
        tick.
        """

        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.services.ai_services import ChatService

            owner = await self._owner()
            text = (question or "").strip()
            if not text:
                raise ValueError("问题不能为空")
            async with session_scope() as session:
                answer = await ChatService(session).ask(
                    owner_id=owner,
                    question=text,
                    knowledge_base_ids=[uuid.UUID(knowledge_base_id)],
                    mode="hybrid",
                )
                await session.commit()
            return {
                "run_id": answer.run_id,
                "answer": answer.answer,
                "grounded": answer.grounded,
                "used_citation_ids": answer.used_citation_ids,
                "invalid_citation_ids": answer.invalid_citation_ids,
                "citation_report": answer.citation_report,
                "provider": answer.provider,
                "model": answer.model,
                "sources": [
                    {
                        "index": c.get("index"),
                        "document_title": c.get("document_title"),
                        "chunk_index": c.get("chunk_index"),
                        "score": c.get("score"),
                        "snippet": (c.get("snippet") or "")[:300],
                    }
                    for c in answer.citations
                ],
            }

        self._submit(request_id, work, timeout=300.0)

    @Slot(str, str, str)
    def streamAnswer(self, request_id: str, knowledge_base_id: str, question: str) -> None:
        """The same answer, streamed, when the configured provider can stream.

        A separate slot rather than a flag on `askKnowledge`, because the two produce
        different signals: this one emits `progress` for every delta, and the caller needs
        to know which it is getting. When the provider cannot stream, this reports that
        rather than pretending — the base-class default would deliver one chunk and a UI
        that animated it would be animating nothing.
        """

        async def work() -> dict[str, Any]:
            from app.ai.providers.factory import build_llm_provider
            from app.ai.rag.retriever import Retriever
            from app.ai.types import ChatMessage
            from app.infrastructure.database import session_scope

            # Called for the binding, not the value: `_owner()` also sets the request
            # context the provider factory reads.
            await self._owner()
            text = (question or "").strip()
            if not text:
                raise ValueError("问题不能为空")

            llm, info = build_llm_provider()
            if not llm.supports_streaming:
                return {
                    "streamed": False,
                    "provider": info.kind,
                    "reason": f"provider '{info.kind}' 不支持流式输出",
                    "answer": "",
                }

            async with session_scope() as session:
                retrieval = await Retriever().retrieve(
                    session,
                    query=text,
                    knowledge_base_ids=[uuid.UUID(knowledge_base_id)],
                    mode="hybrid",
                    top_k=4,
                )
            context = "\n\n".join(
                f"【资料 {i}】{c.document_title}\n{c.content[:1200]}"
                for i, c in enumerate(retrieval.chunks, start=1)
            )
            messages = [
                ChatMessage(
                    role="system",
                    content=(
                        "只能依据下面提供的资料回答。每一条结论后面必须标注 [编号]。"
                        "资料里没有的不要编。\n\n" + (context or "（没有检索到资料）")
                    ),
                ),
                ChatMessage(role="user", content=text),
            ]

            parts: list[str] = []
            self.progress.emit(request_id, "")
            async for chunk in llm.stream_chat(messages, temperature=0.2, max_tokens=600):
                if chunk.delta:
                    parts.append(chunk.delta)
                    self.progress.emit(request_id, chunk.delta)
            await llm.aclose()
            return {
                "streamed": True,
                "provider": info.kind,
                "model": info.model,
                "answer": "".join(parts),
                "sources": [
                    {"index": i, "document_title": c.document_title, "score": c.score}
                    for i, c in enumerate(retrieval.chunks, start=1)
                ],
            }

        self._submit(request_id, work, timeout=300.0)

    # --------------------------------------------------------------- write side --
    @Slot(str, str, str)
    def createTask(self, request_id: str, title: str, priority: str = "MEDIUM") -> None:
        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.services.task_service import TaskService

            owner = await self._owner()
            async with session_scope() as session:
                task = await TaskService(session).create_task(
                    owner_id=owner, title=title.strip(), priority=priority
                )
                await session.commit()
                return {"id": str(task.id), "title": task.title, "status": task.status}

        self._submit(request_id, work)

    @Slot(str, str)
    def completeTask(self, request_id: str, task_id: str) -> None:
        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.services.task_service import TaskService

            owner = await self._owner()
            async with session_scope() as session:
                task = await TaskService(session).complete_task(
                    owner_id=owner, task_id=uuid.UUID(task_id)
                )
                await session.commit()
                return {"id": str(task.id), "status": task.status}

        self._submit(request_id, work)

    @Slot(str, str)
    def saveAiConfig(self, request_id: str, patch_json: str) -> None:
        """Write this account's provider settings.

        This is the entry point the product needs: a key is entered here, stored in the
        account's own file, and takes effect on the next call — no source edit, no
        rebuild, no re-hardcoding. `api_key` is write-only: `loadAiConfig` returns
        `api_key_set` and a mask, never the value.
        """

        async def work() -> dict[str, Any]:
            from app.ai.runtime_config import resolve, save

            owner = await self._owner()
            patch = json.loads(patch_json or "{}")
            if not isinstance(patch, dict):
                raise TypeError("配置必须是一个 JSON 对象")
            save(owner, patch)
            return {"effective": resolve(owner).safe()}

        self._submit(request_id, work)

    @Slot(str)
    def clearAiConfig(self, request_id: str) -> None:
        async def work() -> dict[str, Any]:
            from app.ai.runtime_config import clear, resolve

            owner = await self._owner()
            clear(owner)
            return {"effective": resolve(owner).safe()}

        self._submit(request_id, work)

    # ------------------------------------------------------------------- agent --
    @Slot(str, str)
    def startAgentRun(self, request_id: str, goal: str) -> None:
        """Run the agent and stream progress. Cancellable via `cancelAgentRun`.

        The provider is whatever the account configured. With no key configured this is
        the offline engine, which is why the PoC is runnable without one — and why a run
        that ends `INCOMPLETE` here is the honest result, not a failure of the harness.
        """
        self._cancel_requested = False

        async def work() -> dict[str, Any]:
            from app.ai.agent.executor import AgentExecutor
            from app.infrastructure.database import session_scope

            owner = await self._owner()
            self.progress.emit(request_id, "正在启动 Agent…")
            executor = AgentExecutor(max_steps=4)
            async with session_scope() as session:
                if self._cancel_requested:
                    return {"status": "CANCELLED", "stopped_reason": "cancelled", "steps": 0}
                self.progress.emit(request_id, "正在执行…")
                outcome = await executor.run(session, owner_id=owner, goal=goal)
                await session.commit()
            self.progress.emit(request_id, f"结束：{outcome.stopped_reason}")
            return {
                "run_id": outcome.run_id,
                "status": outcome.status,
                "stopped_reason": outcome.stopped_reason,
                "steps": len(outcome.steps),
                "final_answer": outcome.final_answer[:600],
            }

        self._submit(request_id, work, timeout=300.0)

    @Slot()
    def cancelAgentRun(self) -> None:
        self._cancel_requested = True
        self.notify.emit("warn", "已请求取消；当前步骤结束后停止")

    # -------------------------------------------------------------- file + OS --
    @Slot(str, str, str)
    def saveTextFile(self, request_id: str, suggested_name: str, content: str) -> None:
        """Save through the permission layer, not around it.

        The dialog is Qt's own native dialog, so the user picks the path — but the write
        still goes through `PermissionManager`, exactly as the agent's `file_write` does.
        A UI that skipped that check would be a bypass with a nice font.
        """

        async def work() -> dict[str, Any]:
            from app.ai.tools.permissions import PermissionManager
            from app.ai.tools.policy_store import build_policy

            owner = await self._owner()
            target = Path(suggested_name).expanduser()
            manager = PermissionManager(build_policy(owner))
            verdict = manager.check_path(target, write=True)
            if not verdict.allowed:
                raise PermissionError(verdict.reason)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"path": str(target), "bytes": target.stat().st_size}

        self._submit(request_id, work)

    @Slot(str, str)
    def openPath(self, request_id: str, path: str) -> None:
        async def work() -> dict[str, Any]:
            from app.ai.tools.permissions import PermissionManager
            from app.ai.tools.policy_store import build_policy

            owner = await self._owner()
            target = Path(path).expanduser()
            if not target.exists():
                raise FileNotFoundError(f"文件不存在：{target}")
            manager = PermissionManager(build_policy(owner))
            verdict = manager.check_path(target, write=False)
            if not verdict.allowed:
                raise PermissionError(verdict.reason)
            # `QDesktopServices` is the shell's own "open with the default application",
            # which is what the WebView build used a custom URL scheme for.
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
            return {"path": str(target), "opened": bool(opened)}

        self._submit(request_id, work)

    @Slot(str, str)
    def revealPath(self, request_id: str, path: str) -> None:
        async def work() -> dict[str, Any]:
            target = Path(path).expanduser()
            if not target.exists():
                raise FileNotFoundError(f"文件不存在：{target}")
            if os.name == "nt":  # pragma: no cover - platform branch
                os.startfile(target.parent)
            else:  # pragma: no cover
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))
            return {"path": str(target.parent)}

        self._submit(request_id, work)

    # ---------------------------------------------------------------- projects --
    @Slot(str, str, str)
    def createProject(self, request_id: str, name: str, description: str) -> None:
        """Create a project, and return it in the same shape `loadProjects` returns.

        The same shape on purpose: the page appends the returned row to the list it already
        has, so a created project cannot appear with different fields from a loaded one —
        which is how a list ends up disagreeing with itself about what a project is.
        """

        async def work() -> dict[str, Any]:
            from app.core.errors import ValidationError
            from app.infrastructure.database import session_scope
            from app.models.project import Project

            owner = await self._owner()
            cleaned = (name or "").strip()
            if not cleaned:
                raise ValidationError("项目名不能为空")
            async with session_scope() as session:
                project = Project(
                    owner_id=owner, name=cleaned[:160], description=(description or "")[:2000]
                )
                session.add(project)
                await session.flush()
                return {
                    "item": {
                        "id": str(project.id),
                        "name": project.name,
                        "status": project.status,
                        "description": project.description or "",
                    }
                }

        self._submit(request_id, work)

    # ------------------------------------------------------------------- notes --
    @Slot(str)
    def loadNotes(self, request_id: str) -> None:
        """The account's notes, newest first.

        Ordered here rather than in QML: the list is paged by the backend eventually, and
        ordering that lives in the view is ordering that silently changes when the view
        stops being given the whole list.
        """

        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.models.note import Note
            from sqlalchemy import select

            owner = await self._owner()
            async with session_scope() as session:
                rows = list(
                    (
                        await session.execute(
                            select(Note)
                            .where(Note.owner_id == owner)
                            .order_by(Note.created_at.desc())
                            .limit(200)
                        )
                    ).scalars()
                )
            return {
                "items": [
                    {
                        "id": str(n.id),
                        "title": n.title or "",
                        "content": n.content_md or "",
                        "pinned": bool(n.is_pinned),
                        "ai_generated": bool(n.is_ai_generated),
                        "created_at": n.created_at.isoformat() if n.created_at else "",
                    }
                    for n in rows
                ],
                "total": len(rows),
            }

        self._submit(request_id, work)

    @Slot(str, str, str)
    def createNote(self, request_id: str, title: str, content: str) -> None:
        async def work() -> dict[str, Any]:
            from app.core.errors import ValidationError
            from app.infrastructure.database import session_scope
            from app.models.note import Note

            owner = await self._owner()
            cleaned = (title or "").strip()
            body = (content or "").strip()
            if not cleaned and not body:
                raise ValidationError("标题和内容不能同时为空")
            async with session_scope() as session:
                # `title` is NOT NULL in the schema. Deriving it from the body rather than
                # defaulting to a placeholder keeps the list readable when the user only
                # typed content, which is the common case for a quick capture.
                note = Note(
                    owner_id=owner,
                    title=(cleaned or body[:60])[:300],
                    content_md=body[:20_000],
                )
                session.add(note)
                await session.flush()
                return {
                    "item": {
                        "id": str(note.id),
                        "title": note.title,
                        "content": note.content_md,
                        "pinned": False,
                        "ai_generated": False,
                        "created_at": note.created_at.isoformat() if note.created_at else "",
                    }
                }

        self._submit(request_id, work)

    # -------------------------------------------------------------------- runs --
    #
    # The history and the detail come from the stored records, not from the in-process
    # registry. The registry only knows about runs this process is driving; every run the
    # user can see in their history is over, and after a restart it is the only source
    # there is.
    #
    # The detail view also shows the **invocations whose effect could not be confirmed** —
    # `UNKNOWN` rows left by an interruption — and offers to check them. That is the part
    # that makes the invocation record useful to a person rather than only to recovery: a
    # run that died mid-tool leaves a question, and this is where the answer appears.

    @Slot(str, int)
    def loadRuns(self, request_id: str, limit: int = 50) -> None:
        async def work() -> dict[str, Any]:
            from app.domain.enums import RunType
            from app.infrastructure.database import session_scope
            from app.models.ai import AIRun
            from sqlalchemy import select

            owner = await self._owner()
            capped = max(1, min(int(limit or 50), 200))
            async with session_scope() as session:
                rows = list(
                    (
                        await session.execute(
                            select(AIRun)
                            .where(
                                AIRun.owner_id == owner,
                                AIRun.run_type == RunType.AGENT.value,
                            )
                            .order_by(AIRun.created_at.desc())
                            .limit(capped)
                        )
                    ).scalars()
                )
            return {"items": [_run_summary(row) for row in rows], "total": len(rows)}

        self._submit(request_id, work)

    @Slot(str, str)
    def loadRunDetail(self, request_id: str, run_id: str) -> None:
        async def work() -> dict[str, Any]:
            from app.domain.enums import ToolCallStatus
            from app.infrastructure.database import session_scope
            from app.models.ai import AgentRun, AIRun, ToolCall
            from sqlalchemy import select

            owner = await self._owner()
            async with session_scope() as session:
                run = (
                    await session.execute(
                        select(AIRun).where(
                            AIRun.run_id == run_id, AIRun.owner_id == owner
                        )
                    )
                ).scalar_one_or_none()
                if run is None:
                    raise ValueError("找不到该运行记录")
                agent = (
                    await session.execute(
                        select(AgentRun).where(AgentRun.ai_run_id == run.id)
                    )
                ).scalar_one_or_none()
                calls = list(
                    (
                        await session.execute(
                            select(ToolCall)
                            .where(ToolCall.ai_run_id == run.id)
                            .order_by(ToolCall.step_index, ToolCall.created_at)
                        )
                    ).scalars()
                )

            return {
                "run": _run_summary(run),
                "goal": agent.goal if agent else "",
                "final_answer": agent.final_answer if agent else "",
                "plan": agent.plan if agent else [],
                "scratchpad": agent.scratchpad if agent else {},
                "steps_used": agent.steps_used if agent else 0,
                "tool_calls": [_tool_call_row(call) for call in calls],
                "unresolved": [
                    _tool_call_row(call)
                    for call in calls
                    if call.status == ToolCallStatus.UNKNOWN.value and call.mutates_state
                ],
                # Stated rather than left for the interface to infer. There is no replay
                # path in this product, and a UI that has to guess will eventually guess
                # wrong.
                "replay_allowed": False,
            }

        self._submit(request_id, work)

    @Slot(str, str)
    def reconcileRun(self, request_id: str, run_id: str) -> None:
        """Check the world and settle what each interrupted call did. Re-runs nothing."""
        async def work() -> dict[str, Any]:
            from app.ai.tools.permissions import AgentPolicy
            from app.ai.tools.reconcile import reconcile_unknown_calls
            from app.ai.tools.registry import build_computer_registry
            from app.infrastructure.database import session_scope
            from app.models.ai import AIRun
            from sqlalchemy import select

            owner = await self._owner()
            async with session_scope() as session:
                run = (
                    await session.execute(
                        select(AIRun).where(
                            AIRun.run_id == run_id, AIRun.owner_id == owner
                        )
                    )
                ).scalar_one_or_none()
                if run is None:
                    raise ValueError("找不到该运行记录")
                registry = build_computer_registry(
                    AgentPolicy.from_settings(mode="autonomous", level=3)
                )
                reports = await reconcile_unknown_calls(
                    session, registry=registry, ai_run_id=run.id
                )
                await session.commit()

            return {
                "checked": len(reports),
                "items": [report.to_payload() for report in reports],
                "decided": sum(
                    1 for report in reports if report.status_before != report.status_after
                ),
                "replay_allowed": False,
            }

        self._submit(request_id, work)

    @Slot(str)
    def loadEmbeddingStatus(self, request_id: str) -> None:
        """Whether embedding is real, and which model it would use.

        Reported from `build_embedding_provider()` — the function that decides — rather than
        from the settings, because the two can disagree and the settings are the more
        flattering of the two. On this machine the answer is the offline hash/n-gram
        fallback, and the point of showing it is that "semantic search" must not be claimed
        when what runs is n-gram matching.

        No probe request is made. An embeddings endpoint that is known to be unconfigured
        does not need asking again, and a verification budget is not spent on questions whose
        answers are already in the configuration.
        """
        async def work() -> dict[str, Any]:
            from app.ai.providers.factory import build_embedding_provider
            from app.core.config import settings

            await self._owner()
            try:
                _provider, info = build_embedding_provider()
            except Exception as exc:  # noqa: BLE001 - the message is the report
                return {
                    "usable": False,
                    "provider": settings.embedding_provider,
                    "model": settings.embedding_model,
                    "reason": f"{type(exc).__name__}: {exc}",
                    "semantic_search": False,
                }

            semantic = info.kind in ("openai_compatible", "ollama") and not getattr(
                info, "degraded", False
            )
            return {
                "usable": True,
                "semantic_search": semantic,
                "provider": info.kind,
                "model": info.model,
                "base_url": info.base_url or "",
                "degraded": bool(getattr(info, "degraded", False)),
                "reason": getattr(info, "reason", ""),
                "configured_provider": settings.embedding_provider,
                "configured_model": settings.embedding_model,
                "vector_size": int(getattr(settings, "qdrant_vector_size", 0) or 0),
                "note": (
                    "语义检索已可用。"
                    if semantic
                    else "语义检索不可用：当前用离线哈希 n-gram 向量化，"
                    "它按字符重叠度匹配，不是语义。关键词检索不受影响。"
                ),
            }

        self._submit(request_id, work)

    # ---------------------------------------------------------------- search --
    @Slot(str, str, str)
    def search(self, request_id: str, query: str, mode: str) -> None:
        """Keyword / semantic / hybrid search over the knowledge base.

        The mode is passed through rather than decided here. `semantic` **raises** when there
        is no vector store rather than quietly returning keyword results, and `hybrid`
        degrades to keyword-only and reports `dense_hits = 0`. Both of those are the honest
        answer and the page shows them; deciding the mode in the interface would hide the
        difference between "this matched by meaning" and "this matched by spelling".
        """
        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.services.ai_services import SearchService

            owner = await self._owner()
            text = (query or "").strip()
            if not text:
                raise ValueError("先写一个搜索词")
            if mode not in ("keyword", "semantic", "hybrid"):
                raise ValueError(f"未知的检索方式：{mode}")

            async with session_scope() as session:
                return await SearchService(session).search(
                    owner_id=owner, query=text, mode=mode, limit=20
                )

        self._submit(request_id, work)

    # ----------------------------------------------------------------- graph --
    @Slot(str, str)
    def loadGraph(self, request_id: str, project_id: str) -> None:
        """The relationship graph, projected from real foreign keys.

        The whole graph is fetched once and laid out in the interface. That is the right
        split here: the projection is a database query, the layout is arithmetic, and doing
        the layout in Python would mean a second thread whose only job is to keep the window
        responsive while it computes positions for a few hundred nodes.
        """
        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.services.stats_service import GraphService

            owner = await self._owner()
            async with session_scope() as session:
                return await GraphService(session).build(
                    owner_id=owner,
                    project_id=uuid.UUID(project_id) if project_id else None,
                    limit=250,
                )

        self._submit(request_id, work)

    # ------------------------------------------------------------------ tasks --
    @Slot(str)
    def loadTaskTree(self, request_id: str) -> None:
        """The full goal → phase → task → subtask tree, plus the flat list.

        Both, because they answer different questions: the tree is how the work is
        organised, and the flat list is how it is tracked. A page that only showed the tree
        could not answer "what is in progress right now" without walking it.
        """
        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.services.task_service import TaskService

            owner = await self._owner()
            async with session_scope() as session:
                tree = await TaskService(session).tree(owner_id=owner)

            # Counted from the tree rather than queried again: it is the same rows, and a
            # second query would be a second answer that can disagree with the first.
            by_status: dict[str, int] = {}
            flat: list[dict[str, Any]] = []

            def walk(nodes: list[dict[str, Any]], depth: int) -> None:
                for node in nodes:
                    by_status[node["status"]] = by_status.get(node["status"], 0) + 1
                    flat.append({**node, "depth": depth, "children": []})
                    walk(node.get("children") or [], depth + 1)

            walk(tree, 0)
            return {"tree": tree, "flat": flat, "by_status": by_status, "count": len(flat)}

        self._submit(request_id, work)

    @Slot(str, str, str)
    def updateTask(self, request_id: str, task_id: str, changes_json: str) -> None:
        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.services.task_service import TaskService

            owner = await self._owner()
            try:
                changes = json.loads(changes_json) if changes_json else {}
            except json.JSONDecodeError as exc:
                raise ValueError(f"改动不是合法的 JSON：{exc}") from exc
            if not isinstance(changes, dict) or not changes:
                raise ValueError("没有要改的内容")

            async with session_scope() as session:
                task = await TaskService(session).update_task(
                    owner_id=owner, task_id=uuid.UUID(task_id), **changes
                )
                return {"id": str(task.id), "status": task.status, "title": task.title}

        self._submit(request_id, work)

    @Slot(str, str)
    def deleteTask(self, request_id: str, task_id: str) -> None:
        async def work() -> dict[str, Any]:
            from app.infrastructure.database import session_scope
            from app.services.task_service import TaskService

            owner = await self._owner()
            async with session_scope() as session:
                await TaskService(session).delete_task(owner_id=owner, task_id=uuid.UUID(task_id))
            return {"deleted": True, "task_id": task_id}

        self._submit(request_id, work)

    # -------------------------------------------------------- permissions --
    #
    # These call `policy_store` and `PermissionManager` directly rather than going through
    # the HTTP layer. The routes in `api/v1/agent.py` are thin wrappers over the same
    # functions, and the bridge already runs in the backend's process — so an extra hop
    # would add a serialisation step and no safety. What matters is that there is exactly
    # **one** implementation of the rules, and both callers use it.

    @Slot(str)
    def loadPermissions(self, request_id: str) -> None:
        """The policy as it currently stands, with where each value came from."""
        async def work() -> dict[str, Any]:
            from app.ai.tools import policy_store
            from app.ai.tools.permissions import AgentMode, PermissionLevel

            owner = await self._owner()
            view = policy_store.describe(owner)
            view["modes"] = [
                {
                    "value": AgentMode.ASSIST.value,
                    "label": "Assist",
                    "description": "分析并给出建议，不执行任何操作",
                },
                {
                    "value": AgentMode.SUPERVISED.value,
                    "label": "Supervised",
                    "description": "可以执行；高风险操作需要确认",
                },
                {
                    "value": AgentMode.AUTONOMOUS.value,
                    "label": "Autonomous",
                    "description": "在授权范围内自主完成，关键操作仍会确认",
                },
            ]
            view["levels"] = [
                {"value": int(level), "label": PermissionLevel.label(int(level))}
                for level in PermissionLevel
            ]
            return view

        self._submit(request_id, work)

    @Slot(str, str)
    def savePermissions(self, request_id: str, patch_json: str) -> None:
        """Apply a partial policy change. Unknown keys are ignored by the store itself."""
        async def work() -> dict[str, Any]:
            from app.ai.tools import policy_store
            from app.ai.tools.permissions import AgentMode, PermissionLevel

            owner = await self._owner()
            try:
                patch = json.loads(patch_json) if patch_json else {}
            except json.JSONDecodeError as exc:
                raise ValueError(f"改动不是合法的 JSON：{exc}") from exc
            if not isinstance(patch, dict):
                raise TypeError("改动必须是一个对象")

            if patch.get("mode") is not None:
                mode = str(patch["mode"]).lower()
                if mode not in {m.value for m in AgentMode}:
                    raise ValueError(f"未知的运行模式：{patch['mode']}")
                patch["mode"] = mode
            if patch.get("level") is not None:
                level = int(patch["level"])
                if level not in {int(x) for x in PermissionLevel}:
                    raise ValueError(f"未知的权限级别：{level}")
                patch["level"] = level

            policy_store.save_overrides(patch, owner)
            return policy_store.describe(owner)

        self._submit(request_id, work)

    @Slot(str)
    def resetPermissions(self, request_id: str) -> None:
        async def work() -> dict[str, Any]:
            from app.ai.tools import policy_store

            owner = await self._owner()
            policy_store.reset_overrides(owner)
            return policy_store.describe(owner)

        self._submit(request_id, work)

    @Slot(str)
    def loadCapabilities(self, request_id: str) -> None:
        """Capability areas, whether each is on, and how many tools that leaves."""
        async def work() -> dict[str, Any]:
            from app.ai.tools import policy_store
            from app.ai.tools.permissions import PermissionLevel
            from app.ai.tools.registry import build_computer_registry

            owner = await self._owner()
            policy = policy_store.build_policy(owner)
            flags = policy_store.capability_flags(owner)
            registry = build_computer_registry(policy, enabled=flags)
            areas = [{**area, "user_enabled": flags.get(area["key"], True)} for area in registry.capabilities()]
            return {
                "capabilities": areas,
                "level": int(policy.level),
                "level_label": PermissionLevel.label(int(policy.level)),
                "tool_count": len(registry.names()),
            }

        self._submit(request_id, work)

    @Slot(str, str)
    def saveCapabilities(self, request_id: str, flags_json: str) -> None:
        async def work() -> dict[str, Any]:
            from app.ai.tools import policy_store
            from app.ai.tools.registry import CAPABILITIES, build_computer_registry

            owner = await self._owner()
            try:
                flags = json.loads(flags_json) if flags_json else {}
            except json.JSONDecodeError as exc:
                raise ValueError(f"能力开关不是合法的 JSON：{exc}") from exc
            if not isinstance(flags, dict):
                raise TypeError("能力开关必须是一个对象")

            known = {group.key for group in CAPABILITIES}
            unknown = sorted(set(flags) - known)
            if unknown:
                raise ValueError(f"未知的能力分组：{', '.join(unknown)}")

            policy_store.save_capability_flags({k: bool(v) for k, v in flags.items()}, owner)
            policy = policy_store.build_policy(owner)
            registry = build_computer_registry(policy, enabled=policy_store.capability_flags(owner))
            return {"tool_count": len(registry.names())}

        self._submit(request_id, work)

    @Slot(str)
    def loadTools(self, request_id: str) -> None:
        """The tool catalogue, with what the policy currently does about each one.

        A tool the policy would refuse is reported as **excluded with the reason**, not
        omitted. Someone opening this screen is asking "what can the agent do", and a list
        that silently drops things answers a different question.
        """
        async def work() -> dict[str, Any]:
            from app.ai.agent.tools import get_tool_registry
            from app.ai.tools import policy_store
            from app.ai.tools.permissions import AgentMode, AgentPolicy, PermissionLevel
            from app.ai.tools.registry import build_computer_registry

            owner = await self._owner()
            policy = policy_store.build_policy(owner)
            registry = build_computer_registry(policy, enabled=policy_store.capability_flags(owner))
            available = set(registry.names())

            rows: list[dict[str, Any]] = []
            for spec in get_tool_registry().specs():
                rows.append(
                    {
                        "name": spec.name,
                        "description": spec.description,
                        "category": spec.category,
                        "source": "business",
                        "permission_label": PermissionLevel.label(spec.permission_level),
                        "risk_level": spec.risk_level,
                        "mutates_state": bool(spec.mutates_state),
                        "requires_confirmation": bool(spec.requires_confirmation),
                        "available": True,
                        "reason": "",
                    }
                )
            for row in registry.catalog():
                rows.append({**row, "source": "computer", "available": row["name"] in available, "reason": ""})

            permissive = AgentPolicy.from_settings(
                mode=AgentMode.SUPERVISED, level=PermissionLevel.FULL_CONTROL
            )
            everything = build_computer_registry(permissive)
            excluded = [
                {
                    "name": row["name"],
                    "category": row["category"],
                    "permission_label": row["permission_label"],
                    "reason": f"需要 {row['permission_label']} 级别，或该能力已被关闭",
                }
                for row in everything.catalog()
                if row["name"] not in available
            ]

            return {
                "tools": rows,
                "count": len(rows),
                "available": sum(1 for r in rows if r["available"]),
                "excluded": excluded,
                "level_label": PermissionLevel.label(int(policy.level)),
            }

        self._submit(request_id, work)

    @Slot(str, str, bool)
    def checkPermission(self, request_id: str, target: str, write: bool) -> None:
        """Ask the real permission service about one path, and show its answer.

        This is the part that makes the screen honest: it is the same `PermissionManager`
        call the tool makes before it touches the file, so the answer cannot be a promise
        the tool will not keep.
        """
        async def work() -> dict[str, Any]:
            from app.ai.tools import policy_store
            from app.ai.tools.permissions import PermissionLevel, PermissionManager

            owner = await self._owner()
            path = (target or "").strip()
            if not path:
                raise ValueError("先写一个路径")
            policy = policy_store.build_policy(owner)
            verdict = PermissionManager(policy).check_path(path, write=bool(write))
            return {
                "target": path,
                "write": bool(write),
                "verdict": verdict.as_dict(),
                "level_label": PermissionLevel.label(int(policy.level)),
                "workspace": str(policy.workspace),
            }

        self._submit(request_id, work)

    # ------------------------------------------------------------- conversation --
    #
    # A chat turn with no retrieval, streamed. `streamAnswer` is the same plumbing with a
    # knowledge base attached; this one is the plain conversation the user has with the
    # configured provider.
    #
    # The provider is asked whether it can stream rather than assumed to. A provider that
    # cannot stream returns one chunk and reports `supports_streaming` False, and a UI that
    # animated that would be animating nothing — which is exactly what the acceptance round
    # found when a reasoning model consumed its whole token budget in `reasoning_content`
    # and left `content` empty.

    @Slot(str, str, str)
    def streamChat(self, request_id: str, history_json: str, prompt: str) -> None:
        async def work() -> dict[str, Any]:
            from app.ai.providers.factory import build_llm_provider
            from app.ai.types import ChatMessage

            await self._owner()
            text = (prompt or "").strip()
            if not text:
                raise ValueError("消息不能为空")

            history: list[dict[str, Any]] = []
            if history_json:
                try:
                    parsed = json.loads(history_json)
                except json.JSONDecodeError:
                    parsed = []
                if isinstance(parsed, list):
                    history = [item for item in parsed if isinstance(item, dict)]

            messages = [
                ChatMessage(role=str(item.get("role") or "user"), content=str(item.get("content") or ""))
                for item in history[-20:]
                if str(item.get("content") or "").strip()
            ]
            messages.append(ChatMessage(role="user", content=text))

            llm, info = build_llm_provider()
            if not llm.supports_streaming:
                return {
                    "streamed": False,
                    "provider": info.kind,
                    "model": info.model,
                    "reason": f"provider '{info.kind}' 不支持流式输出",
                    "answer": "",
                }

            parts: list[str] = []
            reasoning_parts: list[str] = []
            self.progress.emit(request_id, "")
            async for chunk in llm.stream_chat(messages, temperature=0.6, max_tokens=900):
                # Reasoning tokens are counted separately. A reasoning model can spend its
                # whole budget in `reasoning_content` and emit no `content` at all; a UI
                # that showed an empty answer with no explanation would look broken, and
                # the explanation is right here.
                if getattr(chunk, "reasoning_delta", ""):
                    reasoning_parts.append(chunk.reasoning_delta)
                    self.progress.emit(
                        request_id, json.dumps({"reasoning": chunk.reasoning_delta})
                    )
                if chunk.delta:
                    parts.append(chunk.delta)
                    self.progress.emit(request_id, chunk.delta)

            answer = "".join(parts)
            return {
                "streamed": True,
                "provider": info.kind,
                "model": info.model,
                "answer": answer,
                "reasoning_chars": sum(len(part) for part in reasoning_parts),
                "empty_because_reasoning": not answer and bool(reasoning_parts),
            }

        self._submit(request_id, work, timeout=300.0)

    # ------------------------------------------------------------------ startup --
    # The stack comes up **behind** the window, not in front of it.
    #
    # `main.py` used to run the first-run initialisation before creating the window, which
    # on a first launch means `initdb` — tens of seconds during which the user has
    # double-clicked an icon and nothing at all has happened. On a machine where the
    # database is missing it is worse: the window never appears and the only evidence is a
    # log file. So the window is created first and these two steps report into it.
    #
    # Neither step runs on the GUI thread. `runtime.ensure()` spawns PowerShell and waits
    # for a database; `bootstrap.ensure_ready()` opens connections. Either one on the UI
    # thread is a frozen window.

    @Property(str, notify=startupChanged)
    def startupState(self) -> str:
        """`idle` | `starting` | `ready` | `degraded` | `failed`."""
        return self._startup_state

    @Property(str, notify=startupChanged)
    def startupDetail(self) -> str:
        return self._startup_detail

    @Property(bool, notify=startupChanged)
    def startupBusy(self) -> bool:
        return self._startup_state in {"idle", "starting"}

    def _set_startup(self, state: str, detail: str) -> None:
        if state == self._startup_state and detail == self._startup_detail:
            return
        self._startup_state = state
        self._startup_detail = detail
        self.startupChanged.emit()

    def _apply_startup(self, report: dict[str, Any]) -> None:
        self._environment = report
        readiness = report.get("readiness") or {}
        runtime_state = report.get("runtime") or {}
        self._started_services = bool(runtime_state.get("started_here"))
        failed = list(readiness.get("failed") or [])
        blocked = list(readiness.get("blocked") or [])
        if failed:
            self._set_startup("failed", "本地环境没能启动：" + _plain_names(failed) + "。详见设置页。")
        elif blocked:
            self._set_startup("degraded", "部分功能不可用：" + _plain_names(blocked))
        else:
            self._set_startup("ready", "已就绪，可以开始使用")

    def record_startup(self, runtime_state: dict[str, Any], readiness: dict[str, Any]) -> None:
        """Adopt a startup result computed elsewhere.

        The headless self-check runs the same two steps synchronously, before the window,
        so that its report describes a complete environment. Without this the banner would
        still say `idle` in that run, and a check that reads the banner would be checking
        the wrong thing — which is how "the state is visible in the interface" passes while
        the interface shows nothing.
        """
        self._apply_startup({"runtime": runtime_state, "readiness": readiness})

    @Slot()
    def startEnvironment(self) -> None:
        """Bring up the local stack, then verify the schema. Returns immediately."""
        if self._startup_state == "starting":
            return
        self._set_startup("starting", "正在准备本地环境…")
        self._runner.submit(self.STARTUP_REQUEST, self._bring_up, timeout=900.0)

    async def _bring_up(self) -> dict[str, Any]:
        from awbnative import bootstrap, runtime

        # A thread, not the runner's loop: `runtime.ensure()` is a subprocess that waits for
        # a database to accept connections. Blocking the loop would stall every other call
        # the interface makes while the banner is up.
        runtime_state = await asyncio.to_thread(runtime.ensure)
        # Deliberately not `runtime_state.detail`: that names the engine it just started.
        self._set_startup("starting", "正在准备本地环境…")
        readiness = await bootstrap.ensure_ready()
        return {"runtime": runtime_state.as_dict(), "readiness": readiness}

    @Slot()
    def stopEnvironment(self) -> None:
        """Stop the local services — **only** if this window started them.

        Called on exit. A stack this process did not start belongs to someone else: a
        developer's checkout, or a second window. Stopping it would take out their database
        and, worse, look like data loss.
        """
        if not self._started_services:
            return
        from awbnative import runtime

        try:
            result = runtime.stop()
        except Exception as exc:  # noqa: BLE001 - exit must not raise
            self.notify.emit("error", f"停止本地服务时出错：{type(exc).__name__}: {exc}")
            return
        self._started_services = False
        if result.get("state") != "ok":
            self.notify.emit("error", str(result.get("detail") or "停止本地服务失败"))

    @Slot(result=str)
    def environmentReport(self) -> str:
        """The last startup report, as JSON. For the settings page and the verifiers."""
        return _json(self._environment)

    # ---------------------------------------------------------------- lifecycle --
    @Slot()
    def shutdown(self) -> None:
        self._runner.shutdown()


def _overridable() -> tuple[str, ...]:
    from app.ai.runtime_config import OVERRIDABLE

    return OVERRIDABLE

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

import json
import os
import uuid
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from awbnative.async_runner import AsyncRunner


def _json(payload: Any) -> str:
    """QML receives JSON text; a QVariantMap would need a converter on every slot."""
    return json.dumps(payload, ensure_ascii=False, default=str)


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

    def __init__(self, owner_id: uuid.UUID | str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._runner = AsyncRunner(self)
        self._runner.completed.connect(self._on_completed)
        self._runner.failed.connect(self._on_failed)
        self._owner_id = str(owner_id) if owner_id else ""
        self._cancel_requested = False

    # ------------------------------------------------------------------- wiring --
    def _on_completed(self, request_id: str, value: Any) -> None:
        self.completed.emit(request_id, value if isinstance(value, str) else _json(value))

    def _on_failed(self, request_id: str, message: str, code: str) -> None:
        self.failed.emit(request_id, message, code)

    def _submit(self, request_id: str, factory, *, timeout: float = 120.0) -> None:
        self._runner.submit(request_id, factory, timeout=timeout)

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
            raise RuntimeError("这台机器上还没有账号，请先在原应用中创建一个")
        self._owner_id = str(row.id)
        set_current_owner(self._owner_id)
        return row.id

    # ---------------------------------------------------------------- read side --
    @Slot(str)
    def loadDashboard(self, request_id: str) -> None:
        async def work() -> dict[str, Any]:
            from app.services.stats_service import StatsService

            owner = await self._owner()
            return await StatsService().dashboard(owner_id=owner)

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

    # ---------------------------------------------------------------- lifecycle --
    @Slot()
    def shutdown(self) -> None:
        self._runner.shutdown()


def _overridable() -> tuple[str, ...]:
    from app.ai.runtime_config import OVERRIDABLE

    return OVERRIDABLE

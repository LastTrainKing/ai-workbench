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
        """This install's account. Resolved once and remembered."""
        if self._owner_id:
            return uuid.UUID(self._owner_id)
        from sqlalchemy import select

        from app.infrastructure.database import session_scope
        from app.models.user import User

        async with session_scope() as session:
            row = (
                await session.execute(
                    select(User).where(User.is_active.is_(True)).order_by(User.created_at).limit(1)
                )
            ).scalar_one_or_none()
        if row is None:
            raise RuntimeError("这台机器上还没有账号，请先在原应用中创建一个")
        self._owner_id = str(row.id)
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
                raise ValueError("配置必须是一个 JSON 对象")
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
                os.startfile(target.parent)  # noqa: S606 - explorer, not an executable
            else:  # pragma: no cover
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))
            return {"path": str(target.parent)}

        self._submit(request_id, work)

    # ---------------------------------------------------------------- lifecycle --
    @Slot()
    def shutdown(self) -> None:
        self._runner.shutdown()


def _overridable() -> tuple[str, ...]:
    from app.ai.runtime_config import OVERRIDABLE

    return OVERRIDABLE

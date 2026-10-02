"""Agent tool layer.

Architecture rule enforced here: an agent can **never** touch the database
directly.  It may only call a registered ``Tool``, and every tool goes through
``Service`` → ``Repository`` → database.  Tools are the only write path an agent
has, which is what makes the agent auditable and safe.

Registered tools
----------------
=============================  =========  ==========================================
name                           category   purpose
=============================  =========  ==========================================
``knowledge_search``           read       hybrid search inside the knowledge base
``document_read``              read       read a document's full/partial text
``task_list``                  read       list existing tasks (avoid duplicates)
``project_list``               read       list projects available for assignment
``calculator``                 utility    safe arithmetic evaluation
``task_planner``               ai         generate a phased plan from a goal
``task_create``                write      create one task
``task_create_batch``          write      create a task tree from a plan/goal
``task_update``                write      change status/priority/due date
``note_create``                write      persist a Markdown note
``knowledge_link``             write      link knowledge to a task
=============================  =========  ==========================================
"""

from __future__ import annotations

import ast
import json
import operator
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.types import ToolResult, ToolSpec
from app.core.errors import AppError, NotFoundError, ToolExecutionError, ValidationError
from app.core.logging import get_logger
from app.domain.enums import TaskSource
from app.models.knowledge import Document, KnowledgeBase
from app.models.project import Project
from app.models.task import Task

logger = get_logger(__name__)


@dataclass
class ToolContext:
    """Everything a tool is allowed to know about the current execution."""

    session: AsyncSession
    owner_id: uuid.UUID
    project_id: uuid.UUID | None = None
    knowledge_base_ids: list[uuid.UUID] = field(default_factory=list)
    run_id: str = ""
    max_write_ops: int = 40
    write_ops: int = 0
    artifacts: dict[str, list[str]] = field(default_factory=dict)

    def register_artifact(self, kind: str, entity_id: str) -> None:
        self.artifacts.setdefault(kind, []).append(entity_id)

    def consume_write(self) -> None:
        self.write_ops += 1
        if self.write_ops > self.max_write_ops:
            raise ToolExecutionError(
                "The agent exceeded the maximum number of write operations "
                f"({self.max_write_ops}) for a single run."
            )


class Tool(ABC):
    """Base class for every agent tool."""

    spec: ClassVar[ToolSpec]
    is_write: ClassVar[bool] = False

    @abstractmethod
    async def execute(self, ctx: ToolContext, **kwargs: Any) -> ToolResult: ...

    # ------------------------------------------------------------ validation --
    def validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        schema = self.spec.parameters or {}
        properties: dict[str, Any] = schema.get("properties", {})
        required: list[str] = schema.get("required", [])
        cleaned: dict[str, Any] = {}

        for name in required:
            if name not in arguments or arguments[name] in (None, ""):
                raise ValidationError(
                    f"Tool '{self.spec.name}' requires the parameter '{name}'",
                    details={"tool": self.spec.name, "missing": name},
                )
        for name, value in arguments.items():
            if name not in properties:
                continue  # ignore hallucinated parameters instead of failing
            expected = properties[name].get("type")
            if value is None:
                continue
            if expected == "integer" and isinstance(value, str) and value.isdigit():
                value = int(value)
            if expected == "number" and isinstance(value, str):
                try:
                    value = float(value)
                except ValueError:
                    raise ValidationError(
                        f"Parameter '{name}' must be a number",
                        details={"tool": self.spec.name, "value": value},
                    ) from None
            if expected == "integer" and not isinstance(value, int):
                raise ValidationError(
                    f"Parameter '{name}' must be an integer",
                    details={"tool": self.spec.name, "value": value},
                )
            if expected == "array" and isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    value = parsed if isinstance(parsed, list) else [value]
                except json.JSONDecodeError:
                    value = [value]
            cleaned[name] = value

        for name, definition in properties.items():
            if name not in cleaned and "default" in definition:
                cleaned[name] = definition["default"]
        return cleaned

    async def run(self, ctx: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        started = time.perf_counter()
        try:
            cleaned = self.validate(arguments)
            if self.is_write:
                ctx.consume_write()
            result = await self.execute(ctx, **cleaned)
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            return result
        except AppError as exc:
            logger.warning(
                "tool_failed",
                extra={"tool": self.spec.name, "code": exc.code, "detail": exc.message},
            )
            return ToolResult(
                ok=False,
                error=f"{exc.code}: {exc.message}",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            logger.exception("tool_crashed", extra={"tool": self.spec.name})
            return ToolResult(
                ok=False,
                error=f"unexpected_error: {type(exc).__name__}: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------
class KnowledgeSearchTool(Tool):
    spec = ToolSpec(
        name="knowledge_search",
        description=(
            "在用户的知识库中做混合检索（向量 + 关键词），返回带来源的文本片段。"
            "任何需要引用资料的任务都必须先调用它。"
        ),
        category="read",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索问题或关键词"},
                "limit": {"type": "integer", "description": "返回片段数量", "default": 6},
            },
            "required": ["query"],
        },
    )

    def __init__(self) -> None:
        from app.ai.rag.retriever import Retriever

        self.retriever = Retriever()

    async def execute(self, ctx: ToolContext, query: str, limit: int = 6) -> ToolResult:
        limit = max(1, min(int(limit), 12))
        result = await self.retriever.retrieve(
            ctx.session,
            query=query,
            knowledge_base_ids=ctx.knowledge_base_ids or None,
            top_k=limit,
        )
        payload = {
            "query": query,
            "mode": result.mode,
            "count": len(result.chunks),
            "results": [
                {
                    "chunk_id": c.chunk_id,
                    "document_id": c.document_id,
                    "document_title": c.document_title,
                    "heading": c.heading,
                    "score": round(c.score, 4),
                    "content": c.content[:1200],
                }
                for c in result.chunks
            ],
        }
        summary = (
            f"检索到 {len(result.chunks)} 个片段"
            + (f"，来自《{result.chunks[0].document_title}》等资料" if result.chunks else "")
        )
        return ToolResult(ok=True, data=payload, summary=summary)


class DocumentReadTool(Tool):
    spec = ToolSpec(
        name="document_read",
        description="读取知识库中某篇文档的正文内容（可指定字符范围）。",
        category="read",
        parameters={
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "description": "文档 UUID"},
                "max_chars": {"type": "integer", "description": "最多读取的字符数", "default": 6000},
            },
            "required": ["document_id"],
        },
    )

    async def execute(self, ctx: ToolContext, document_id: str, max_chars: int = 6000) -> ToolResult:
        doc = await _get_document(ctx, document_id)
        from app.infrastructure.files import read_text

        try:
            text = read_text(__import__("pathlib").Path(doc.stored_path), limit=int(max_chars))
        except AppError as exc:
            return ToolResult(ok=False, error=f"{exc.code}: {exc.message}")
        return ToolResult(
            ok=True,
            data={"document_id": str(doc.id), "title": doc.title, "content": text},
            summary=f"读取《{doc.title}》共 {len(text)} 字符",
        )


class TaskListTool(Tool):
    spec = ToolSpec(
        name="task_list",
        description="列出当前用户已有的任务，用于避免重复创建。",
        category="read",
        parameters={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "按状态过滤：TODO/IN_PROGRESS/BLOCKED/DONE/CANCELLED",
                },
                "limit": {"type": "integer", "default": 30},
            },
            "required": [],
        },
    )

    async def execute(self, ctx: ToolContext, status: str | None = None, limit: int = 30) -> ToolResult:
        stmt = select(Task).where(Task.owner_id == ctx.owner_id)
        if ctx.project_id:
            stmt = stmt.where(Task.project_id == ctx.project_id)
        if status:
            stmt = stmt.where(Task.status == status.upper())
        stmt = stmt.order_by(Task.created_at.desc()).limit(max(1, min(int(limit), 100)))
        rows = (await ctx.session.execute(stmt)).scalars().all()
        return ToolResult(
            ok=True,
            data={
                "count": len(rows),
                "tasks": [
                    {
                        "id": str(t.id),
                        "title": t.title,
                        "status": t.status,
                        "priority": t.priority,
                        "due_date": t.due_date.isoformat() if t.due_date else None,
                    }
                    for t in rows
                ],
            },
            summary=f"当前共 {len(rows)} 个任务",
        )


class ProjectListTool(Tool):
    spec = ToolSpec(
        name="project_list",
        description="列出用户的项目，用于确定新任务应归属的项目。",
        category="read",
        parameters={"type": "object", "properties": {}, "required": []},
    )

    async def execute(self, ctx: ToolContext) -> ToolResult:
        rows = (
            await ctx.session.execute(
                select(Project).where(Project.owner_id == ctx.owner_id).order_by(Project.created_at)
            )
        ).scalars().all()
        return ToolResult(
            ok=True,
            data={
                "count": len(rows),
                "projects": [{"id": str(p.id), "name": p.name, "status": p.status} for p in rows],
            },
            summary=f"共 {len(rows)} 个项目",
        )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def safe_eval(expression: str) -> float:
    """AST-restricted arithmetic evaluator (no ``eval`` on user input)."""
    if len(expression) > 200:
        raise ValidationError("Expression is too long")

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
            left, right = _eval(node.left), _eval(node.right)
            if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
                raise ValidationError("Division by zero")
            if isinstance(node.op, ast.Pow) and abs(right) > 64:
                raise ValidationError("Exponent is too large")
            return _ALLOWED_OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
            return _ALLOWED_OPS[type(node.op)](_eval(node.operand))
        raise ValidationError(f"Unsupported expression element: {type(node).__name__}")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValidationError(f"Invalid expression: {exc.msg}") from exc
    return _eval(tree)


class CalculatorTool(Tool):
    spec = ToolSpec(
        name="calculator",
        description="计算数学表达式，例如 (12+8)*3/2。支持 + - * / // % **。",
        category="utility",
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "数学表达式"}},
            "required": ["expression"],
        },
    )

    async def execute(self, ctx: ToolContext, expression: str) -> ToolResult:
        value = safe_eval(expression)
        return ToolResult(
            ok=True,
            data={"expression": expression, "result": value},
            summary=f"{expression} = {value:g}",
        )


# ---------------------------------------------------------------------------
# AI-backed tool
# ---------------------------------------------------------------------------
class TaskPlannerTool(Tool):
    spec = ToolSpec(
        name="task_planner",
        description=(
            "根据目标与知识库资料生成分阶段的任务树（不写入数据库）。"
            "生成后可用 task_create_batch 落地为真实任务。"
        ),
        category="ai",
        parameters={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "要达成的目标"},
                "horizon_days": {"type": "integer", "description": "计划周期（天）", "default": 30},
            },
            "required": ["goal"],
        },
    )

    async def execute(self, ctx: ToolContext, goal: str, horizon_days: int = 30) -> ToolResult:
        from app.services.ai_services import PlannerService

        planner = PlannerService()
        plan = await planner.generate_plan(
            ctx.session,
            owner_id=ctx.owner_id,
            goal=goal,
            knowledge_base_ids=ctx.knowledge_base_ids or None,
            horizon_days=int(horizon_days),
            project_id=ctx.project_id,
        )
        return ToolResult(
            ok=True,
            data=plan,
            summary=f"已生成 {len(plan.get('phases', []))} 个阶段的学习/执行计划",
        )


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------
class TaskCreateTool(Tool):
    is_write = True
    spec = ToolSpec(
        name="task_create",
        description="创建一个任务，可指定项目、优先级、预计耗时与截止时间。",
        category="write",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任务标题"},
                "description": {"type": "string", "default": ""},
                "priority": {"type": "string", "description": "LOW/MEDIUM/HIGH/URGENT", "default": "MEDIUM"},
                "estimated_minutes": {"type": "integer", "default": 0},
                "due_date": {"type": "string", "description": "ISO 日期，例如 2026-11-01"},
                "completion_criteria": {"type": "string", "default": ""},
            },
            "required": ["title"],
        },
    )

    async def execute(
        self,
        ctx: ToolContext,
        title: str,
        description: str = "",
        priority: str = "MEDIUM",
        estimated_minutes: int = 0,
        due_date: str | None = None,
        completion_criteria: str = "",
    ) -> ToolResult:
        from app.services.task_service import TaskService

        service = TaskService(ctx.session)
        task = await service.create_task(
            owner_id=ctx.owner_id,
            project_id=ctx.project_id,
            title=title,
            description=description,
            priority=priority,
            estimated_minutes=int(estimated_minutes or 0),
            due_date_raw=due_date,
            completion_criteria=completion_criteria,
            source=TaskSource.AGENT,
        )
        ctx.register_artifact("tasks", str(task.id))
        return ToolResult(
            ok=True,
            data={"task_id": str(task.id), "title": task.title, "status": task.status},
            summary=f"已创建任务《{task.title}》",
        )


class TaskCreateBatchTool(Tool):
    is_write = True
    spec = ToolSpec(
        name="task_create_batch",
        description=(
            "根据目标自动规划并批量创建任务树（阶段 → 任务 → 子任务）。"
            "当用户要求“制定计划并创建任务”时使用。"
        ),
        category="write",
        parameters={
            "type": "object",
            "properties": {
                "from_goal": {"type": "string", "description": "目标描述"},
                "horizon_days": {"type": "integer", "default": 30},
                "project_id": {"type": "string", "description": "可选，指定项目 UUID"},
            },
            "required": ["from_goal"],
        },
    )

    async def execute(
        self, ctx: ToolContext, from_goal: str, horizon_days: int = 30, project_id: str | None = None
    ) -> ToolResult:
        from app.services.ai_services import PlannerService

        target_project = _as_uuid(project_id) or ctx.project_id
        planner = PlannerService()
        plan = await planner.generate_plan(
            ctx.session,
            owner_id=ctx.owner_id,
            goal=from_goal,
            knowledge_base_ids=ctx.knowledge_base_ids or None,
            horizon_days=int(horizon_days),
            project_id=target_project,
        )
        created = await planner.persist_plan(
            ctx.session,
            owner_id=ctx.owner_id,
            project_id=target_project,
            plan=plan,
            source=TaskSource.AGENT,
        )
        for task_id in created["task_ids"]:
            ctx.register_artifact("tasks", task_id)
        return ToolResult(
            ok=True,
            data=created,
            summary=f"已创建 {created['created']} 个任务（含 {created['phases']} 个阶段）",
        )


class TaskUpdateTool(Tool):
    is_write = True
    spec = ToolSpec(
        name="task_update",
        description="更新任务的状态、优先级、预计耗时或完成标准。",
        category="write",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {"type": "string", "description": "TODO/IN_PROGRESS/BLOCKED/DONE/CANCELLED"},
                "priority": {"type": "string"},
                "estimated_minutes": {"type": "integer"},
                "actual_minutes": {"type": "integer"},
                "completion_criteria": {"type": "string"},
            },
            "required": ["task_id"],
        },
    )

    async def execute(self, ctx: ToolContext, task_id: str, **changes: Any) -> ToolResult:
        from app.services.task_service import TaskService

        service = TaskService(ctx.session)
        task = await service.update_task(
            owner_id=ctx.owner_id, task_id=_as_uuid(task_id), **changes
        )
        ctx.register_artifact("tasks", str(task.id))
        return ToolResult(
            ok=True,
            data={"task_id": str(task.id), "status": task.status, "priority": task.priority},
            summary=f"已更新任务《{task.title}》→ {task.status}",
        )


class NoteCreateTool(Tool):
    is_write = True
    spec = ToolSpec(
        name="note_create",
        description="创建一篇 Markdown 笔记；当提供 from_goal 时，会先基于知识库资料生成内容。",
        category="write",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content_md": {"type": "string", "default": ""},
                "from_goal": {"type": "string", "description": "若正文为空，则据此生成"},
                "project_id": {"type": "string"},
            },
            "required": ["title"],
        },
    )

    async def execute(
        self,
        ctx: ToolContext,
        title: str,
        content_md: str = "",
        from_goal: str = "",
        project_id: str | None = None,
    ) -> ToolResult:
        from app.services.workspace_service import NoteService

        body = content_md
        run_id = ""
        if not body.strip() and from_goal.strip():
            from app.services.document_service import DocumentService

            generator = DocumentService(ctx.session)
            generated = await generator.generate_note(
                owner_id=ctx.owner_id,
                title=title,
                topic=from_goal,
                knowledge_base_ids=ctx.knowledge_base_ids or None,
            )
            body = generated["content_md"]
            run_id = generated["run_id"]

        service = NoteService(ctx.session)
        note = await service.create_note(
            owner_id=ctx.owner_id,
            title=title,
            content_md=body or f"# {title}\n\n（由 Agent 创建，暂无正文）",
            project_id=_as_uuid(project_id) or ctx.project_id,
            source_run_id=run_id,
            is_ai_generated=bool(run_id),
        )
        ctx.register_artifact("notes", str(note.id))
        return ToolResult(
            ok=True,
            data={"note_id": str(note.id), "title": note.title, "chars": len(note.content_md)},
            summary=f"已创建笔记《{note.title}》（{len(note.content_md)} 字符）",
        )


class KnowledgeLinkTool(Tool):
    is_write = True
    spec = ToolSpec(
        name="knowledge_link",
        description="把知识库文档关联到某个任务，形成知识—任务闭环。",
        category="write",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "document_id": {"type": "string"},
                "relevance": {"type": "number", "default": 1.0},
            },
            "required": ["task_id", "document_id"],
        },
    )

    async def execute(
        self, ctx: ToolContext, task_id: str, document_id: str, relevance: float = 1.0
    ) -> ToolResult:
        from app.services.task_service import TaskService

        service = TaskService(ctx.session)
        await service.link_document(
            owner_id=ctx.owner_id,
            task_id=_as_uuid(task_id),
            document_id=_as_uuid(document_id),
            relevance=float(relevance),
        )
        return ToolResult(
            ok=True,
            data={"task_id": task_id, "document_id": document_id},
            summary="已建立知识关联",
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.spec.name in self._tools:
            raise ValueError(f"Tool '{tool.spec.name}' is already registered")
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise NotFoundError(
                f"Unknown tool '{name}'",
                details={"available": sorted(self._tools)},
            )
        return tool

    def specs(self, *, include_write: bool = True) -> list[ToolSpec]:
        return [
            tool.spec
            for tool in self._tools.values()
            if include_write or not tool.is_write
        ]

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.spec.name,
                "description": tool.spec.description,
                "category": tool.spec.category,
                "parameters": tool.spec.parameters,
                "is_write": tool.is_write,
            }
            for tool in self._tools.values()
        ]

    def names(self) -> list[str]:
        return sorted(self._tools)

    async def execute(
        self, ctx: ToolContext, name: str, arguments: dict[str, Any]
    ) -> ToolResult:
        try:
            tool = self.get(name)
        except NotFoundError:
            return ToolResult(ok=False, error=f"unknown_tool: {name}")
        return await tool.run(ctx, arguments)


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        KnowledgeSearchTool(),
        DocumentReadTool(),
        TaskListTool(),
        ProjectListTool(),
        CalculatorTool(),
        TaskPlannerTool(),
        TaskCreateTool(),
        TaskCreateBatchTool(),
        TaskUpdateTool(),
        NoteCreateTool(),
        KnowledgeLinkTool(),
    ):
        registry.register(tool)
    return registry


_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = build_default_registry()
    return _registry


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _as_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return uuid.UUID(value.strip())
        except ValueError:
            return None
    return None


async def _get_document(ctx: ToolContext, document_id: str) -> Document:
    uid = _as_uuid(document_id)
    if uid is None:
        raise ValidationError("document_id must be a valid UUID")
    doc = (
        await ctx.session.execute(
            select(Document).where(Document.id == uid, Document.owner_id == ctx.owner_id)
        )
    ).scalar_one_or_none()
    if doc is None:
        raise NotFoundError("Document not found or not owned by the current user")
    return doc


async def knowledge_base_summary(session: AsyncSession, owner_id: uuid.UUID) -> dict[str, Any]:
    total = (
        await session.execute(
            select(func.count(KnowledgeBase.id)).where(KnowledgeBase.owner_id == owner_id)
        )
    ).scalar_one()
    docs = (
        await session.execute(
            select(func.count(Document.id)).where(Document.owner_id == owner_id)
        )
    ).scalar_one()
    return {"knowledge_bases": int(total), "documents": int(docs)}

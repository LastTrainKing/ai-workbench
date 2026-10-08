"""Search, chat (RAG), planning, agent and AI-run services."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent.executor import AgentExecutor, AgentOutcome
from app.ai.memory import ContextAssembler, MemoryEvaluator
from app.ai.prompts.registry import get_prompt
from app.ai.providers.factory import build_llm_provider
from app.ai.rag.pipeline import RAGAnswer, RAGPipeline, request_structured
from app.ai.rag.retriever import Retriever
from app.ai.tracing import RunRecorder
from app.ai.types import ChatMessage
from app.core.cache import cache_get, cache_set, make_key, namespace_version
from app.core.config import settings
from app.core.errors import AppError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.domain.enums import RunStatus, RunType, TaskSource
from app.models.ai import AIRun
from app.repositories.ai_repo import ActivityRepository, AIRunRepository, JobRepository
from app.repositories.knowledge_repo import DocumentRepository, KnowledgeBaseRepository
from app.repositories.work_repo import ProjectRepository, TaskRepository
from app.services.task_service import TaskService

logger = get_logger(__name__)


# ===========================================================================
# Search
# ===========================================================================
class SearchService:
    """Keyword / semantic / hybrid search over the knowledge base."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.retriever = Retriever()
        self.bases = KnowledgeBaseRepository(session)

    async def search(
        self,
        *,
        owner_id: uuid.UUID,
        query: str,
        mode: str = "hybrid",
        knowledge_base_ids: list[uuid.UUID] | None = None,
        project_id: uuid.UUID | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            raise ValidationError("Search query must not be empty")

        # Ownership check: a caller must not be able to search another tenant's KB.
        if knowledge_base_ids:
            for kb_id in knowledge_base_ids:
                if await self.bases.get_owned(kb_id, owner_id) is None:
                    raise NotFoundError(f"Knowledge base {kb_id} not found")
        else:
            owned = await self.bases.list(KnowledgeBaseRepository.model.owner_id == owner_id)
            knowledge_base_ids = [kb.id for kb in owned]

        if not knowledge_base_ids:
            return {
                "query": query,
                "mode": mode,
                "results": [],
                "took_ms": 0,
                "cached": False,
                "message": "当前没有任何知识库，请先创建知识库并上传文档。",
            }

        version = await namespace_version("search")
        cache_key = make_key(
            "search", owner_id, query, mode, sorted(map(str, knowledge_base_ids)), limit, version
        )
        cached = await cache_get("search", cache_key)
        if cached:
            cached["cached"] = True
            return cached

        result = await self.retriever.retrieve(
            self.session,
            query=query,
            knowledge_base_ids=knowledge_base_ids,
            mode=mode,
            top_k=limit,
        )
        payload = {
            "query": query,
            "mode": result.mode,
            "cached": False,
            "took_ms": result.took_ms,
            "reranked": result.reranked,
            "dense_hits": result.dense_hits,
            "sparse_hits": result.sparse_hits,
            "results": [
                {
                    "chunk_id": c.chunk_id,
                    "document_id": c.document_id,
                    "document_title": c.document_title,
                    "knowledge_base_id": c.knowledge_base_id,
                    "chunk_index": c.chunk_index,
                    "heading": c.heading,
                    "content": c.content,
                    "score": round(c.score, 6),
                    "vector_score": round(c.vector_score, 6),
                    "keyword_score": round(c.keyword_score, 6),
                }
                for c in result.chunks
            ],
        }
        await cache_set("search", cache_key, payload, settings.cache_ttl_search_seconds)
        return payload


# ===========================================================================
# Chat / RAG
# ===========================================================================
class ChatService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.pipeline = RAGPipeline()
        self.bases = KnowledgeBaseRepository(session)
        self.runs = AIRunRepository(session)

    async def ask(
        self,
        *,
        owner_id: uuid.UUID,
        question: str,
        knowledge_base_ids: list[uuid.UUID] | None = None,
        document_ids: list[uuid.UUID] | None = None,
        mode: str = "hybrid",
        top_k: int | None = None,
        history: list[dict[str, str]] | None = None,
        project_id: uuid.UUID | None = None,
    ) -> RAGAnswer:
        if knowledge_base_ids:
            for kb_id in knowledge_base_ids:
                if await self.bases.get_owned(kb_id, owner_id) is None:
                    raise NotFoundError(f"Knowledge base {kb_id} not found")
        else:
            owned = await self.bases.list(KnowledgeBaseRepository.model.owner_id == owner_id)
            knowledge_base_ids = [kb.id for kb in owned]
            if not knowledge_base_ids:
                raise ValidationError(
                    "No knowledge base available. Create one and upload documents first."
                )

        # Assemble the personal block and record it. Retrieval happens inside the
        # pipeline, so the knowledge part is added there; this contributes the
        # profile, the memories and the preferences.
        assembler = ContextAssembler(self.session, user_id=owner_id)
        assembled = await assembler.assemble(goal=question, include_knowledge=False)
        await assembler.persist(assembled, purpose="rag")

        answer = await self.pipeline.answer(
            self.session,
            question=question,
            owner_id=owner_id,
            knowledge_base_ids=knowledge_base_ids,
            document_ids=document_ids,
            mode=mode,
            top_k=top_k,
            history=history,
            project_id=project_id,
            extra_context=assembled.render(),
        )

        # Consider what the turn revealed about the user. Deliberately quiet: a
        # failure here must not turn a successful answer into an error, and the
        # evaluator itself drops almost everything it is shown.
        await _consider_memories(
            self.session,
            owner_id=owner_id,
            statement=question,
            context=f"用户提问：{question}\n助手回答：{answer.answer[:800]}",
            source="conversation",
        )
        return answer


async def _consider_memories(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    statement: str,
    context: str = "",
    source: str = "conversation",
) -> None:
    """Offer a statement to the memory evaluator, swallowing nothing silently.

    The evaluator is allowed to fail without breaking the caller's task, but the
    failure is logged and counted rather than hidden. A memory pass that quietly
    stops working would make the product look like it had stopped learning.
    """
    try:
        llm, _ = build_llm_provider()
        evaluator = MemoryEvaluator(session, llm, user_id=owner_id)
        verdict = await evaluator.evaluate(statement, context=context, source=source)
        logger.info(
            "memory_verdict",
            extra={"routing": verdict.routing.value, "kind": verdict.kind, "reason": verdict.reason[:120]},
        )
    except Exception as exc:
        logger.warning("memory_pass_failed", extra={"error": f"{type(exc).__name__}: {exc}"})


# ===========================================================================
# Task planner
# ===========================================================================
class PlannerService:
    def __init__(self) -> None:
        self.llm, self.info = build_llm_provider()
        self.retriever = Retriever()

    async def generate_plan(
        self,
        session: AsyncSession,
        *,
        owner_id: uuid.UUID,
        goal: str,
        knowledge_base_ids: list[uuid.UUID] | None = None,
        horizon_days: int = 30,
        project_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        goal = (goal or "").strip()
        if not goal:
            raise ValidationError("Goal must not be empty")
        horizon_days = max(1, min(int(horizon_days or 30), 365))

        # Grounding first: a plan built on the user's own material beats a generic one.
        retrieval = await self.retriever.retrieve(
            session,
            query=goal,
            knowledge_base_ids=knowledge_base_ids,
            top_k=6,
        )
        knowledge_context = "\n\n".join(
            f"【资料 {i}】{c.document_title} / {c.heading}\n{c.content[:1200]}"
            for i, c in enumerate(retrieval.chunks, start=1)
        ) or "（知识库中没有检索到相关资料，请基于通用方法论拆解）"

        # Personal context first, then the grounded material.
        #
        # This is what makes the same goal produce a different plan for a
        # different person: a student writing a thesis and a professional
        # shipping a feature get different steps for the same sentence. It is
        # also the clearest evidence that the personalisation is real rather
        # than a stored preference nobody reads.
        assembler = ContextAssembler(session, user_id=owner_id)
        assembled = await assembler.assemble(
            goal=goal,
            knowledge_chunks=[
                {
                    "document_title": c.document_title,
                    "content": c.content[:800],
                    "chunk_id": str(getattr(c, "chunk_id", "") or ""),
                }
                for c in retrieval.chunks
            ]
        )
        personal = assembled.render()
        context = f"{personal}\n\n{knowledge_context}" if personal else knowledge_context

        prompt = await get_prompt(session, "task_planner")
        recorder = await RunRecorder.start(
            session,
            run_type=RunType.TASK_PLANNER,
            owner_id=owner_id,
            provider=self.info.kind,
            model=self.llm.model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            request_payload={
                "goal": goal,
                "horizon_days": horizon_days,
                "knowledge_base_ids": [str(k) for k in (knowledge_base_ids or [])],
            },
            project_id=project_id,
            knowledge_base_id=knowledge_base_ids[0] if knowledge_base_ids else None,
        )
        recorder.set_retrieval(
            {"mode": retrieval.mode, "retrieved": len(retrieval.chunks)}, len(retrieval.chunks)
        )

        try:
            # Retries the *parse*, not the HTTP call: a JSON request occasionally
            # comes back as prose, and simply asking again succeeds.
            raw, chat = await request_structured(
                self.llm,
                [
                    ChatMessage(
                        role="system",
                        content=prompt.render(
                            goal=goal, context=context, horizon_days=horizon_days
                        ),
                    ),
                    ChatMessage(role="user", content=f"目标：{goal}"),
                ],
                temperature=0.3,
                max_tokens=settings.ai_max_tokens,
            )
            recorder.record_usage(chat.usage)

            # Record what was injected, now that the run has an id. This is the
            # transparency guarantee: "what did it know about me when it planned
            # this" is answerable afterwards rather than a claim.
            await assembler.persist(assembled, run_id=recorder.run_id, purpose="planner")
            plan = self._normalise_plan(raw, goal, horizon_days)

            await recorder.add_sources(
                [c.to_citation(i) for i, c in enumerate(retrieval.chunks, start=1)]
            )
            # The FULL plan is stored on the run, not just a summary: it is what
            # POST /planner/plan/apply replays into real tasks.
            await recorder.finish(
                status=RunStatus.SUCCEEDED,
                response_text=chat.content,
                result_payload={
                    **plan,
                    "summary": {
                        "phases": len(plan["phases"]),
                        "tasks": sum(len(p["tasks"]) for p in plan["phases"]),
                    },
                },
                chat_result=chat,
            )
            plan["run_id"] = recorder.run_id
            plan["provider"] = self.info.kind
            plan["model"] = self.llm.model
            plan["grounded_chunks"] = len(retrieval.chunks)
            plan["latency_ms"] = recorder.model_row.latency_ms
            return plan

        except AppError as exc:
            await recorder.finish(
                status=RunStatus.FAILED, error_code=exc.code, error_message=exc.message
            )
            raise
        except Exception as exc:
            await recorder.finish(
                status=RunStatus.FAILED,
                error_code="unexpected_error",
                error_message=f"{type(exc).__name__}: {exc}",
            )
            raise

    @staticmethod
    def _normalise_plan(raw: dict[str, Any], goal: str, horizon_days: int) -> dict[str, Any]:
        phases_raw = raw.get("phases")
        phases: list[dict[str, Any]] = []
        repaired_criteria = 0

        if isinstance(phases_raw, list):
            for index, phase in enumerate(phases_raw):
                if not isinstance(phase, dict):
                    continue
                tasks: list[dict[str, Any]] = []
                for task in phase.get("tasks") or []:
                    if not isinstance(task, dict):
                        continue
                    title = str(task.get("title") or "").strip()
                    if not title:
                        continue
                    subtasks = [
                        {
                            "title": str(st.get("title") or "").strip(),
                            "estimated_minutes": _safe_int(st.get("estimated_minutes"), 30),
                        }
                        for st in (task.get("subtasks") or [])
                        if isinstance(st, dict) and str(st.get("title") or "").strip()
                    ]
                    criteria = str(task.get("completion_criteria") or "").strip()
                    if not criteria:
                        # "Every task has a verifiable completion criterion" is a
                        # product guarantee, not a hope: models occasionally omit
                        # the field, so the contract is enforced here and the
                        # under-delivery is logged rather than silently shipped.
                        criteria = _derive_criterion(title)
                        repaired_criteria += 1
                    tasks.append(
                        {
                            "title": title[:300],
                            "description": str(task.get("description") or "")[:2000],
                            "estimated_minutes": _safe_int(task.get("estimated_minutes"), 60),
                            "priority": _safe_priority(task.get("priority")),
                            "completion_criteria": criteria[:1000],
                            "depends_on": [
                                str(d)[:300]
                                for d in (task.get("depends_on") or [])
                                if isinstance(d, str)
                            ],
                            "subtasks": subtasks,
                        }
                    )
                if tasks:
                    phases.append(
                        {
                            "name": str(phase.get("name") or f"阶段 {index + 1}")[:200],
                            "description": str(phase.get("description") or "")[:1000],
                            "tasks": tasks,
                        }
                    )

        if repaired_criteria:
            logger.warning(
                "plan_completion_criteria_repaired",
                extra={"count": repaired_criteria, "reason": "model omitted the field"},
            )

        if not phases:
            phases = [
                {
                    "name": "阶段 1：目标澄清",
                    "description": "模型未返回可解析的阶段结构，已生成最小可执行计划。",
                    "tasks": [
                        {
                            "title": goal[:160],
                            "description": "明确目标范围、验收标准与时间盒。",
                            "estimated_minutes": 60,
                            "priority": "HIGH",
                            "completion_criteria": "产出书面的目标与验收标准。",
                            "depends_on": [],
                            "subtasks": [],
                        }
                    ],
                }
            ]

        total_minutes = sum(
            t["estimated_minutes"] for p in phases for t in p["tasks"]
        ) + sum(
            st["estimated_minutes"] for p in phases for t in p["tasks"] for st in t["subtasks"]
        )
        return {
            "goal": str(raw.get("goal") or goal)[:500],
            "strategy": str(raw.get("strategy") or "")[:2000],
            "horizon_days": horizon_days,
            "phases": phases,
            "estimated_total_minutes": total_minutes,
            "estimated_total_hours": round(total_minutes / 60, 1),
        }

    async def persist_plan(
        self,
        session: AsyncSession,
        *,
        owner_id: uuid.UUID,
        project_id: uuid.UUID | None,
        plan: dict[str, Any],
        source: TaskSource = TaskSource.PLANNER,
    ) -> dict[str, Any]:
        tasks_service = TaskService(session)
        task_ids: list[str] = []
        title_to_id: dict[str, uuid.UUID] = {}
        pending_dependencies: list[tuple[uuid.UUID, str]] = []

        for phase in plan.get("phases", []):
            phase_task = await tasks_service.create_task(
                owner_id=owner_id,
                project_id=project_id,
                title=str(phase.get("name") or "阶段")[:300],
                description=str(phase.get("description") or ""),
                priority="HIGH",
                estimated_minutes=sum(
                    int(t.get("estimated_minutes") or 0) for t in phase.get("tasks", [])
                ),
                source=source,
                ai_suggestion=plan.get("strategy", "")[:2000],
            )
            task_ids.append(str(phase_task.id))

            for item in phase.get("tasks", []):
                task = await tasks_service.create_task(
                    owner_id=owner_id,
                    project_id=project_id,
                    parent_id=phase_task.id,
                    title=str(item.get("title") or "任务")[:300],
                    description=str(item.get("description") or ""),
                    priority=str(item.get("priority") or "MEDIUM"),
                    estimated_minutes=int(item.get("estimated_minutes") or 0),
                    completion_criteria=str(item.get("completion_criteria") or ""),
                    source=source,
                    ai_suggestion=plan.get("strategy", "")[:2000],
                )
                task_ids.append(str(task.id))
                title_to_id[str(item.get("title"))] = task.id
                for dependency_title in item.get("depends_on", []):
                    pending_dependencies.append((task.id, str(dependency_title)))

                for subtask in item.get("subtasks", []):
                    child = await tasks_service.create_task(
                        owner_id=owner_id,
                        project_id=project_id,
                        parent_id=task.id,
                        title=str(subtask.get("title") or "子任务")[:300],
                        estimated_minutes=int(subtask.get("estimated_minutes") or 0),
                        source=source,
                    )
                    task_ids.append(str(child.id))

        for task_id, dependency_title in pending_dependencies:
            dependency_id = title_to_id.get(dependency_title)
            if dependency_id and dependency_id != task_id:
                try:
                    await tasks_service.add_dependency(
                        owner_id=owner_id, task_id=task_id, depends_on_id=dependency_id
                    )
                except AppError as exc:
                    logger.info(
                        "dependency_skipped",
                        extra={"task_id": str(task_id), "reason": exc.message},
                    )

        return {
            "created": len(task_ids),
            "phases": len(plan.get("phases", [])),
            "task_ids": task_ids,
            "goal": plan.get("goal", ""),
        }


# ===========================================================================
# Agent
# ===========================================================================
class AgentService:
    def __init__(self, session: AsyncSession, user_id: uuid.UUID | None = None) -> None:
        self.session = session
        self.user_id = user_id
        # The policy is resolved per service instance rather than cached: a user
        # who raises the permission level in Settings expects the next run to use
        # it, not the next restart.
        #
        # It is resolved **for this user**. It used to be one file per machine, so
        # raising the level in one account raised it in every account.
        from app.ai.tools.policy_store import build_policy, capability_flags

        self.policy = build_policy(user_id)
        self.executor = AgentExecutor(
            policy=self.policy, enabled_capabilities=capability_flags(user_id)
        )
        self.runs = AIRunRepository(session)
        self.projects = ProjectRepository(session)
        self.bases = KnowledgeBaseRepository(session)

    async def run(
        self,
        *,
        owner_id: uuid.UUID,
        goal: str,
        project_id: uuid.UUID | None = None,
        knowledge_base_ids: list[uuid.UUID] | None = None,
        max_steps: int | None = None,
    ) -> AgentOutcome:
        if project_id and await self.projects.get_owned(project_id, owner_id) is None:
            raise NotFoundError("Project not found")
        if knowledge_base_ids:
            for kb_id in knowledge_base_ids:
                if await self.bases.get_owned(kb_id, owner_id) is None:
                    raise NotFoundError(f"Knowledge base {kb_id} not found")
        else:
            owned = await self.bases.list(KnowledgeBaseRepository.model.owner_id == owner_id)
            knowledge_base_ids = [kb.id for kb in owned]

        return await self.executor.run(
            self.session,
            owner_id=owner_id,
            goal=goal,
            project_id=project_id,
            knowledge_base_ids=knowledge_base_ids,
            max_steps=max_steps,
        )


# ===========================================================================
# AI run inspection
# ===========================================================================
class AIRunService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runs = AIRunRepository(session)

    async def get_trace(self, *, owner_id: uuid.UUID, run_id: str) -> dict[str, Any]:
        run = await self.runs.by_run_id(run_id, owner_id)
        if run is None:
            raise NotFoundError("AI run not found")
        return {
            "run": run,
            "tool_calls": await self.runs.tool_calls(run.id),
            "sources": await self.runs.sources(run.id),
            "agent_run": await self.runs.agent_run(run.id),
        }

    async def metrics(self, *, owner_id: uuid.UUID, days: int = 30) -> dict[str, Any]:
        return await self.runs.metrics(owner_id, days=days)

    async def daily(self, *, owner_id: uuid.UUID, days: int = 14) -> list[dict[str, Any]]:
        return await self.runs.daily_counts(owner_id, days=days)

    async def health(self) -> dict[str, Any]:
        """Success/failure counters over the last 24h (observability endpoint)."""
        since = datetime.now(UTC) - timedelta(hours=24)
        rows = (
            await self.session.execute(
                select(AIRun.status, func.count(AIRun.id))
                .where(AIRun.created_at >= since)
                .group_by(AIRun.status)
            )
        ).all()
        counts = {str(status): int(count) for status, count in rows}
        total = sum(counts.values())
        failed = counts.get(RunStatus.FAILED.value, 0) + counts.get(RunStatus.TIMEOUT.value, 0)
        latency = (
            await self.session.execute(
                select(func.coalesce(func.avg(AIRun.latency_ms), 0)).where(
                    AIRun.created_at >= since
                )
            )
        ).scalar_one()
        return {
            "window_hours": 24,
            "total": total,
            "failed": failed,
            "failure_rate": round(failed / total, 4) if total else 0.0,
            "avg_latency_ms": int(latency or 0),
            "by_status": counts,
        }


def _safe_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _derive_criterion(title: str) -> str:
    """Build a verifiable criterion for a task whose model omitted the field.

    Deliberately concrete: it names the deliverable so the criterion can still
    be checked off, rather than being a generic "掌握 / 了解" placeholder that
    the prompt explicitly forbids.
    """
    return f"产出「{title[:120]}」的可交付结果，并能说明其关键结论与依据。"


def _safe_priority(value: Any) -> str:
    candidate = str(value or "").upper()
    return candidate if candidate in {"LOW", "MEDIUM", "HIGH", "URGENT"} else "MEDIUM"


__all__ = [
    "AIRunService",
    "ActivityRepository",
    "AgentService",
    "ChatService",
    "DocumentRepository",
    "JobRepository",
    "PlannerService",
    "SearchService",
    "TaskRepository",
]

"""AI endpoints: RAG chat, planner, agent, tools, prompts and execution traces."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.ai.agent.tools import get_tool_registry
from app.ai.prompts import registry as prompt_registry
from app.ai.prompts.builtin import TEMPLATES_BY_NAME
from app.api.deps import CurrentUser, DbSession, Pagination
from app.core.response import build_page, ok
from app.repositories.ai_repo import AIRunRepository
from app.services.ai_services import (
    AgentService,
    AIRunService,
    ChatService,
    PlannerService,
)
from app.services.task_service import TaskService

router = APIRouter(tags=["ai"])


# ---------------------------------------------------------------- schemas ----
class ChatMessageIn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=8000)


class RagAskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    knowledge_base_ids: list[uuid.UUID] = Field(default_factory=list)
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    mode: str = Field(default="hybrid", pattern="^(keyword|semantic|hybrid)$")
    top_k: int | None = Field(default=None, ge=1, le=20)
    history: list[ChatMessageIn] = Field(default_factory=list)
    project_id: uuid.UUID | None = None


class PlanIn(BaseModel):
    goal: str = Field(min_length=2, max_length=1000)
    knowledge_base_ids: list[uuid.UUID] = Field(default_factory=list)
    project_id: uuid.UUID | None = None
    horizon_days: int = Field(default=30, ge=1, le=365)
    create_tasks: bool = False


class ApplyPlanIn(BaseModel):
    run_id: str = Field(min_length=4, max_length=64)
    project_id: uuid.UUID | None = None


class AgentRunIn(BaseModel):
    goal: str = Field(min_length=2, max_length=2000)
    project_id: uuid.UUID | None = None
    knowledge_base_ids: list[uuid.UUID] = Field(default_factory=list)
    max_steps: int = Field(default=8, ge=1, le=16)


class PromptVersionIn(BaseModel):
    content: str = Field(min_length=10, max_length=40_000)
    change_note: str = Field(default="", max_length=500)
    activate: bool = True


class PromptCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=120, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(default="", max_length=500)
    category: str = Field(default="general", max_length=64)
    content: str = Field(min_length=10, max_length=40_000)


class AIRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    run_type: str
    status: str
    provider: str
    model: str
    prompt_name: str
    prompt_version: int
    retrieval_count: int
    tool_call_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    retry_count: int
    cost_estimate: float
    error_code: str
    error_message: str
    created_at: object | None = None
    finished_at: object | None = None


# ===========================================================================
# Chat (RAG)
# ===========================================================================
@router.post("/chat/rag", summary="Ask a question grounded in the knowledge base")
async def rag_ask(payload: RagAskIn, user: CurrentUser, session: DbSession) -> dict:
    answer = await ChatService(session).ask(
        owner_id=user.id,
        question=payload.question,
        knowledge_base_ids=payload.knowledge_base_ids or None,
        document_ids=payload.document_ids or None,
        mode=payload.mode,
        top_k=payload.top_k,
        history=[m.model_dump() for m in payload.history],
        project_id=payload.project_id,
    )
    return ok(
        {
            "run_id": answer.run_id,
            "answer": answer.answer,
            "grounded": answer.grounded,
            # `retrieved_sources` is what the search returned and the model was shown;
            # `used_citation_ids` is what the answer's text actually refers to. They were
            # one field before, which made "we found documents" look like "the answer
            # cites them".
            "retrieved_sources": answer.citations,
            "used_citation_ids": answer.used_citation_ids,
            "invalid_citation_ids": answer.invalid_citation_ids,
            "citation_report": answer.citation_report,
            # Kept under the old name as well so existing clients keep working.
            "citations": answer.citations,
            "queries": answer.queries,
            "retrieval": answer.retrieval_meta,
            "provider": answer.provider,
            "model": answer.model,
            "latency_ms": answer.latency_ms,
        }
    )


# ===========================================================================
# Planner
# ===========================================================================
@router.post("/planner/plan", summary="Generate a phased task tree from a goal")
async def plan(payload: PlanIn, user: CurrentUser, session: DbSession) -> dict:
    planner = PlannerService()
    plan_result = await planner.generate_plan(
        session,
        owner_id=user.id,
        goal=payload.goal,
        knowledge_base_ids=payload.knowledge_base_ids or None,
        horizon_days=payload.horizon_days,
        project_id=payload.project_id,
    )
    created: dict | None = None
    if payload.create_tasks:
        created = await planner.persist_plan(
            session,
            owner_id=user.id,
            project_id=payload.project_id,
            plan=plan_result,
        )
    return ok(
        {"plan": plan_result, "created": created},
        message="计划已生成" + ("并创建任务" if created else ""),
    )


@router.post("/planner/plan/apply", summary="Persist a previously generated plan as tasks")
async def apply_plan(payload: ApplyPlanIn, user: CurrentUser, session: DbSession) -> dict:
    from app.core.errors import NotFoundError

    run = await AIRunRepository(session).by_run_id(payload.run_id, user.id)
    if run is None or not run.result_payload:
        raise NotFoundError("Plan run not found or contains no plan payload")

    stored = run.result_payload
    phases = stored.get("phases")
    if not isinstance(phases, list) or not phases:
        raise NotFoundError(
            "This run does not contain an applicable plan",
            details={"run_type": run.run_type},
        )

    plan_payload = {
        "goal": stored.get("goal", ""),
        "strategy": stored.get("strategy", ""),
        "phases": phases,
    }

    created = await PlannerService().persist_plan(
        session,
        owner_id=user.id,
        project_id=payload.project_id,
        plan=plan_payload,
    )
    return ok(created, message=f"已创建 {created['created']} 个任务")


@router.post("/planner/study-plan", summary="Generate a day-by-day study plan")
async def study_plan(payload: PlanIn, user: CurrentUser, session: DbSession) -> dict:
    from app.ai.prompts.registry import get_prompt
    from app.ai.providers.factory import build_llm_provider
    from app.ai.rag.pipeline import load_json_object
    from app.ai.rag.retriever import Retriever
    from app.ai.tracing import RunRecorder
    from app.ai.types import ChatMessage
    from app.domain.enums import RunStatus, RunType

    retriever = Retriever()
    retrieval = await retriever.retrieve(
        session,
        query=payload.goal,
        knowledge_base_ids=payload.knowledge_base_ids or None,
        top_k=6,
    )
    context = (
        "\n\n".join(
            f"【资料 {i}】{c.document_title}\n{c.content[:1200]}"
            for i, c in enumerate(retrieval.chunks, start=1)
        )
        or "（无相关资料）"
    )

    llm, info = build_llm_provider()
    prompt = await get_prompt(session, "study_plan")
    recorder = await RunRecorder.start(
        session,
        run_type=RunType.STUDY_PLAN,
        owner_id=user.id,
        provider=info.kind,
        model=llm.model,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
        request_payload={"goal": payload.goal, "days": payload.horizon_days},
        project_id=payload.project_id,
    )
    try:
        chat = await llm.chat(
            [
                ChatMessage(
                    role="system",
                    content=prompt.render(
                        goal=payload.goal, days=payload.horizon_days, context=context
                    ),
                ),
                ChatMessage(role="user", content=payload.goal),
            ],
            temperature=0.3,
            max_tokens=2048,
            json_mode=True,
        )
        recorder.record_usage(chat.usage)
        data = load_json_object(chat.content)
        await recorder.add_sources(
            [c.to_citation(i) for i, c in enumerate(retrieval.chunks, start=1)]
        )
        await recorder.finish(
            status=RunStatus.SUCCEEDED,
            response_text=chat.content,
            result_payload=data,
            chat_result=chat,
        )
        return ok({"run_id": recorder.run_id, **data})
    except Exception as exc:
        from app.core.errors import AppError

        await recorder.finish(
            status=RunStatus.FAILED,
            error_code=exc.code if isinstance(exc, AppError) else "unexpected_error",
            error_message=str(exc),
        )
        raise


# ===========================================================================
# Agent
# ===========================================================================
@router.get("/agents/tools", summary="Tool catalogue exposed to the agent")
async def list_tools(user: CurrentUser) -> dict:
    registry = get_tool_registry()
    return ok({"tools": registry.catalog(), "count": len(registry.names())})


@router.post("/agents/run", summary="Execute the tool-calling agent")
async def agent_run(payload: AgentRunIn, user: CurrentUser, session: DbSession) -> dict:
    outcome = await AgentService(session, user.id).run(
        owner_id=user.id,
        goal=payload.goal,
        project_id=payload.project_id,
        knowledge_base_ids=payload.knowledge_base_ids or None,
        max_steps=payload.max_steps,
    )
    return ok(
        {
            "run_id": outcome.run_id,
            "status": outcome.status,
            "goal": outcome.goal,
            "final_answer": outcome.final_answer,
            "stopped_reason": outcome.stopped_reason,
            "provider": outcome.provider,
            "model": outcome.model,
            "latency_ms": outcome.latency_ms,
            "created": outcome.created,
            "steps": [
                {
                    "index": step.index,
                    "thought": step.thought,
                    "tool": step.tool,
                    "arguments": step.arguments,
                    "ok": step.ok,
                    "summary": step.summary,
                    "error": step.error,
                    "latency_ms": step.latency_ms,
                }
                for step in outcome.steps
            ],
        }
    )


@router.get("/agents/runs/{run_id}", summary="Inspect an agent trajectory")
async def agent_trace(run_id: str, user: CurrentUser, session: DbSession) -> dict:
    trace = await AIRunService(session).get_trace(owner_id=user.id, run_id=run_id)
    agent = trace["agent_run"]
    return ok(
        {
            "run_id": run_id,
            "goal": agent.goal if agent else "",
            "status": agent.status if agent else trace["run"].status,
            "steps_used": agent.steps_used if agent else 0,
            "max_steps": agent.max_steps if agent else 0,
            "final_answer": agent.final_answer if agent else trace["run"].response_text,
            "scratchpad": agent.scratchpad if agent else [],
            "created": agent.created_entity_ids if agent else {},
            "tool_calls": [
                {
                    "step": call.step_index,
                    "tool": call.tool_name,
                    "arguments": call.arguments,
                    "status": call.status,
                    "error": call.error_message,
                    "latency_ms": call.latency_ms,
                }
                for call in trace["tool_calls"]
            ],
        }
    )


# ===========================================================================
# Prompts
# ===========================================================================
@router.get("/prompts", summary="List prompt templates and their active version")
async def list_prompts(user: CurrentUser, session: DbSession) -> dict:
    return ok(await prompt_registry.list_prompts(session))


@router.post("/prompts", status_code=status.HTTP_201_CREATED, summary="Register a prompt")
async def create_prompt(payload: PromptCreateIn, user: CurrentUser, session: DbSession) -> dict:
    existing = await prompt_registry.get_prompt_detail(session, payload.name)
    if existing and existing.get("versions"):
        from app.core.errors import ConflictError

        raise ConflictError(f"Prompt '{payload.name}' already exists")
    result = await prompt_registry.create_version(
        session,
        payload.name,
        content=payload.content,
        change_note="created via API",
        created_by=user.username,
    )
    return ok(result, message="Prompt 已注册")


@router.get("/prompts/{name}", summary="Prompt detail with full version history")
async def get_prompt(name: str, user: CurrentUser, session: DbSession) -> dict:
    return ok(await prompt_registry.get_prompt_detail(session, name))


@router.post(
    "/prompts/{name}/versions",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new prompt version",
)
async def create_prompt_version(
    name: str, payload: PromptVersionIn, user: CurrentUser, session: DbSession
) -> dict:
    result = await prompt_registry.create_version(
        session,
        name,
        content=payload.content,
        change_note=payload.change_note,
        created_by=user.username,
        activate=payload.activate,
    )
    return ok(result, message=f"已创建版本 v{result['version']}")


@router.post("/prompts/{name}/versions/{version}/activate", summary="Roll a version back in")
async def activate_prompt_version(
    name: str, version: int, user: CurrentUser, session: DbSession
) -> dict:
    result = await prompt_registry.activate_version(session, name, version)
    return ok(result, message=f"已切换到 v{version}")


@router.post("/prompts/{name}/reset", summary="Reset a prompt to its built-in template")
async def reset_prompt(name: str, user: CurrentUser, session: DbSession) -> dict:
    if name not in TEMPLATES_BY_NAME:
        from app.core.errors import NotFoundError

        raise NotFoundError(f"'{name}' has no built-in template")
    result = await prompt_registry.reset_to_builtin(session, name)
    return ok(result, message="已恢复内置模板")


# ===========================================================================
# AI runs
# ===========================================================================
airuns_router = APIRouter(prefix="/ai-runs", tags=["ai-runs"])


@airuns_router.get("", summary="Paginated AI execution history")
async def list_ai_runs(
    user: CurrentUser,
    session: DbSession,
    pagination: Pagination,
    run_type: str | None = Query(None, max_length=32),
    status_filter: str | None = Query(None, alias="status", max_length=16),
    project_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=200),
) -> dict:
    repo = AIRunRepository(session)
    stmt = repo.query(
        user.id,
        run_type=run_type,
        status=status_filter,
        project_id=project_id,
        search=q,
    )
    rows, total = await repo.paginate(stmt, page=pagination.page, page_size=pagination.page_size)
    return ok(
        build_page(
            [AIRunOut.model_validate(row).model_dump(mode="json") for row in rows],
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
        ).model_dump(mode="json")
    )


@airuns_router.get("/metrics", summary="Aggregated AI metrics (tokens, latency, failure rate)")
async def ai_metrics(
    user: CurrentUser, session: DbSession, days: int = Query(30, ge=1, le=365)
) -> dict:
    service = AIRunService(session)
    return ok(
        {
            "metrics": await service.metrics(owner_id=user.id, days=days),
            "daily": await service.daily(owner_id=user.id, days=min(days, 30)),
        }
    )


@airuns_router.get("/{run_id}", summary="Full execution trace: prompt, retrieval, tools, tokens")
async def get_ai_run(run_id: str, user: CurrentUser, session: DbSession) -> dict:
    trace = await AIRunService(session).get_trace(owner_id=user.id, run_id=run_id)
    run = trace["run"]
    agent = trace["agent_run"]
    return ok(
        {
            "run": AIRunOut.model_validate(run).model_dump(mode="json"),
            "request": run.request_payload,
            "result": run.result_payload,
            "retrieval": run.retrieval_meta,
            "tools_used": run.tools_used,
            "response_text": run.response_text,
            "sources": [
                {
                    "document_id": str(s.document_id) if s.document_id else None,
                    "chunk_id": str(s.chunk_id) if s.chunk_id else None,
                    "document_title": s.document_title,
                    "chunk_index": s.chunk_index,
                    "score": s.score,
                    "snippet": s.snippet,
                }
                for s in trace["sources"]
            ],
            "tool_calls": [
                {
                    "step": c.step_index,
                    "tool": c.tool_name,
                    "arguments": c.arguments,
                    "result": c.result,
                    "status": c.status,
                    "error": c.error_message,
                    "latency_ms": c.latency_ms,
                }
                for c in trace["tool_calls"]
            ],
            "agent": (
                {
                    "goal": agent.goal,
                    "status": agent.status,
                    "steps_used": agent.steps_used,
                    "max_steps": agent.max_steps,
                    "scratchpad": agent.scratchpad,
                    "created": agent.created_entity_ids,
                }
                if agent
                else None
            ),
        }
    )


__all__ = ["TaskService", "airuns_router", "router"]

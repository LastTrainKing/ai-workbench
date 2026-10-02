"""Tool-calling agent executor.

Loop
----
1. Build the system prompt from the versioned ``agent_system`` template with the
   live tool catalogue injected.
2. Ask the model for the next action (native tool calling when the provider
   supports it).
3. Execute at most one tool per step, append the observation, repeat.
4. Stop on: final answer, ``max_steps``, a repeated identical call (loop guard),
   or a fatal provider error.

Guarantees
----------
* The agent never touches the database — only tools do (see ``tools.py``).
* Write operations are counted and capped per run (``ToolContext.max_write_ops``).
* Every step is persisted on ``tool_calls`` + ``agent_runs.scratchpad``, so the
  whole trajectory is replayable from the AI Run page.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent.tools import ToolContext, ToolRegistry, get_tool_registry
from app.ai.prompts.registry import get_prompt
from app.ai.providers.factory import build_llm_provider
from app.ai.tracing import RunRecorder
from app.ai.types import ChatMessage, ToolResult, estimate_tokens
from app.core.config import settings
from app.core.errors import AIError
from app.core.logging import get_logger
from app.domain.enums import RunStatus, RunType

logger = get_logger(__name__)

DEFAULT_MAX_STEPS = 8
_TOOL_BLOCK = re.compile(r"\{.*?\}", re.DOTALL)


@dataclass(slots=True)
class AgentStep:
    index: int
    thought: str
    tool: str
    arguments: dict[str, Any]
    ok: bool
    summary: str
    error: str
    latency_ms: int
    data: Any = None


@dataclass(slots=True)
class AgentOutcome:
    run_id: str
    status: str
    goal: str
    final_answer: str
    steps: list[AgentStep] = field(default_factory=list)
    created: dict[str, list[str]] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    stopped_reason: str = ""


class AgentExecutor:
    def __init__(
        self,
        registry: ToolRegistry | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> None:
        self.registry = registry or get_tool_registry()
        self.max_steps = max_steps
        self.llm, self.llm_info = build_llm_provider()

    # ------------------------------------------------------------------ prompt --
    def _tool_catalogue(self) -> str:
        lines: list[str] = []
        for spec in self.registry.specs():
            params = ", ".join((spec.parameters or {}).get("properties", {}))
            marker = "[写操作]" if spec.category == "write" else "[只读]"
            lines.append(f"- {spec.name}{marker}: {spec.description} 参数: {params or '无'}")
        return "\n".join(lines)

    async def _system_prompt(
        self, session: AsyncSession, goal: str, context: str
    ) -> tuple[str, str, int]:
        prompt = await get_prompt(session, "agent_system")
        rendered = prompt.render(
            tools=self._tool_catalogue(),
            goal=goal,
            context=context or "（无）",
            max_steps=self.max_steps,
        )
        return rendered, prompt.name, prompt.version

    # --------------------------------------------------------------------- run --
    async def run(
        self,
        session: AsyncSession,
        *,
        owner_id: uuid.UUID,
        goal: str,
        project_id: uuid.UUID | None = None,
        knowledge_base_ids: list[uuid.UUID] | None = None,
        max_steps: int | None = None,
        context: str = "",
    ) -> AgentOutcome:
        goal = (goal or "").strip()
        if not goal:
            raise AIError("Agent goal must not be empty", status_code=422)

        steps_allowed = max(1, min(max_steps or self.max_steps, 16))
        system_prompt, prompt_name, prompt_version = await self._system_prompt(
            session, goal, context
        )

        recorder = await RunRecorder.start(
            session,
            run_type=RunType.AGENT,
            owner_id=owner_id,
            provider=self.llm_info.kind,
            model=self.llm.model,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            request_payload={
                "goal": goal,
                "max_steps": steps_allowed,
                "tools": self.registry.names(),
                "project_id": str(project_id) if project_id else None,
            },
            project_id=project_id,
            knowledge_base_id=knowledge_base_ids[0] if knowledge_base_ids else None,
        )
        agent_run = await recorder.attach_agent_run(goal=goal, max_steps=steps_allowed)

        ctx = ToolContext(
            session=session,
            owner_id=owner_id,
            project_id=project_id,
            knowledge_base_ids=list(knowledge_base_ids or []),
            run_id=recorder.run_id,
        )

        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=goal),
        ]
        steps: list[AgentStep] = []
        seen_calls: dict[str, int] = {}
        final_answer = ""
        stopped_reason = "final_answer"

        try:
            for step_index in range(1, steps_allowed + 1):
                chat = await self.llm.chat(
                    messages,
                    temperature=0.0,
                    max_tokens=min(settings.ai_max_tokens, 1600),
                    tools=self.registry.specs(),
                )
                recorder.record_usage(chat.usage)

                if not chat.tool_calls:
                    final_answer = chat.content or ""
                    if not final_answer.strip():
                        stopped_reason = "empty_response"
                        final_answer = (
                            "Agent 未能生成最终答复（模型返回空内容）。"
                            "请查看 AI 执行记录中的工具调用轨迹。"
                        )
                    break

                request = chat.tool_calls[0]
                signature = f"{request.name}:{json.dumps(request.arguments, sort_keys=True, default=str)}"
                seen_calls[signature] = seen_calls.get(signature, 0) + 1
                if seen_calls[signature] > 1:
                    stopped_reason = "loop_detected"
                    final_answer = (
                        f"检测到重复调用 `{request.name}`（相同参数），已主动终止以避免死循环。\n\n"
                        f"已完成的步骤：{len(steps)} 步。"
                    )
                    await recorder.record_tool_call(
                        tool_name=request.name,
                        arguments=request.arguments,
                        result={"skipped": True, "reason": "duplicate call"},
                        status="SKIPPED",
                        step_index=step_index,
                        latency_ms=0,
                        error_message="duplicate tool call detected",
                        agent_run_id=agent_run.id,
                    )
                    break

                result: ToolResult = await self.registry.execute(
                    ctx, request.name, request.arguments
                )
                step = AgentStep(
                    index=step_index,
                    thought=(chat.content or "").strip()[:2000],
                    tool=request.name,
                    arguments=request.arguments,
                    ok=result.ok,
                    summary=result.summary,
                    error=result.error,
                    latency_ms=result.latency_ms,
                    data=result.data if result.ok else None,
                )
                steps.append(step)

                await recorder.record_tool_call(
                    tool_name=request.name,
                    arguments=request.arguments,
                    result=result.to_payload(),
                    status="SUCCEEDED" if result.ok else "FAILED",
                    step_index=step_index,
                    latency_ms=result.latency_ms,
                    error_message=result.error,
                    agent_run_id=agent_run.id,
                )
                agent_run.steps_used = step_index
                agent_run.scratchpad = [
                    {
                        "step": s.index,
                        "tool": s.tool,
                        "ok": s.ok,
                        "summary": s.summary,
                        "error": s.error,
                    }
                    for s in steps
                ]
                await session.flush()

                # Feed the observation back to the model.
                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=chat.content or "",
                        tool_calls=[
                            {
                                "id": request.id,
                                "type": "function",
                                "function": {
                                    "name": request.name,
                                    "arguments": json.dumps(
                                        request.arguments, ensure_ascii=False, default=str
                                    ),
                                },
                            }
                        ],
                    )
                )
                messages.append(
                    ChatMessage(
                        role="tool",
                        name=request.name,
                        tool_call_id=request.id,
                        content=json.dumps(
                            result.to_payload(limit=2500), ensure_ascii=False, default=str
                        ),
                    )
                )
            else:
                stopped_reason = "max_steps"
                final_answer = (
                    f"已达到最大步数（{steps_allowed} 步），Agent 停止继续调用工具。\n\n"
                    f"本次共执行 {len(steps)} 个工具调用，结果已记录在 AI 执行记录中。"
                )

            agent_run.status = (
                RunStatus.SUCCEEDED.value if final_answer else RunStatus.FAILED.value
            )
            agent_run.final_answer = final_answer[:20_000]
            agent_run.created_entity_ids = ctx.artifacts
            await session.flush()

            await recorder.finish(
                status=RunStatus.SUCCEEDED,
                response_text=final_answer,
                result_payload={
                    "steps": [
                        {
                            "index": s.index,
                            "tool": s.tool,
                            "ok": s.ok,
                            "summary": s.summary,
                            "error": s.error,
                            "latency_ms": s.latency_ms,
                        }
                        for s in steps
                    ],
                    "created": ctx.artifacts,
                    "stopped_reason": stopped_reason,
                },
                chat_result=None,
            )

            return AgentOutcome(
                run_id=recorder.run_id,
                status=RunStatus.SUCCEEDED.value,
                goal=goal,
                final_answer=final_answer,
                steps=steps,
                created=ctx.artifacts,
                provider=self.llm_info.kind,
                model=self.llm.model,
                latency_ms=recorder.model_row.latency_ms,
                stopped_reason=stopped_reason,
            )

        except AIError as exc:
            agent_run.status = RunStatus.FAILED.value
            agent_run.scratchpad = [
                {"step": s.index, "tool": s.tool, "ok": s.ok, "summary": s.summary}
                for s in steps
            ]
            await session.flush()
            await recorder.finish(
                status=RunStatus.TIMEOUT if "timeout" in exc.code else RunStatus.FAILED,
                error_code=exc.code,
                error_message=exc.message,
                result_payload={"steps_completed": len(steps), "created": ctx.artifacts},
            )
            raise
        except Exception as exc:
            agent_run.status = RunStatus.FAILED.value
            await session.flush()
            await recorder.finish(
                status=RunStatus.FAILED,
                error_code="unexpected_error",
                error_message=f"{type(exc).__name__}: {exc}",
                result_payload={"steps_completed": len(steps)},
            )
            raise


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    """Best-effort extraction of a JSON object from free-form model output."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        match = _TOOL_BLOCK.search(raw)
        if match:
            try:
                data = json.loads(match.group(0))
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def estimate_prompt_tokens(messages: list[ChatMessage]) -> int:
    return sum(estimate_tokens(m.content) for m in messages)

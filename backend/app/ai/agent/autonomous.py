"""The autonomous agent loop: PLAN -> EXECUTE -> OBSERVE -> VERIFY -> FIX -> RETRY.

The division of labour here is the whole design, and it is deliberate:

* **the model decides what to do** — it writes the plan, chooses tools, reads
  results and proposes fixes;
* **the code decides whether it worked** — verification is a tool call whose
  result is checked, never the model's opinion of its own output.

That split is what stops the classic failure of agent frameworks: the model
announcing success because its prose sounded confident. If ``pytest`` exits 1, the
step failed, whatever the model says about it.

The loop is bounded on every axis — steps, tool calls, retries, wall clock,
files modified. A run that cannot finish stops and says why rather than grinding
until someone notices.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.ai.agent.control import RunCancelledError, RunController
from app.ai.agent.plan import ExecutionPlan, PlanStep, RunState, StepStatus
from app.ai.agent.tools import ToolContext
from app.ai.prompts.registry import get_prompt
from app.ai.tools.executor import ResourceLimits
from app.ai.tools.permissions import AgentPolicy
from app.ai.types import ChatMessage
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Tools whose results are evidence, not opinion. A step that ran one of these
#: and saw a non-zero exit code has failed, full stop.
EVIDENCE_TOOLS = {
    "run_tests", "run_lint", "run_typecheck", "run_build",
    "terminal_exec", "powershell_exec", "cmd_exec", "python_exec", "node_exec",
}

#: How many tool calls a single step may use before it is considered stuck.
MAX_TOOLS_PER_STEP = 12

#: Phrases that mark a step as *diagnostic* rather than *assertive*.
#:
#: "Run the tests and record which ones fail" is a step whose whole purpose is to
#: see a non-zero exit code. Treating that as a failure made the agent retry the
#: diagnosis three times and never reach the fix — which is exactly what Demo 2
#: did, repeatedly, against a bug it had already identified correctly.
#:
#: A step is diagnostic when its description or its verification says so. For
#: those, producing real output is the success criterion; the exit code still
#: decides every step that asserts a result.
_DIAGNOSTIC_MARKERS = (
    "记录", "读取", "观察", "定位", "分析", "查看", "列出", "确认失败", "复现",
    "record", "read", "observe", "locate", "analyse", "analyze", "inspect",
    "list", "reproduce", "capture", "identify",
)


def is_diagnostic_step(step: PlanStep) -> bool:
    """True when the step is meant to gather information, not to assert success."""
    text = f"{step.description} {step.verification}".lower()
    return any(marker in text for marker in _DIAGNOSTIC_MARKERS)


@dataclass
class RunResult:
    """What the caller gets back."""

    run_id: str
    state: RunState
    plan: ExecutionPlan | None = None
    answer: str = ""
    steps_used: int = 0
    tool_calls: int = 0
    files_modified: int = 0
    retries: int = 0
    elapsed_seconds: float = 0.0
    snapshot_id: str = ""
    verified: bool = False
    verification_evidence: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state.value,
            "answer": self.answer,
            "steps_used": self.steps_used,
            "tool_calls": self.tool_calls,
            "files_modified": self.files_modified,
            "retries": self.retries,
            "elapsed_seconds": self.elapsed_seconds,
            "snapshot_id": self.snapshot_id,
            "verified": self.verified,
            "verification_evidence": self.verification_evidence,
            "error": self.error,
            "plan": self.plan.as_dict() if self.plan else None,
        }


class AutonomousAgent:
    """Drives one goal from plan to verified completion."""

    def __init__(
        self,
        *,
        llm: Any,
        registry: Any,
        policy: AgentPolicy,
        controller: RunController,
        session: Any,
        owner_id: Any,
        limits: ResourceLimits | None = None,
        working_directory: str = "",
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.policy = policy
        self.controller = controller
        self.session = session
        self.owner_id = owner_id
        self.working_directory = working_directory
        self.limits = limits or ResourceLimits(
            max_output_bytes=policy.max_output_bytes,
            max_files_modified=policy.max_files_modified,
            max_tool_calls=policy.max_tool_calls,
            max_runtime_seconds=policy.max_runtime_seconds,
        )
        self.ctx = ToolContext(
            session=session,
            owner_id=owner_id,
            run_id=controller.run_id,
            permissions=self._permissions(),
            working_directory=working_directory,
        )
        self._tool_messages: list[ChatMessage] = []

    def _permissions(self) -> Any:
        from app.ai.tools.permissions import PermissionManager

        return PermissionManager(self.policy)

    # ==================================================================== plan ==
    async def _make_plan(self, goal: str, context: str) -> ExecutionPlan:
        """Ask the model for a plan, and refuse to proceed without a usable one."""
        self.controller.set_state(RunState.PLANNING, "正在制定执行计划")
        prompt = await get_prompt(self.session, "agent_planner")
        catalogue = ", ".join(self.registry.names())

        messages = [
            ChatMessage(role="system", content=prompt.render(tools=catalogue, goal=goal, context=context or "（无）")),
            ChatMessage(role="user", content=f"目标：{goal}"),
        ]

        last_error = ""
        for attempt in range(1, 3):
            chat = await self.llm.chat(
                messages,
                temperature=0.1,
                max_tokens=min(settings.ai_max_tokens, 3000),
                json_mode=True,
            )
            try:
                payload = json.loads(chat.content)
            except json.JSONDecodeError:
                last_error = "模型返回的不是有效 JSON"
                self.controller.emit("error", message=last_error, data={"attempt": attempt})
                messages.append(ChatMessage(role="assistant", content=chat.content))
                messages.append(
                    ChatMessage(role="user", content="请只输出合法 JSON，不要任何解释文字。")
                )
                continue

            try:
                plan = ExecutionPlan.from_model(payload, goal)
            except ValueError as exc:
                last_error = str(exc)
                self.controller.emit("error", message=last_error)
                break

            self.controller.plan = plan
            self.controller.emit(
                "plan",
                message=f"已制定 {len(plan.steps)} 步执行计划",
                data=plan.as_dict(),
            )
            return plan

        raise RuntimeError(f"无法生成可执行的计划：{last_error}")

    # ================================================================= execute ==
    async def _run_tools_for_step(self, step: PlanStep, plan: ExecutionPlan) -> list[dict[str, Any]]:
        """Let the model work on one step until it stops calling tools."""
        system = (
            "你是运行在用户 Windows 电脑上的私人 AI Agent。\n"
            f"总目标：{plan.goal}\n"
            f"当前步骤（第 {step.id} 步）：{step.description}\n"
            f"该步骤的验收方式：{step.verification or '由你调用工具确认结果'}\n\n"
            "要求：\n"
            "1. 只处理当前这一步，不要跳到后面的步骤。\n"
            "2. 需要操作时调用工具，一次一个。\n"
            "3. 看到工具结果后再决定下一步。\n"
            "4. 这一步完成后，不要再调用工具，直接用一句话总结结果。\n"
            "5. 工具被拒绝时如实说明，不要试图绕过权限。\n"
            f"6. 可用工具：{', '.join(self.registry.names())}"
        )

        diagnostic = is_diagnostic_step(step)

        messages: list[ChatMessage] = [ChatMessage(role="system", content=system)]
        messages.extend(self._tool_messages[-6:])  # keep recent context, bounded
        messages.append(ChatMessage(role="user", content=f"请执行第 {step.id} 步：{step.description}"))

        observations: list[dict[str, Any]] = []
        summary = ""

        for _ in range(MAX_TOOLS_PER_STEP):
            await self.controller.checkpoint()

            chat = await asyncio.wait_for(
                self.llm.chat(
                    messages,
                    temperature=0.0,
                    max_tokens=min(settings.ai_max_tokens, 2500),
                    tools=self.registry.specs(),
                ),
                timeout=180,
            )

            if not chat.tool_calls:
                summary = (chat.content or "").strip()
                break

            request = chat.tool_calls[0]
            self.controller.current_tool = request.name
            self.controller.current_step_id = step.id
            self.controller.emit(
                "tool",
                message=f"{request.name}",
                data={"tool": request.name, "arguments": _trim(request.arguments)},
            )

            result = await self.registry.execute(self.ctx, request.name, request.arguments)
            self.controller.tool_calls += 1
            step.tool_calls += 1

            observation = {
                "tool": request.name,
                "ok": result.ok,
                "summary": result.summary,
                "error": result.error,
                "exit_code": getattr(result, "exit_code", None),
                "data": _trim(result.data) if result.ok else None,
            }
            observations.append(observation)
            self.controller.emit(
                "tool",
                message=f"{request.name} → {'ok' if result.ok else 'failed'}",
                data=observation,
            )

            if (
                request.name in EVIDENCE_TOOLS
                and getattr(result, "exit_code", None) not in (None, 0)
                and not diagnostic
            ):
                # For an assertive step, a failing command ends it early: continuing
                # would pile more changes on top of a broken state. A diagnostic
                # step is the opposite case — the failure *is* the finding.
                summary = f"{request.name} 退出码 {result.exit_code}"
                break

            messages.append(
                ChatMessage(
                    role="assistant",
                    content=chat.content or "",
                    reasoning_content=chat.reasoning_content or None,
                    tool_calls=[
                        {
                            "id": request.id,
                            "type": "function",
                            "function": {
                                "name": request.name,
                                "arguments": json.dumps(request.arguments, ensure_ascii=False, default=str),
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
                    content=json.dumps(result.to_payload(limit=2000), ensure_ascii=False, default=str),
                )
            )
        else:
            summary = f"该步骤达到工具调用上限（{MAX_TOOLS_PER_STEP} 次）"

        step.result = summary or step.result
        self._tool_messages.extend(messages[-2:])
        return observations

    # ================================================================== verify ==
    async def _verify_step(self, step: PlanStep, plan: ExecutionPlan) -> tuple[bool, dict[str, Any]]:
        """Decide whether a step succeeded, using evidence.

        The model is asked for a *tool call* that proves the step, not for an
        opinion. Its exit code is the verdict.
        """
        self.controller.set_state(RunState.VERIFYING, f"正在验证第 {step.id} 步")

        system = (
            "你是验证器。只判断事实，不看措辞。\n"
            f"目标：{plan.goal}\n"
            f"待验证步骤：{step.description}\n"
            f"期望：{step.verification or '该步骤的结果真实成立'}\n\n"
            "请调用**一个**工具来取得客观证据，例如：\n"
            "- 运行测试用 run_tests\n"
            "- 检查文件用 file_exists 或 file_read\n"
            "- 运行命令用 terminal_exec\n"
            "- 查看代码用 code_search\n\n"
            "只调用工具，不要输出解释。"
        )
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=f"请验证：{step.description}"),
        ]

        try:
            chat = await asyncio.wait_for(
                self.llm.chat(
                    messages,
                    temperature=0.0,
                    max_tokens=1200,
                    tools=self.registry.specs(),
                ),
                timeout=120,
            )
        except (TimeoutError, Exception) as exc:
            logger.warning("verify_call_failed", extra={"error": str(exc)})
            return False, {"tool": "", "reason": f"验证调用失败：{type(exc).__name__}"}

        if not chat.tool_calls:
            # The model would not produce evidence. That is not a pass.
            return False, {
                "tool": "",
                "reason": "验证未产生客观证据（模型没有调用任何工具）",
                "claimed": (chat.content or "")[:300],
            }

        request = chat.tool_calls[0]
        result = await self.registry.execute(self.ctx, request.name, request.arguments)
        self.controller.tool_calls += 1

        exit_code = getattr(result, "exit_code", None)
        diagnostic = is_diagnostic_step(step)
        produced_evidence = bool(
            (result.data or {}).get("stdout") or (result.data or {}).get("stderr")
            if isinstance(result.data, dict)
            else result.data
        )

        if diagnostic:
            # The step set out to observe something. It succeeded if it observed
            # it, whatever the exit code says about the state of the project.
            passed = produced_evidence or result.ok
        elif exit_code is not None:
            passed = result.ok and exit_code == 0
        else:
            passed = result.ok

        evidence = {
            "tool": request.name,
            "arguments": _trim(request.arguments),
            "ok": result.ok,
            "exit_code": exit_code,
            "summary": result.summary,
            "error": result.error,
            "passed": passed,
            "diagnostic": diagnostic,
        }
        self.controller.emit(
            "verification",
            message=f"验证{'通过' if passed else '未通过'}：{request.name}",
            data=evidence,
        )
        return passed, evidence

    # ================================================================= recover ==
    async def _recover(self, step: PlanStep, observations: list[dict[str, Any]]) -> str:
        """Ask the model what to do about a failed step. Returns a short plan."""
        self.controller.set_state(RunState.RECOVERING, f"第 {step.id} 步失败，正在分析")
        system = (
            "你正在修复一个失败的步骤。\n"
            f"目标：{plan_goal(self.controller.plan)}\n"
            f"失败的步骤：{step.description}\n"
            f"错误：{step.error or step.result}\n\n"
            "请用两三句话说明：根因是什么，下一步具体做什么。不要长篇大论。"
        )
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(
                role="user",
                content=json.dumps(observations[-4:], ensure_ascii=False, default=str)[:3000],
            ),
        ]
        try:
            chat = await asyncio.wait_for(
                self.llm.chat(messages, temperature=0.2, max_tokens=600),
                timeout=90,
            )
            analysis = (chat.content or "").strip()
        except (TimeoutError, Exception) as exc:
            analysis = f"（分析失败：{type(exc).__name__}）"

        self.controller.emit("state", message=analysis[:400] or "继续重试")
        return analysis

    # ==================================================================== run ==
    async def run(self, goal: str, *, context: str = "") -> RunResult:
        started = time.monotonic()
        self.controller.emit("state", message="开始执行", state=RunState.PLANNING.value)

        try:
            plan = await self._make_plan(goal, context)
        except RunCancelledError:
            return self._result(RunState.CANCELLED, started, error="已取消")
        except Exception as exc:
            logger.error("planning_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
            self.controller.set_state(RunState.FAILED, f"制定计划失败：{exc}")
            return self._result(RunState.FAILED, started, error=str(exc))

        # Snapshot before anything is modified, so the run can be rolled back.
        snapshot_id = ""
        try:
            from app.ai.agent.snapshot import get_snapshot_manager

            manager = get_snapshot_manager()
            snapshot = await asyncio.to_thread(
                manager.create, self.working_directory, label=goal[:120], run_id=self.controller.run_id
            )
            snapshot_id = snapshot.id
            self.controller.emit("state", message=f"已创建快照 {snapshot_id}", data={"snapshot_id": snapshot_id})
        except Exception as exc:
            logger.warning("snapshot_failed", extra={"error": str(exc)})

        self.controller.set_state(RunState.READY, "计划就绪，开始执行")

        all_evidence: list[dict[str, Any]] = []

        for step in plan.steps:
            try:
                await self.controller.checkpoint()
            except RunCancelledError:
                step.status = StepStatus.SKIPPED
                return self._result(RunState.CANCELLED, started, plan, snapshot_id, all_evidence)

            exhausted = self.limits.exhausted()
            if exhausted:
                self.controller.set_state(RunState.LIMIT_REACHED, f"已达资源上限：{exhausted}")
                return self._result(
                    RunState.LIMIT_REACHED, started, plan, snapshot_id, all_evidence, error=exhausted
                )

            step.status = StepStatus.RUNNING
            step.started_at = time.strftime("%H:%M:%S")
            self.controller.set_state(RunState.EXECUTING, f"执行第 {step.id} 步：{step.description}")

            for attempt in range(1, self.policy.max_retries + 2):
                try:
                    observations = await self._run_tools_for_step(step, plan)
                except RunCancelledError:
                    step.status = StepStatus.SKIPPED
                    return self._result(RunState.CANCELLED, started, plan, snapshot_id, all_evidence)
                except Exception as exc:
                    observations = [{"tool": "", "ok": False, "error": f"{type(exc).__name__}: {exc}"}]

                self.controller.set_state(RunState.OBSERVING, f"分析第 {step.id} 步的结果")

                passed, evidence = await self._verify_step(step, plan)
                all_evidence.append(evidence)

                if passed:
                    step.status = StepStatus.DONE
                    step.completed_at = time.strftime("%H:%M:%S")
                    step.result = step.result or evidence.get("summary", "")
                    self.controller.emit("step", message=f"第 {step.id} 步完成", data=step.as_dict())
                    break

                step.status = StepStatus.FAILED
                step.error = evidence.get("error") or evidence.get("reason", "")
                self.controller.retries += 1
                step.retry_count += 1

                if attempt > self.policy.max_retries:
                    self.controller.emit(
                        "step",
                        message=f"第 {step.id} 步在 {self.policy.max_retries} 次重试后仍失败",
                        data=step.as_dict(),
                    )
                    break

                self.controller.emit(
                    "step",
                    message=f"第 {step.id} 步未通过验证，第 {attempt} 次重试",
                    data={"attempt": attempt, "error": step.error[:400]},
                )
                await self._recover(step, observations)
                step.status = StepStatus.RUNNING

        # ------------------------------------------------------------ final --
        self.controller.set_state(RunState.VERIFYING, "正在核验成功标准")
        verified, final_evidence = await self._final_verify(plan)
        all_evidence.extend(final_evidence)

        if verified:
            self.controller.set_state(RunState.COMPLETED, "成功标准已满足")
            state = RunState.COMPLETED
        elif any(s.status is StepStatus.FAILED for s in plan.steps):
            self.controller.set_state(RunState.FAILED, "存在未完成的步骤")
            state = RunState.FAILED
        else:
            # Every step passed but the criteria did not. Report that honestly
            # rather than calling it done.
            self.controller.set_state(RunState.FAILED, "步骤已完成，但成功标准未通过核验")
            state = RunState.FAILED

        # Record what the run left behind, so rollback can detect later edits.
        if snapshot_id:
            try:
                from app.ai.agent.snapshot import get_snapshot_manager

                manager = get_snapshot_manager()
                snap = manager.get(snapshot_id)
                if snap:
                    await asyncio.to_thread(manager.mark_post_state, snap)
            except Exception as exc:
                logger.warning("post_state_failed", extra={"error": str(exc)})

        answer = await self._final_report(plan, state, all_evidence)
        return self._result(state, started, plan, snapshot_id, all_evidence, answer=answer, verified=verified)

    # ------------------------------------------------------------- final check --
    async def _final_verify(self, plan: ExecutionPlan) -> tuple[bool, list[dict[str, Any]]]:
        """Check the plan's success criteria against real evidence."""
        evidence: list[dict[str, Any]] = []
        if not plan.success_criteria:
            return True, evidence

        system = (
            "你是最终验收器。逐条核验成功标准，必须用工具取得客观证据。\n"
            "成功标准：\n"
            + "\n".join(f"{i}. {c}" for i, c in enumerate(plan.success_criteria, start=1))
            + "\n\n请为**最关键的一条**调用一个工具取得证据。只调用工具，不要解释。"
        )
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content="请核验。"),
        ]
        try:
            chat = await asyncio.wait_for(
                self.llm.chat(messages, temperature=0.0, max_tokens=1200, tools=self.registry.specs()),
                timeout=120,
            )
        except (TimeoutError, Exception) as exc:
            return False, [{"tool": "", "passed": False, "reason": f"核验调用失败：{type(exc).__name__}"}]

        if not chat.tool_calls:
            return False, [{"tool": "", "passed": False, "reason": "最终核验未产生客观证据"}]

        request = chat.tool_calls[0]
        result = await self.registry.execute(self.ctx, request.name, request.arguments)
        self.controller.tool_calls += 1
        exit_code = getattr(result, "exit_code", None)
        passed = result.ok and (exit_code in (None, 0))

        evidence.append(
            {
                "tool": request.name,
                "arguments": _trim(request.arguments),
                "ok": result.ok,
                "exit_code": exit_code,
                "summary": result.summary,
                "passed": passed,
                "final": True,
            }
        )
        self.controller.emit(
            "verification",
            message=f"最终核验{'通过' if passed else '未通过'}",
            data=evidence[-1],
        )
        return passed, evidence

    async def _final_report(
        self, plan: ExecutionPlan, state: RunState, evidence: list[dict[str, Any]]
    ) -> str:
        """Ask the model for a summary, but never let it change the verdict."""
        lines = [
            f"状态：{state.value}",
            f"步骤：{plan.done_steps}/{len(plan.steps)} 完成，{plan.failed_steps} 失败",
            f"工具调用：{self.controller.tool_calls} 次，重试 {self.controller.retries} 次",
            f"耗时：{self.controller.elapsed_seconds:.0f}s",
        ]
        steps_text = "\n".join(
            f"- [{s.status.value}] {s.description}" + (f" — {s.error[:120]}" if s.error else "")
            for s in plan.steps
        )
        system = (
            "请用中文向用户汇报这次任务的执行结果，控制在 300 字以内。\n"
            "必须包含：做了什么、当前状态、验证结果。\n"
            "禁止使用「应该」「理论上」这类词；不确定就说不确定。\n"
            "客观事实：\n" + "\n".join(lines) + "\n\n步骤明细：\n" + steps_text
        )
        try:
            chat = await asyncio.wait_for(
                self.llm.chat(
                    [
                        ChatMessage(role="system", content=system),
                        ChatMessage(role="user", content=f"目标：{plan.goal}"),
                    ],
                    temperature=0.3,
                    max_tokens=800,
                ),
                timeout=120,
            )
            body = (chat.content or "").strip()
        except (TimeoutError, Exception):
            body = ""

        header = "\n".join(lines) + "\n\n"
        return header + (body or "（模型未生成总结，以上为客观执行数据。）")

    # ---------------------------------------------------------------- helpers --
    def _result(
        self,
        state: RunState,
        started: float,
        plan: ExecutionPlan | None = None,
        snapshot_id: str = "",
        evidence: list[dict[str, Any]] | None = None,
        *,
        answer: str = "",
        verified: bool = False,
        error: str = "",
    ) -> RunResult:
        self.controller.finished_at = time.monotonic()
        self.controller.files_modified = self.limits.files_modified
        return RunResult(
            run_id=self.controller.run_id,
            state=state,
            plan=plan,
            answer=answer,
            steps_used=plan.done_steps if plan else 0,
            tool_calls=self.controller.tool_calls,
            files_modified=self.limits.files_modified,
            retries=self.controller.retries,
            elapsed_seconds=round(time.monotonic() - started, 1),
            snapshot_id=snapshot_id,
            verified=verified,
            verification_evidence=evidence or [],
            error=error,
        )


def plan_goal(plan: ExecutionPlan | None) -> str:
    return plan.goal if plan else "（未知目标）"


def _trim(value: Any, limit: int = 400) -> Any:
    """Keep event payloads small enough for a live UI stream."""
    if value is None:
        return None
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, dict):
        return {k: _trim(v, limit // 2) for k, v in list(value.items())[:12]}
    if isinstance(value, list):
        return [_trim(v, limit // 2) for v in value[:12]]
    return value


__all__ = ["EVIDENCE_TOOLS", "MAX_TOOLS_PER_STEP", "AutonomousAgent", "RunResult"]

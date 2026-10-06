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
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ai.agent.constraints import (
    PreferenceResolution,
    TaskConstraints,
    derive_constraints,
    load_preferences,
    resolve_preferences,
)
from app.ai.agent.control import RunCancelledError, RunController
from app.ai.agent.plan import ExecutionPlan, PlanStep, RunState, StepStatus
from app.ai.agent.tools import ToolContext
from app.ai.prompts.registry import get_prompt
from app.ai.providers.budget import REASONING_TOKENS, reasoning_budget
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

#: A single step may never consume more than this fraction of the run's whole
#: tool-call budget.
#:
#: The per-attempt ceiling above is not enough on its own: ``MAX_TOOLS_PER_STEP``
#: (12) × ``max_retries + 1`` (4) is 48 calls for one step, so two unlucky steps
#: can spend the entire 100-call run budget between them. That is precisely what
#: happened — the third attempt burned 105 calls in two steps and ended in
#: ``LIMIT_REACHED`` with no recovery ever taking effect. A step that cannot
#: finish inside its share is a step that needs a different approach, not more
#: of the same one.
STEP_BUDGET_DIVISOR = 3

#: Signature used when an attempt produced no tool call at all.
EMPTY_ATTEMPT_SIGNATURE = "no-tools"

#: Completion cap for a call to the model. See `app.ai.providers.budget` for why this
#: is generous, why a small value is dangerous rather than merely tight, and the two
#: silent failures a small value has already caused in this codebase.
MIN_REASONING_TOKENS = REASONING_TOKENS


def _completion_budget() -> int:
    return reasoning_budget()


#: The provider's code for "the thinking used the whole completion budget".
#:
#: It is a real, actionable signal rather than a generic failure: the answer is not
#: "give up", it is "ask again with more room". Treating it as terminal is how the
#: recovery call silently turned into "no new strategy" on long prompts.
_REASONING_EXHAUSTED = "ai_reasoning_budget_exhausted"


async def _chat(
    llm: Any,
    messages: list[ChatMessage],
    *,
    temperature: float,
    tools: list[Any] | None = None,
    timeout: float = 180,
) -> Any:
    """Call the model, giving the reasoning budget one honest second chance.

    A reasoning model that spends its whole budget thinking returns *nothing* — no
    truncated answer, an exception. The recovery prompt is the longest in the loop
    (it carries the whole attempt history), so it is exactly where the budget runs
    out first. Retrying once with double the room is what the provider's own message
    asks for; retrying with the same budget would be the same failure again.
    """
    budget = _completion_budget()

    def call(size: int) -> Any:
        kwargs: dict[str, Any] = {"temperature": temperature, "max_tokens": size}
        if tools is not None:
            kwargs["tools"] = tools
        return llm.chat(messages, **kwargs)

    try:
        return await asyncio.wait_for(call(budget), timeout=timeout)
    except Exception as exc:
        if getattr(exc, "code", "") != _REASONING_EXHAUSTED:
            raise
        # Straight to the ceiling rather than doubling: the failure mode is the
        # model thinking itself to a standstill on a genuinely hard question ("find
        # a different approach" when there may not be one), and a 2× bump was
        # measured to still be short. The second attempt also gets more wall clock,
        # because thinking for longer takes longer.
        wider = min(settings.ai_max_tokens, budget * 4)
        if wider <= budget:
            raise
        logger.warning("reasoning_budget_raised", extra={"from": budget, "to": wider})
        return await asyncio.wait_for(call(wider), timeout=timeout * 2)

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


def attempt_signature(observations: list[dict[str, Any]]) -> str:
    """A stable fingerprint of *what an attempt did*: the tools and their arguments.

    Two attempts with the same signature did the same thing. Comparing them is how
    "it retried" is distinguished from "it retried the same way" — and the second
    of those is not a retry at all, it is a loop.

    The arguments are hashed rather than stored verbatim because a
    ``document_write`` call carries an entire deck in its ``sections``; keeping it
    whole would put megabytes into the event stream. The digest answers "same or
    different", which is the only question being asked here.
    """
    if not observations:
        return EMPTY_ATTEMPT_SIGNATURE
    parts: list[str] = []
    for observation in observations:
        name = str(observation.get("tool") or "")
        if not name:
            continue
        payload = json.dumps(
            observation.get("arguments"), ensure_ascii=False, sort_keys=True, default=str
        )
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
        parts.append(f"{name}:{digest}")
    if not parts:
        return EMPTY_ATTEMPT_SIGNATURE
    return "|".join(parts)


def render_attempt_history(step: PlanStep, *, limit: int = 3) -> str:
    """The block injected into a retry, so the next attempt is not the last one.

    This is the fix for the root cause: ``_recover`` always returned an analysis and
    the caller always threw it away, so every retry ran with an identical prompt and
    therefore reached an identical decision. The analysis only becomes useful when
    it is *read by the thing that is about to try again*.

    Only the most recent attempts are rendered, and each analysis is cut short. The
    block goes into every prompt of the attempt, and it is also what `_recover`
    itself is given — so an unbounded history would make the prompt grow with each
    retry until the model's thinking budget ran out, which is a failure mode this
    loop has already produced once.
    """
    if not step.attempts:
        return ""
    lines = ["你之前已经尝试过以下做法，它们都失败了："]
    for record in step.attempts[-limit:]:
        number = record.get("attempt")
        tools = "、".join(record.get("tools_used") or []) or "（没有调用任何工具）"
        error = str(record.get("error") or "未通过验证")[:160]
        lines.append(f"  第 {number} 次：调用 {tools} → 失败：{error}")
        analysis = str(record.get("analysis") or "").strip()
        if analysis:
            lines.append(f"           上次的分析：{analysis[:220]}")
    lines.append(
        "不要重复上面这些做法。必须换一个明显不同的方式：换一个工具，"
        "或者对同一个工具换一组参数。如果你认为没有别的做法，就直接说明做不到，不要原样重试。"
    )
    # Hard ceiling on the block. It is injected into every prompt of the attempt
    # *and* into the recovery call itself, so an unbounded history makes the prompt
    # grow with every retry — and the thing it crowds out is the model's thinking
    # budget, which is the failure this loop has already produced twice.
    return "\n".join(lines)[:1500]


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


def trim_history(messages: list[ChatMessage], limit: int) -> list[ChatMessage]:
    """Keep the last `limit` messages, without breaking tool-call pairing.

    The OpenAI contract requires two things that a naive slice violates:

      * a `role="tool"` message must be preceded by an assistant message whose
        `tool_calls` include its `tool_call_id`;
      * an assistant message with `tool_calls` must be followed by tool results.

    Slicing a long conversation at an arbitrary index breaks whichever of the two
    the boundary lands on, and the provider rejects the whole request with HTTP 400.
    That is exactly what happened: the agent got two steps into a run, the history
    was cut mid-pair, and every subsequent call failed with

        Messages with role 'tool' must be a response to a preceding message with
        'tool_calls'

    So the boundary is repaired rather than assumed: leading tool results are
    dropped (their request is gone, so they carry no context anyway), and a
    trailing assistant tool-call request with no results is dropped too.
    """
    if limit <= 0:
        return []
    window = list(messages[-limit:])

    # Drop leading tool results whose requesting assistant was cut off.
    start = 0
    while start < len(window) and str(getattr(window[start], "role", "")) == "tool":
        start += 1
    window = window[start:]

    # Drop a trailing assistant request that has no results in the window.
    while window:
        last = window[-1]
        if str(getattr(last, "role", "")) == "assistant" and getattr(last, "tool_calls", None):
            window = window[:-1]
            continue
        break

    return window


def repair_tool_pairing(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Return the messages with tool-call pairing made valid.

    The provider requires two invariants and rejects the whole request with HTTP 400
    when either is broken:

      * every `role="tool"` message follows an assistant message whose `tool_calls`
        contain its `tool_call_id`;
      * every assistant `tool_calls` entry has a following result.

    The agent's step loop builds its message list incrementally, and there are
    several ways to end up invalid: an early `break` after a tool ran but before the
    assistant message was recorded, a retry that re-enters with a half-built list, a
    tool that produced no result. Rather than audit every path, the list is repaired
    before it is sent — cheap, and it makes the invariant a property of the request
    rather than a property of the caller being careful.

    Orphaned tool results are dropped (their request is gone, so they carry no
    context), and unanswered requests are dropped too.
    """
    repaired: list[ChatMessage] = []
    pending_ids: set[str] = set()

    for message in messages:
        role = str(getattr(message, "role", ""))
        if role == "tool":
            # Keep it only if the assistant that asked for it is still in the list.
            if getattr(message, "tool_call_id", None) in pending_ids:
                pending_ids.discard(message.tool_call_id)
                repaired.append(message)
            continue

        if role == "assistant":
            calls = getattr(message, "tool_calls", None) or []
            # A previous assistant's unanswered requests are dropped when the next
            # assistant message arrives: the model moved on without their results.
            if pending_ids:
                repaired = [
                    m
                    for m in repaired
                    if not (str(getattr(m, "role", "")) == "assistant" and getattr(m, "tool_calls", None))
                ]
                pending_ids.clear()
            if calls:
                pending_ids = {
                    str(call.get("id")) for call in calls if isinstance(call, dict) and call.get("id")
                }
            repaired.append(message)
            continue

        repaired.append(message)

    # A trailing assistant request with no results is invalid on its own.
    while repaired:
        last = repaired[-1]
        if str(getattr(last, "role", "")) == "assistant" and getattr(last, "tool_calls", None):
            repaired.pop()
            continue
        break

    return repaired


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
        deliverable_kind: str = "",
    ) -> None:
        # What the user asked to be produced, when they said so. Empty means "any".
        #
        # The agent's default is to satisfy the goal with the smallest artifact that
        # passes, which is usually a .md. For a request that named a format, that is
        # not the same thing as complying — so the kind is carried into the planner
        # and checked against what actually came out.
        self.deliverable_kind = deliverable_kind.strip().lower()
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

        #: What the task itself requires, and what the user's preferences turned
        #: into once those requirements were taken into account. Filled in by
        #: `_resolve_requirements` before the plan is written, because the plan is
        #: the first place the difference between the two shows up.
        self.constraints = TaskConstraints()
        self.preferences = PreferenceResolution()

    async def _resolve_requirements(self, goal: str) -> None:
        """Work out the hard constraints and what each preference becomes.

        The order matters and is the whole point: constraints are derived from the
        task *first*, then preferences are resolved *against* them. Resolving
        preferences first — which is what a single blended prompt does — is how
        "页数更少" ended up beating "15 分钟".
        """
        self.constraints = derive_constraints(goal, deliverable_kind=self.deliverable_kind)

        loaded: list[Any] = []
        if self.session is not None and self.owner_id is not None:
            loaded = await load_preferences(self.session, self.owner_id)

        workspace = Path(self.working_directory) if self.working_directory else Path.cwd()
        self.preferences = resolve_preferences(loaded, self.constraints, workspace=workspace)

        if self.constraints.has_slide_range:
            self.controller.emit(
                "state",
                message=(
                    f"任务硬约束：{self.constraints.duration_minutes:g} 分钟 → "
                    f"{self.constraints.slide_min}~{self.constraints.slide_max} 页"
                ),
                data=self.constraints.as_dict(),
            )

        if self.preferences.outcomes:
            self.controller.emit(
                "state",
                message=f"已解析 {len(self.preferences.outcomes)} 条长期偏好",
                data=self.preferences.as_dict(),
            )

        # A preference that cannot be executed is reported, not dropped. Silence
        # here is the failure mode the whole feature exists to avoid: the user
        # walks away believing it took effect.
        for outcome in self.preferences.unsatisfiable:
            self.controller.emit(
                "error",
                message=f"偏好无法满足：「{outcome.text}」—— {outcome.detail}",
                data=outcome.as_dict(),
            )

    def _requirements_block(self) -> str:
        """The constraint and preference text injected into planner and executor."""
        parts = []
        if self.constraints.has_slide_range or self.constraints.duration_minutes:
            parts.append(self.constraints.render())
        rendered = self.preferences.render()
        if rendered:
            parts.append(rendered)
        return "\n\n".join(parts)

    async def _personal_context(self, *, purpose: str = "agent") -> str:
        """Assemble the personal block and record what was injected.

        Returns an empty string when there is nothing to say — a new user with no
        profile is a normal state, and the agent runs exactly as before.

        A failure here must not stop the run: personalisation is an enhancement,
        and an enhancement that can break the core task is a liability.
        """
        try:
            from app.ai.memory import ContextAssembler

            assembler = ContextAssembler(self.session, user_id=self.owner_id)
            assembled = await assembler.assemble()
            if assembled.is_empty:
                return ""
            await assembler.persist(assembled, run_id=self.controller.run_id, purpose=purpose)
            return assembled.render()
        except Exception as exc:
            logger.warning(
                "personal_context_failed",
                extra={"purpose": purpose, "error": f"{type(exc).__name__}: {exc}"},
            )
            return ""

    def _permissions(self) -> Any:
        from app.ai.tools.permissions import PermissionManager

        return PermissionManager(self.policy)

    # ==================================================================== plan ==
    async def _make_plan(self, goal: str, context: str) -> ExecutionPlan:
        """Ask the model for a plan, and refuse to proceed without a usable one."""
        self.controller.set_state(RunState.PLANNING, "正在制定执行计划")
        prompt = await get_prompt(self.session, "agent_planner")
        catalogue = ", ".join(self.registry.names())

        # The personal block goes into the planner as well as the executor, so
        # the plan itself reflects who is asking: language, depth, whether to
        # confirm first, which working directory to prefer.
        personal = await self._personal_context(purpose="planner")
        combined = "\n\n".join(part for part in (personal, context) if part.strip()) or "（无）"

        system = prompt.render(tools=catalogue, goal=goal, context=combined)
        requirements = self._requirements_block()
        if requirements:
            # Stated as a hard requirement, ahead of the goal, so the plan is built
            # around it rather than patched to fit it afterwards.
            system += (
                "\n\n"
                + requirements
                + "\n\n【规划时必须遵守】\n"
                "1. 硬约束是任务本身的要求，优先于任何长期偏好。\n"
                "2. 长期偏好只能在硬约束范围内起作用；被标为 bounded 的偏好要按上面的结论执行，"
                "不要自行放大或缩小。\n"
                "3. 被标为 unsatisfiable 的偏好这次做不到，在最终汇报里必须明确说出来。\n"
                "4. success_criteria 里必须包含一条能验证页数/行数/字数的标准。"
            )
        if self.deliverable_kind and self.deliverable_kind != "mixed":
            # Stated as a hard requirement, not a preference.
            #
            # Without it the planner reaches for the smallest thing that satisfies
            # the goal — which for "做一份 PPT" is a markdown outline. The file
            # exists, the step verifies, and the user gets something they cannot
            # present. Naming the tool removes the choice.
            system += (
                "\n\n【硬性要求】用户明确要求产出格式为 "
                f"{self.deliverable_kind}。"
                "因此计划中必须包含一步，调用 document_write 生成该格式的文件，"
                f"format={self.deliverable_kind}。"
                "不允许用 .md / .txt 或纯文本代替；用 file_write 写一个同名文本文件不算完成。"
                "最后一步必须回读该文件并核对页数、大小或行列数。"
            )
            if self.deliverable_kind == "pptx" and self.constraints.has_slide_range:
                target = self.preferences.target_slides or self.constraints.slide_min
                chart_rule = (
                    " 每个内容页都要带一个 chart（categories + series 结构），"
                    "因为用户偏好图表更多。"
                    if self.preferences.charts_per_slide
                    else ""
                )
                system += (
                    f" 调用 document_write 时，sections 必须包含标题页加至少 "
                    f"{target - 1} 个内容页（合计 {target} 页），"
                    f"并且必须传入 min_slides={self.constraints.slide_min}、"
                    f"max_slides={self.constraints.slide_max}，"
                    "由工具在写入时核对页数。"
                    f"{chart_rule}"
                )
            elif self.deliverable_kind == "docx":
                # A document whose headings are all at the same level has no
                # hierarchy, whatever it looks like. `level` is a field of the
                # section, so the plan has to say what the levels are — otherwise
                # every chapter comes out as H1 and the result is a flat list.
                system += (
                    " 调用 document_write 时，sections 的每一项都要给出 level："
                    "章用 level=1，节用 level=2，必要时小节用 level=3；"
                    "整篇至少要出现两个不同的 level，形成真正的层级。"
                )

        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=f"目标：{goal}"),
        ]

        last_error = ""
        for attempt in range(1, 3):
            # Budgeted generously on purpose.
            #
            # The default model is a reasoning model: it spends tokens thinking
            # before it emits the JSON. At 3,000 the plan came back truncated and
            # parsed as zero steps — which surfaced as "无法生成可执行的计划"
            # rather than as anything resembling a token limit.
            chat = await self.llm.chat(
                messages,
                temperature=0.1,
                max_tokens=min(settings.ai_max_tokens, 8000),
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
        personal = await self._personal_context(purpose="executor")
        history = render_attempt_history(step)
        requirements = self._requirements_block()
        system = (
            "你是运行在用户 Windows 电脑上的私人 AI Agent。\n"
            f"总目标：{plan.goal}\n"
            f"当前步骤（第 {step.id} 步）：{step.description}\n"
            f"该步骤的验收方式：{step.verification or '由你调用工具确认结果'}\n\n"
            + (f"{personal}\n\n" if personal else "")
            + (f"{requirements}\n\n" if requirements else "")
            + (f"{history}\n\n" if history else "")
            + "要求：\n"
            "1. 只处理当前这一步，不要跳到后面的步骤。\n"
            "2. 需要操作时调用工具，一次一个。\n"
            "3. 看到工具结果后再决定下一步。\n"
            "4. 这一步完成后，不要再调用工具，直接用一句话总结结果。\n"
            "5. 工具被拒绝时如实说明，不要试图绕过权限。\n"
            f"6. 可用工具：{', '.join(self.registry.names())}"
        )

        diagnostic = is_diagnostic_step(step)

        messages: list[ChatMessage] = [ChatMessage(role="system", content=system)]
        # Bounded, and repaired at the boundary — see `trim_history`.
        messages.extend(trim_history(self._tool_messages, 6))
        messages.append(ChatMessage(role="user", content=f"请执行第 {step.id} 步：{step.description}"))

        observations: list[dict[str, Any]] = []
        summary = ""

        for _ in range(MAX_TOOLS_PER_STEP):
            await self.controller.checkpoint()

            # Repaired before it is sent: the invariant is enforced here rather
            # than assumed of every path that built the list.
            chat = await _chat(
                self.llm,
                repair_tool_pairing(messages),
                temperature=0.0,
                tools=self.registry.specs(),
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
                # Kept so "the second attempt used a different tool or different
                # arguments" is answerable from the record rather than re-run.
                "arguments": _trim(request.arguments),
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
            chat = await _chat(
                self.llm,
                repair_tool_pairing(messages),
                temperature=0.0,
                tools=self.registry.specs(),
                timeout=120,
            )
        except Exception as exc:
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

        # Some tools succeed at answering "no". `file_exists` returns ok=True with
        # `exists: false` — the call worked, the file is not there. Treating ok as a
        # pass let the agent "verify" a step that had produced nothing: it called
        # file_exists, got a truthful "missing", and the step was marked done.
        #
        # So for the tools whose answer *is* the evidence, the answer is read.
        answer = result.data if isinstance(result.data, dict) else {}
        answered_no = False
        if request.name == "file_exists":
            answered_no = not bool(answer.get("exists"))
        elif request.name in ("file_read", "document_read") and not result.ok:
            answered_no = True

        if answered_no:
            passed = False
        elif diagnostic:
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
            # Recorded so "it said done but nothing exists" is answerable from the
            # trace rather than requiring a re-run.
            "answered_no": answered_no,
        }
        self.controller.emit(
            "verification",
            message=f"验证{'通过' if passed else '未通过'}：{request.name}",
            data=evidence,
        )
        return passed, evidence

    # ================================================================= recover ==
    async def _recover(self, step: PlanStep, observations: list[dict[str, Any]]) -> str:
        """Ask the model what to do about a failed step.

        Returns the analysis, or an empty string when no analysis could be
        produced. The empty string is not a placeholder for "carry on": the caller
        treats it as "there is no new strategy", and stops the step rather than
        running the same attempt again. The previous version substituted a sentence
        like ``（分析失败：TimeoutError）`` and retried anyway, which meant the
        failure to analyse was itself silently converted into another blind retry.
        """
        self.controller.set_state(RunState.RECOVERING, f"第 {step.id} 步失败，正在分析")
        history = render_attempt_history(step)
        system = (
            "你正在修复一个失败的步骤。\n"
            f"目标：{plan_goal(self.controller.plan)}\n"
            f"失败的步骤：{step.description}\n"
            f"该步骤的验收方式：{step.verification or '（未说明）'}\n"
            f"错误：{step.error or step.result}\n\n"
            + (f"{history}\n\n" if history else "")
            + "请用两三句话说明：根因是什么，下一次尝试具体改用什么工具、什么参数。"
            "必须给出一个与之前不同的具体做法，不要只说「再试一次」。"
        )
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(
                role="user",
                content=json.dumps(observations[-4:], ensure_ascii=False, default=str)[:3000],
            ),
        ]
        try:
            chat = await _chat(self.llm, messages, temperature=0.2, timeout=90)
            analysis = (chat.content or "").strip()
        except Exception as exc:
            # No blanket `except: pass`, and no invented analysis either. The
            # caller is told, in the event stream, that recovery could not run —
            # because a recovery that silently did nothing looks identical to one
            # that worked, right up until the budget is gone.
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning("recover_failed", extra={"step": step.id, "error": reason})
            self.controller.emit(
                "error",
                message=f"第 {step.id} 步的恢复分析失败（{type(exc).__name__}），没有新的策略可用",
                data={"step": step.id, "error": reason},
            )
            return ""

        self.controller.emit("state", message=analysis[:400] or "恢复分析没有给出新的做法")
        return analysis

    # ==================================================================== run ==
    async def run(self, goal: str, *, context: str = "") -> RunResult:
        started = time.monotonic()
        self.controller.emit("state", message="开始执行", state=RunState.PLANNING.value)

        # Constraints and preferences are resolved before the plan is written, so
        # the plan is built around the requirements rather than adjusted to them.
        await self._resolve_requirements(goal)

        try:
            plan = await self._make_plan(goal, context)
        except RunCancelledError:
            return self._result(RunState.CANCELLED, started, error="已取消")
        except Exception as exc:
            logger.error("planning_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
            self.controller.set_state(RunState.FAILED, f"制定计划失败：{exc}")
            return self._result(RunState.FAILED, started, error=str(exc))

        # Recorded on the plan so the run can be asked afterwards what it was
        # required to do and what it decided to do about the user's preferences.
        plan.hard_constraints = self.constraints.as_dict()
        plan.preference_outcomes = [o.as_dict() for o in self.preferences.outcomes]

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

            # What this step has spent, so one step cannot eat the whole run.
            #
            # Counted from the controller rather than from `ResourceLimits`: the
            # limits counter is only advanced by the tool executor, so business
            # tools (which do not go through it) would be invisible here and a
            # step could spend more than its share while the guard saw nothing.
            step_budget_start = self.controller.tool_calls
            step_ceiling = max(
                MAX_TOOLS_PER_STEP, self.limits.max_tool_calls // STEP_BUDGET_DIVISOR
            )

            for attempt in range(1, self.policy.max_retries + 2):
                spent = self.controller.tool_calls - step_budget_start
                if spent >= step_ceiling:
                    # Stop the step, keep the run: the remaining steps may still be
                    # able to finish, and "one step is stuck" is not the same fact
                    # as "the run is out of budget".
                    step.status = StepStatus.FAILED
                    step.error = (
                        f"该步骤已用掉 {spent} 次工具调用，达到单步上限 {step_ceiling}，停止该步骤"
                    )
                    self.controller.emit(
                        "error",
                        message=f"第 {step.id} 步用尽单步预算（{step_ceiling} 次工具调用），停止该步骤",
                        data=step.as_dict(),
                    )
                    break

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

                signature = attempt_signature(observations)
                record: dict[str, Any] = {
                    "attempt": attempt,
                    "tools_used": [str(o.get("tool")) for o in observations if o.get("tool")],
                    "arguments": [o.get("arguments") for o in observations if o.get("arguments")],
                    "signature": signature,
                    "error": (step.error or "")[:400],
                    "analysis": "",
                }
                previous = step.attempts[-1] if step.attempts else None
                record["same_as_previous"] = bool(
                    previous and previous.get("signature") == signature
                )
                step.attempts.append(record)
                self.controller.emit(
                    "step",
                    message=(
                        f"第 {step.id} 步第 {attempt} 次尝试："
                        + ("、".join(record["tools_used"]) or "没有调用工具")
                        + ("（与上一次完全相同）" if record["same_as_previous"] else "（与上一次不同）")
                    ),
                    data={"attempt": record, "step": step.id},
                )

                if record["same_as_previous"]:
                    # The same tools with the same arguments, twice. Running it a
                    # third time is not a retry, it is a loop, and it is the exact
                    # shape that spent 105 tool calls in two steps. Stop here.
                    message = (
                        f"第 {step.id} 步的第 {attempt} 次尝试与第 {attempt - 1} 次使用了完全相同的"
                        "工具和参数，判定为无效重试，停止该步骤"
                    )
                    step.error = f"{step.error}｜无效重试：与上一次完全相同".strip("｜")
                    self.controller.emit("error", message=message, data=step.as_dict())
                    break

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
                analysis = await self._recover(step, observations)
                if not analysis:
                    # No new strategy means another attempt would be the same
                    # attempt. Reporting that is the honest outcome; retrying
                    # anyway is how the budget disappeared without anything
                    # changing.
                    self.controller.emit(
                        "error",
                        message=f"第 {step.id} 步没有获得新的做法，停止重试而不是重复同一次尝试",
                        data=step.as_dict(),
                    )
                    break
                record["analysis"] = analysis
                # Counted here rather than on every failure: a *retry* is an attempt
                # that actually happens. Incrementing on the final failure too made
                # `retry_count` read `max_retries + 1`, so a step that retried three
                # times claimed four retries and the number no longer matched the
                # budget it was supposed to be bounded by.
                self.controller.retries += 1
                step.retry_count += 1
                step.status = StepStatus.RUNNING

        # ------------------------------------------------------------ final --
        self.controller.set_state(RunState.VERIFYING, "正在核验成功标准")
        verified, final_evidence = await self._final_verify(plan)
        all_evidence.extend(final_evidence)

        # A run with a failed step is not a success, whatever the criteria check
        # says. The previous version let `verified` win, so a plan with a failed
        # step could still end in COMPLETED — the same inconsistency as a FAILED
        # run that leaves a `verified=True` file behind: the verdict and the work
        # disagree, and the verdict is the one that gets read.
        failed = [s for s in plan.steps if s.status is StepStatus.FAILED]
        if verified and not failed:
            self.controller.set_state(RunState.COMPLETED, "成功标准已满足")
            state = RunState.COMPLETED
        elif failed:
            self.controller.set_state(
                RunState.FAILED,
                f"有 {len(failed)} 个步骤失败（{'、'.join(s.id for s in failed[:3])}）"
                + ("，成功标准核验通过" if verified else "，成功标准未通过核验"),
            )
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
                self.llm.chat(
                    repair_tool_pairing(messages),
                    temperature=0.0,
                    max_tokens=_completion_budget(),
                    tools=self.registry.specs(),
                ),
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

        # The same rule `_verify_step` already applies, and for the same reason:
        # some tools succeed at answering "no". `file_exists` returning `ok=True`
        # with `exists: false` is a truthful report that the artifact is missing —
        # reading only `ok` here made the final gate pass on evidence that proved
        # the opposite of what it was checking.
        answer = result.data if isinstance(result.data, dict) else {}
        answered_no = False
        if request.name == "file_exists":
            answered_no = not bool(answer.get("exists"))
        elif request.name in ("file_read", "document_read") and not result.ok:
            answered_no = True

        passed = result.ok and (exit_code in (None, 0)) and not answered_no

        evidence.append(
            {
                "tool": request.name,
                "arguments": _trim(request.arguments),
                "ok": result.ok,
                "exit_code": exit_code,
                "summary": result.summary,
                "passed": passed,
                "answered_no": answered_no,
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
                    max_tokens=_completion_budget(),
                ),
                timeout=120,
            )
            body = (chat.content or "").strip()
        except (TimeoutError, Exception):
            body = ""

        header = "\n".join(lines) + "\n\n"
        body = body or "（模型未生成总结，以上为客观执行数据。）"

        # The trade-off note is generated here, not asked of the model. The
        # question "did it explain why the deck is this long?" has to be answerable
        # from the output, and a model that simply did not mention it would make
        # the honest answer "no" — which is exactly what the last round produced.
        report = self.preferences.report()
        if report:
            body = f"{body}\n\n{report}"
        return header + body

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


__all__ = [
    "EVIDENCE_TOOLS",
    "MAX_TOOLS_PER_STEP",
    "STEP_BUDGET_DIVISOR",
    "AutonomousAgent",
    "RunResult",
    "attempt_signature",
    "is_diagnostic_step",
    "render_attempt_history",
    "repair_tool_pairing",
    "trim_history",
]

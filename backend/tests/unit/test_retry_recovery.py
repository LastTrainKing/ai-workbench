"""The retry mechanism, tested as a mechanism.

The bug these tests exist for was not "the retry limit was too small". It was that
`_recover` returned an analysis which the caller threw away, so every retry ran with
an identical prompt, reached an identical decision, and failed identically — until
the run's whole tool budget was gone. Two steps burned 105 tool calls.

So these tests assert the properties that make a retry a retry:

* the recovery analysis reaches the *next* attempt's prompt;
* an attempt that repeats the previous attempt exactly is refused, not run again;
* a recovery that produced nothing stops the step instead of retrying blindly;
* one step cannot spend the whole run's budget.
"""

from __future__ import annotations

import uuid

import pytest

from app.ai.agent import autonomous as module
from app.ai.agent.autonomous import (
    STEP_BUDGET_DIVISOR,
    AutonomousAgent,
    attempt_signature,
    render_attempt_history,
)
from app.ai.agent.control import RunController
from app.ai.agent.plan import ExecutionPlan, PlanStep, RunState, StepStatus
from app.ai.tools.permissions import AgentPolicy
from app.ai.types import ChatResult, ToolCallRequest, ToolResult

PLAN_JSON = """{
  "goal": "生成一份汇报文件",
  "risk_level": "low",
  "estimated_duration_seconds": 30,
  "estimated_tools": ["file_write"],
  "success_criteria": ["文件真实存在"],
  "steps": [
    {"id": "step-1", "description": "生成汇报文件", "verification": "file_exists 返回 exists=true", "tool": "file_write"}
  ]
}"""


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #
class FakeLLM:
    """A model whose answers are decided by the prompt, not sampled.

    Deterministic on purpose: the loop's behaviour under a *given* sequence of
    decisions is what is under test, and a real model would make the test assert
    something different every time it ran.
    """

    def __init__(
        self,
        *,
        executor_plan=None,
        recover="改用 document_write 并带上 sections",
        fail_recover=False,
        always_call_tools=False,
    ):
        self.executor_plan = executor_plan or (
            lambda turn: ("file_write", {"path": "notes/a.md", "content": "x"})
        )
        self.recover = recover
        self.fail_recover = fail_recover
        self.always_call_tools = always_call_tools
        self.prompts: list[list[object]] = []
        self.executor_turns = 0

    @staticmethod
    def _system(messages) -> str:
        first = messages[0]
        return str(getattr(first, "content", "") or "")

    @staticmethod
    def _tools_since_last_user(messages) -> int:
        """Tool results produced *in the current step attempt*.

        The history carried over from the previous attempt also contains tool
        messages, so counting all of them would make every retry look like it had
        already acted. Counting only those after the last user turn is what
        distinguishes "this attempt has not acted yet" from "it has".
        """
        last_user = -1
        for index, message in enumerate(messages):
            if str(getattr(message, "role", "")) == "user":
                last_user = index
        return sum(
            1
            for message in messages[last_user + 1 :]
            if str(getattr(message, "role", "")) == "tool"
        )

    def _tool(self, name: str, arguments: dict) -> ChatResult:
        return ChatResult(
            content="",
            tool_calls=[
                ToolCallRequest(id=f"call-{len(self.prompts)}", name=name, arguments=arguments)
            ],
        )

    async def chat(self, messages, **kwargs) -> ChatResult:
        self.prompts.append(list(messages))
        system = self._system(messages)

        if "任务规划器" in system:
            return ChatResult(content=PLAN_JSON)
        if "最终验收器" in system:
            return self._tool("file_exists", {"path": "notes/a.md"})
        if "你是验证器" in system:
            # A truthful "no": the call works, the file is not there.
            return self._tool("file_exists", {"path": "notes/a.md"})
        if "你正在修复" in system:
            if self.fail_recover:
                raise RuntimeError("provider unreachable")
            return ChatResult(content=self.recover)
        if "请用中文向用户汇报" in system:
            return ChatResult(content="测试总结")

        # The executor.
        fresh = self._tools_since_last_user(messages) == 0
        if fresh:
            self.executor_turns += 1
        if fresh or self.always_call_tools:
            name, arguments = self.executor_plan(self.executor_turns)
            return self._tool(name, arguments)
        return ChatResult(content="这一步做完了")


class FakeRegistry:
    """Reports every tool as successful but answers "no" where it matters."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def names(self) -> list[str]:
        return ["file_write", "file_exists", "document_write"]

    def specs(self) -> list[object]:
        return []

    async def execute(self, ctx, name, arguments) -> ToolResult:
        self.calls.append((name, arguments))
        if name == "file_exists":
            return ToolResult(ok=True, data={"exists": False}, summary="file_exists → no")
        return ToolResult(ok=True, data={"path": arguments.get("path")}, summary=f"{name} ok")


def build_agent(*, llm, registry, max_retries=3, max_tool_calls=100, tmp_path=None):
    policy = AgentPolicy.from_settings(
        level=2, max_retries=max_retries, max_tool_calls=max_tool_calls
    )
    controller = RunController(run_id="test-run", owner_id=uuid.uuid4())
    agent = AutonomousAgent(
        llm=llm,
        registry=registry,
        policy=policy,
        controller=controller,
        session=None,
        owner_id=None,
        working_directory=str(tmp_path or "."),
    )
    return agent, controller


@pytest.fixture(autouse=True)
def _builtin_planner(monkeypatch):
    """Serve the planner prompt from the builtin seed instead of the database."""
    from app.ai.prompts.builtin import TEMPLATES_BY_NAME
    from app.ai.prompts.registry import ResolvedPrompt

    async def fake_get_prompt(session, name):
        template = TEMPLATES_BY_NAME[name]
        return ResolvedPrompt(
            name=name,
            version=0,
            content=template.content,
            variables=list(template.variables),
            task=template.task,
            json_output=template.json_output,
            category=template.category,
            source="builtin",
        )

    monkeypatch.setattr(module, "get_prompt", fake_get_prompt)


# --------------------------------------------------------------------------- #
# The primitives
# --------------------------------------------------------------------------- #
def test_signature_is_equal_for_equal_tools_and_arguments():
    a = [{"tool": "file_write", "arguments": {"path": "x", "content": "1"}}]
    b = [{"tool": "file_write", "arguments": {"content": "1", "path": "x"}}]
    assert attempt_signature(a) == attempt_signature(b)


def test_signature_differs_when_the_arguments_differ():
    a = [{"tool": "file_write", "arguments": {"path": "x"}}]
    b = [{"tool": "file_write", "arguments": {"path": "y"}}]
    c = [{"tool": "document_write", "arguments": {"path": "x"}}]
    assert attempt_signature(a) != attempt_signature(b)
    assert attempt_signature(a) != attempt_signature(c)


def test_signature_of_an_attempt_that_called_nothing():
    assert attempt_signature([]) == module.EMPTY_ATTEMPT_SIGNATURE


def test_history_block_names_the_failed_attempts_and_forbids_repeating_them():
    step = PlanStep(
        id="step-1",
        description="生成汇报文件",
        attempts=[
            {
                "attempt": 1,
                "tools_used": ["file_write"],
                "error": "产出格式不是 .pptx",
                "analysis": "应该直接使用 document_write",
            }
        ],
    )
    text = render_attempt_history(step)
    assert "file_write" in text
    assert "产出格式不是 .pptx" in text
    assert "应该直接使用 document_write" in text
    assert "不要重复" in text


def test_history_block_is_empty_before_anything_has_failed():
    assert render_attempt_history(PlanStep(id="s", description="d")) == ""


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_first_attempt_gets_no_history_and_the_retry_gets_the_analysis(tmp_path):
    """The root cause, asserted directly: the analysis reaches the next prompt."""
    llm = FakeLLM(recover="换用 document_write，参数带上 sections")
    registry = FakeRegistry()
    agent, _controller = build_agent(llm=llm, registry=registry, max_retries=1, tmp_path=tmp_path)

    result = await agent.run("生成一份汇报文件")

    executor_prompts = [p for p in llm.prompts if "当前步骤" in FakeLLM._system(p)]
    assert len(executor_prompts) >= 2, "the step must have been attempted more than once"
    # Every call inside one attempt carries the same system prompt, so the first
    # one is attempt 1 and the last one is attempt 2.
    first, second = FakeLLM._system(executor_prompts[0]), FakeLLM._system(executor_prompts[-1])
    assert "你之前已经尝试过" not in first
    assert "你之前已经尝试过" in second
    assert "换用 document_write，参数带上 sections" in second
    assert "file_write" in second

    step = result.plan.steps[0]
    assert step.attempts[0]["analysis"] == "换用 document_write，参数带上 sections"


@pytest.mark.asyncio
async def test_an_identical_retry_is_refused_instead_of_run_again(tmp_path):
    """Same tool, same arguments, twice → stop. This is what burned 105 calls."""
    llm = FakeLLM()  # always file_write with the same path
    registry = FakeRegistry()
    agent, controller = build_agent(llm=llm, registry=registry, max_retries=3, tmp_path=tmp_path)

    result = await agent.run("生成一份汇报文件")

    step = result.plan.steps[0]
    assert len(step.attempts) == 2, f"expected exactly two attempts, got {len(step.attempts)}"
    assert step.attempts[1]["same_as_previous"] is True
    assert "无效重试" in step.error
    # Two attempts at three calls each: one tool call, one closing summary, one
    # verification. Nowhere near the 100-call ceiling.
    assert controller.tool_calls < 20
    assert result.state is not module.RunState.LIMIT_REACHED
    assert any("无效重试" in event.message for event in controller.events if event.kind == "error")


@pytest.mark.asyncio
async def test_a_different_second_attempt_is_allowed(tmp_path):
    """A genuinely different retry is not mistaken for a loop."""
    llm = FakeLLM(
        executor_plan=lambda turn: (
            "file_write",
            {"path": f"notes/attempt-{turn}.md", "content": "x"},
        )
    )
    registry = FakeRegistry()
    agent, _controller = build_agent(llm=llm, registry=registry, max_retries=1, tmp_path=tmp_path)

    result = await agent.run("生成一份汇报文件")

    step = result.plan.steps[0]
    assert len(step.attempts) == 2
    assert step.attempts[1]["same_as_previous"] is False
    assert "无效重试" not in step.error


@pytest.mark.asyncio
async def test_recovery_that_produced_nothing_stops_the_step(tmp_path):
    """No new strategy must not become another blind attempt."""
    llm = FakeLLM(fail_recover=True)
    registry = FakeRegistry()
    agent, controller = build_agent(llm=llm, registry=registry, max_retries=3, tmp_path=tmp_path)

    result = await agent.run("生成一份汇报文件")

    step = result.plan.steps[0]
    assert len(step.attempts) == 1, "a failed recovery must not lead to another attempt"
    assert any(
        "恢复分析失败" in event.message for event in controller.events if event.kind == "error"
    )
    assert result.state is not module.RunState.LIMIT_REACHED


@pytest.mark.asyncio
async def test_one_step_cannot_spend_the_whole_run_budget(tmp_path, monkeypatch):
    """The per-step ceiling holds even when every attempt keeps calling tools."""
    monkeypatch.setattr(module, "MAX_TOOLS_PER_STEP", 3)
    # ceiling = max(MAX_TOOLS_PER_STEP, 12 // 3) = 4
    llm = FakeLLM(
        executor_plan=lambda turn: ("file_write", {"path": f"notes/t{turn}.md", "content": "x"}),
        always_call_tools=True,
    )
    registry = FakeRegistry()
    agent, controller = build_agent(
        llm=llm, registry=registry, max_retries=3, max_tool_calls=12, tmp_path=tmp_path
    )

    result = await agent.run("生成一份汇报文件")

    step = result.plan.steps[0]
    assert "单步上限" in step.error
    assert controller.tool_calls <= 8, f"step spent {controller.tool_calls} calls"
    assert result.state is not module.RunState.LIMIT_REACHED


@pytest.mark.asyncio
async def test_a_successful_step_never_enters_recovery(tmp_path):
    """Scenario A: no failure, no recovery, no history."""

    class PassingVerify(FakeLLM):
        async def chat(self, messages, **kwargs):
            system = self._system(messages)
            if "你是验证器" in system:
                self.prompts.append(list(messages))
                return self._tool("file_exists", {"path": "notes/a.md"})
            return await super().chat(messages, **kwargs)

    llm = PassingVerify()
    registry = FakeRegistry()
    # Make the verifier's answer a "yes" by having the file exist.
    original = registry.execute

    async def execute(ctx, name, arguments):
        if name == "file_exists":
            registry.calls.append((name, arguments))
            return ToolResult(ok=True, data={"exists": True}, summary="file_exists → yes")
        return await original(ctx, name, arguments)

    registry.execute = execute  # type: ignore[method-assign]

    agent, controller = build_agent(llm=llm, registry=registry, max_retries=3, tmp_path=tmp_path)
    result = await agent.run("生成一份汇报文件")

    step = result.plan.steps[0]
    assert step.status is StepStatus.DONE
    assert step.attempts == []
    assert controller.retries == 0
    assert not any(event.kind == "error" for event in controller.events)
    assert result.state is RunState.COMPLETED


def test_budget_divisor_is_at_least_three():
    """Three steps must be able to fit inside the default 100-call run budget."""
    assert STEP_BUDGET_DIVISOR >= 3


def test_plan_serialisation_carries_the_attempt_history():
    step = PlanStep(id="s", description="d", attempts=[{"attempt": 1, "signature": "x"}])
    plan = ExecutionPlan(goal="g", steps=[step], success_criteria=["c"])
    assert plan.as_dict()["steps"][0]["attempts"] == [{"attempt": 1, "signature": "x"}]

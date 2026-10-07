"""The final gate: one wrong guess must not fail a run that did everything.

The observed failure this file pins down, in full:

A five-step run produced a real 35,700-byte, 11-slide deck at
`deliverables/Java基础教程_指引目录.pptx`. Every step passed. The final verifier —
which is asked to call **one** tool for the most critical success criterion — called
`document_read` on `Java基础教程_指引目录.pptx`, dropping the `deliverables/`
prefix. The tool returned `ValidationError: 文件不存在`. `_final_verify` read that
as `answered_no`, returned False on the first and only attempt, and the run was
marked FAILED. `build_executor` then reported PARTIAL and stamped the deliverable
「未校验」.

Three separate things were wrong, and each has a test here:

1. The verifier was told nothing about what the run had produced, so it *had* to
   guess a path. → `_artifact_block` gives it the real list.
2. A tool that could not run was read as a negative answer. → an errored call is
   retried with the failure fed back.
3. "The criteria are not met" and "I could not check the criteria" were the same
   outcome. → `conclusive` separates them, and the second one is not FAILED.

Assertions are written so that a partial fix fails. In particular, retrying must
not become "retry until it passes": a truthful `file_exists: false` still ends the
check on the first attempt, and a run whose criteria are genuinely unmet is still
reported FAILED.
"""

from __future__ import annotations

import uuid

import pytest

from app.ai.agent import autonomous as module
from app.ai.agent.autonomous import AutonomousAgent
from app.ai.agent.control import RunController
from app.ai.agent.plan import ExecutionPlan, PlanStep, RunState
from app.ai.tools.permissions import AgentPolicy
from app.ai.types import ChatResult, ToolCallRequest, ToolResult

PLAN_JSON = """{
  "goal": "生成一份目录 PPT",
  "risk_level": "low",
  "estimated_duration_seconds": 30,
  "estimated_tools": ["document_write"],
  "success_criteria": ["文件真实存在且可以读回"],
  "steps": [
    {"id": "step-1", "description": "写出目录 PPT",
     "verification": "document_write 返回成功", "tool": "document_write"}
  ]
}"""

REAL_PATH = "deliverables/Java基础教程_指引目录.pptx"
GUESSED_PATH = "Java基础教程_指引目录.pptx"


class ScriptedLLM:
    """Answers by prompt, and lets the test dictate the verifier's tool calls."""

    def __init__(self, *, verifier_calls: list[tuple[str, str]]) -> None:
        #: (tool_name, path) for each successive call the final verifier makes.
        self.verifier_calls = list(verifier_calls)
        #: The full message list of every verifier call, not just its system prompt.
        #: The retry's feedback lives in the *user* messages, so recording only the
        #: system prompt would make a retry indistinguishable from a repeat.
        self.verifier_prompts: list[list[object]] = []
        self.planner_prompts: list[str] = []

    @staticmethod
    def _system(messages) -> str:
        return str(getattr(messages[0], "content", "") or "")

    @staticmethod
    def _all_text(messages) -> str:
        return "\n".join(str(getattr(m, "content", "") or "") for m in messages)

    def _tool(self, name: str, arguments: dict) -> ChatResult:
        return ChatResult(
            content="",
            tool_calls=[ToolCallRequest(id=f"c{len(self.verifier_prompts)}", name=name, arguments=arguments)],
        )

    async def chat(self, messages, **kwargs) -> ChatResult:
        system = self._system(messages)

        if "任务规划器" in system:
            self.planner_prompts.append(system)
            return ChatResult(content=PLAN_JSON)

        if "最终验收器" in system:
            self.verifier_prompts.append(list(messages))
            index = min(len(self.verifier_prompts) - 1, len(self.verifier_calls) - 1)
            name, path = self.verifier_calls[index]
            return self._tool(name, {"path": path})

        if "你是验证器" in system:
            # The per-step verifier. Always a truthful yes.
            return self._tool("file_exists", {"path": REAL_PATH})

        if "请用中文向用户汇报" in system:
            return ChatResult(content="测试总结")

        # The executor: write the file, then stop.
        if not any(str(getattr(m, "role", "")) == "tool" for m in messages):
            return self._tool("document_write", {"path": REAL_PATH, "format": "pptx"})
        return ChatResult(content="这一步做完了")


class PathAwareRegistry:
    """Reads fail unless the path is exactly the one that was written.

    That is what the real tool does — `document_read` on a path that is not there
    returns `ValidationError: 文件不存在` — and reproducing it is the point: the
    test must be able to tell "the check errored" apart from "the answer is no".
    """

    def __init__(self, *, existing: set[str]) -> None:
        self.existing = existing
        self.calls: list[tuple[str, dict]] = []

    def names(self) -> list[str]:
        return ["document_write", "document_read", "file_exists"]

    def specs(self) -> list[object]:
        return []

    async def execute(self, ctx, name, arguments) -> ToolResult:
        self.calls.append((name, arguments))
        path = str(arguments.get("path") or "")
        if name == "file_exists":
            # Truthful either way: ok=True, and the answer is in `exists`.
            return ToolResult(ok=True, data={"exists": path in self.existing}, summary="file_exists")
        if name == "document_read":
            if path not in self.existing:
                return ToolResult(
                    ok=False, error=f"ValidationError: 文件不存在：{path}", summary="读取失败"
                )
            return ToolResult(ok=True, data={"path": path, "slides": 11}, summary="document_read ok")
        if name == "document_write":
            self.existing.add(path)
            return ToolResult(ok=True, data={"path": path}, summary="document_write ok")
        return ToolResult(ok=True, data={}, summary=f"{name} ok")


def build_agent(*, llm, registry, tmp_path):
    policy = AgentPolicy.from_settings(level=2, max_retries=1, max_tool_calls=100)
    controller = RunController(run_id="test-run", owner_id=uuid.uuid4())
    agent = AutonomousAgent(
        llm=llm,
        registry=registry,
        policy=policy,
        controller=controller,
        session=None,
        owner_id=None,
        working_directory=str(tmp_path),
    )
    return agent, controller


@pytest.fixture(autouse=True)
def _builtin_planner(monkeypatch):
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
# 1. The verifier is told where the file is
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_verifier_is_told_what_the_run_produced(tmp_path):
    llm = ScriptedLLM(verifier_calls=[("document_read", REAL_PATH)])
    registry = PathAwareRegistry(existing=set())
    agent, _ = build_agent(llm=llm, registry=registry, tmp_path=tmp_path)

    await agent.run("生成一份目录 PPT")

    assert llm.verifier_prompts, "the final verifier must have been called"
    prompt = ScriptedLLM._all_text(llm.verifier_prompts[0])
    # The real path, verbatim, and an instruction not to invent one.
    assert REAL_PATH in prompt
    assert "不要自己拼路径" in prompt


@pytest.mark.asyncio
async def test_the_artifact_list_is_absent_when_nothing_was_written(tmp_path):
    """No artifacts must not become an empty bullet list that reads as "there are
    files, here is the list"."""
    llm = ScriptedLLM(verifier_calls=[("document_read", REAL_PATH)])
    registry = PathAwareRegistry(existing=set())
    agent, _ = build_agent(llm=llm, registry=registry, tmp_path=tmp_path)
    agent.artifacts = []
    assert agent._artifact_block() == ""


# --------------------------------------------------------------------------- #
# 2. An errored call is retried; a truthful "no" is not
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_failed_evidence_call_is_retried_and_can_then_pass(tmp_path):
    """The exact regression: a wrong path on attempt 1 must not end the check."""
    llm = ScriptedLLM(
        verifier_calls=[("document_read", GUESSED_PATH), ("document_read", REAL_PATH)]
    )
    registry = PathAwareRegistry(existing=set())
    agent, _ = build_agent(llm=llm, registry=registry, tmp_path=tmp_path)

    result = await agent.run("生成一份目录 PPT")

    assert result.state is RunState.COMPLETED, result.state
    assert result.verified is True
    # Two prompts: the first attempt, and the retry.
    assert len(llm.verifier_prompts) == 2
    # The retry was told *why* it is retrying, not just asked again.
    assert "没有取到证据" in ScriptedLLM._all_text(llm.verifier_prompts[1])
    # And the run remembered what it wrote.
    assert REAL_PATH in agent.artifacts


@pytest.mark.asyncio
async def test_a_truthful_no_is_not_retried(tmp_path):
    """`file_exists: false` is an answer. Retrying it would be asking twice."""
    llm = ScriptedLLM(verifier_calls=[("file_exists", "nope/never.md")])
    registry = PathAwareRegistry(existing=set())
    agent, _ = build_agent(llm=llm, registry=registry, tmp_path=tmp_path)

    result = await agent.run("生成一份目录 PPT")

    assert len(llm.verifier_prompts) == 1, "a conclusive 'no' must not be retried"
    assert result.state is RunState.FAILED, result.state


# --------------------------------------------------------------------------- #
# 3. "Could not check" is not "failed"
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_every_attempt_erroring_is_inconclusive_and_not_failed(tmp_path):
    """The whole point. Steps all passed; the check never ran; nothing failed."""
    llm = ScriptedLLM(verifier_calls=[("document_read", GUESSED_PATH)])  # same bad path every time
    registry = PathAwareRegistry(existing=set())
    agent, controller = build_agent(llm=llm, registry=registry, tmp_path=tmp_path)

    result = await agent.run("生成一份目录 PPT")

    assert len(llm.verifier_prompts) == agent.MAX_FINAL_VERIFY_ATTEMPTS
    assert result.state is RunState.WAITING_CONFIRMATION, result.state
    assert result.state is not RunState.FAILED
    assert result.verified is False
    # The reason is in the record, and it says which of the two things happened.
    assert any(
        "没能取得证据" in event.message for event in controller.events if event.kind == "verification"
    )
    assert any(
        event.kind == "verification" and event.data.get("conclusive") is False
        for event in controller.events
    )


@pytest.mark.asyncio
async def test_a_conclusive_no_still_fails_the_run(tmp_path):
    """The counterweight: the new branch must not swallow real failures."""
    llm = ScriptedLLM(verifier_calls=[("file_exists", "nope/never.md")])
    registry = PathAwareRegistry(existing=set())
    agent, _ = build_agent(llm=llm, registry=registry, tmp_path=tmp_path)

    result = await agent.run("生成一份目录 PPT")

    assert result.state is RunState.FAILED
    assert any(
        event.kind == "verification" and event.data.get("answered_no") is True
        for event in _controller_events(agent)
    )


def _controller_events(agent):
    return agent.controller.events


# --------------------------------------------------------------------------- #
# 4. The retry loop is bounded, and it is a *different* request each time
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_the_retry_is_bounded(tmp_path):
    llm = ScriptedLLM(verifier_calls=[("document_read", GUESSED_PATH)])
    registry = PathAwareRegistry(existing=set())
    agent, _ = build_agent(llm=llm, registry=registry, tmp_path=tmp_path)

    await agent.run("生成一份目录 PPT")

    assert agent.MAX_FINAL_VERIFY_ATTEMPTS <= 5, "a retry loop that cannot end is a hang"


def test_the_retry_ceiling_is_small_enough_to_not_be_a_hang():
    """A retry loop that cannot end is a hang, and this one is on the critical path
    of every run."""
    assert 1 < AutonomousAgent.MAX_FINAL_VERIFY_ATTEMPTS <= 5


@pytest.mark.asyncio
async def test_no_success_criteria_is_passed_and_conclusive(tmp_path):
    """Nothing to check is not the same as nothing checked.

    An empty criteria list means the plan did not ask to be verified, so the gate
    passes — and it must pass as *conclusive*, or every such run would be reported
    as "could not verify" and land in WAITING_USER.
    """
    llm = ScriptedLLM(verifier_calls=[])
    registry = PathAwareRegistry(existing=set())
    agent, _ = build_agent(llm=llm, registry=registry, tmp_path=tmp_path)

    plan = ExecutionPlan(goal="g", steps=[PlanStep(id="s", description="d")], success_criteria=[])
    passed, conclusive, evidence = await agent._final_verify(plan)

    assert (passed, conclusive, evidence) == (True, True, [])
    assert llm.verifier_prompts == [], "no criteria means no verifier call at all"

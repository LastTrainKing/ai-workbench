"""Recovery acceptance: three scenarios, run for real, with the event stream as evidence.

This is not a unit test. It drives the actual agent — real planner prompt, real tool
registry, real permission layer, real model — so that the answer to "does the retry
mechanism work?" comes from a run rather than from reading the code.

    场景 A  a task that succeeds   → no recovery, no retries
    场景 B  a step that must fail  → the 2nd attempt is different from the 1st
    场景 C  a step that keeps failing → the step stops instead of burning the run

The three questions this has to be able to answer afterwards, from the output:

    Which step had the first retry, and what did the second attempt change?
    If two attempts were identical, why did the ineffective-retry rule not fire?
    How many tool calls did the stuck step actually spend?

Run:  python scripts/verify_recovery.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

SCRATCH = ROOT / ".runtime" / "recovery-check"

PASS = "PASS"
FAIL = "FAILED"


def line(title: str) -> None:
    print(f"\n{'=' * 78}\n  {title}\n{'=' * 78}")


class FailingRegistry:
    """Every tool call refused, with the real catalogue still visible.

    Scenario C needs a step that cannot succeed no matter what it tries. Denying
    every call is the honest version of that: the model still sees the full tool
    list and can still choose freely — it simply cannot get anything to run.
    """

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.attempts = 0

    def names(self) -> list[str]:
        return self.inner.names()

    def specs(self) -> list[Any]:
        return self.inner.specs()

    async def execute(self, ctx: Any, name: str, arguments: dict[str, Any]) -> Any:
        from app.ai.types import ToolResult

        self.attempts += 1
        return ToolResult(
            ok=False,
            error="权限被拒绝：该步骤在当前策略下不允许执行任何写操作",
            summary=f"{name} denied",
        )


async def run_scenario(
    label: str,
    goal: str,
    *,
    wrap: Any = None,
    deliverable_kind: str = "md",
) -> dict[str, Any]:
    """Drive the real agent through one scenario and return its record."""
    from sqlalchemy import select

    from app.ai.agent.autonomous import AutonomousAgent
    from app.ai.agent.control import RunController
    from app.ai.providers.factory import build_llm_provider
    from app.ai.tools.audit import make_audit_sink
    from app.ai.tools.builtin.filesystem import build_filesystem_tools  # noqa: F401
    from app.ai.tools.confirmation import get_broker
    from app.ai.tools.executor import ResourceLimits, ToolExecutor
    from app.ai.tools.policy_store import build_policy, capability_flags
    from app.ai.tools.registry import build_computer_registry
    from app.ai.tools.unified import UnifiedToolRegistry
    from app.infrastructure.database import session_scope
    from app.models.user import User

    SCRATCH.mkdir(parents=True, exist_ok=True)
    workdir = SCRATCH / label
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    async with session_scope() as session:
        owner = (await session.execute(select(User).limit(1))).scalar_one()

        policy = build_policy()
        # The run is confined to its own directory: an acceptance run must not be
        # able to leave anything in the real workspace.
        policy.workspace = workdir
        policy.allowed_paths = [*policy.allowed_paths, workdir]

        business = __import__("app.ai.agent.tools", fromlist=["get_tool_registry"]).get_tool_registry()
        computer = build_computer_registry(policy, enabled=capability_flags())
        limits = ResourceLimits(
            max_output_bytes=policy.max_output_bytes,
            max_files_modified=policy.max_files_modified,
            max_tool_calls=policy.max_tool_calls,
            max_runtime_seconds=policy.max_runtime_seconds,
        )
        executor = ToolExecutor(policy, audit=make_audit_sink(session))
        registry: Any = UnifiedToolRegistry(
            business, computer, executor, confirm=get_broker().request, limits=limits
        )
        if wrap is not None:
            registry = wrap(registry)

        llm, info = build_llm_provider()
        controller = RunController(run_id=f"recovery-{label}")
        agent = AutonomousAgent(
            llm=llm,
            registry=registry,
            policy=policy,
            controller=controller,
            session=session,
            owner_id=owner.id,
            limits=limits,
            working_directory=str(workdir),
            deliverable_kind=deliverable_kind,
        )

        print(f"  模型：{info.kind} / {info.model}  降级={info.degraded}")
        print(f"  目标：{goal}")
        print(f"  工作目录：{workdir}")
        result = await agent.run(goal)

        return {
            "label": label,
            "result": result,
            "controller": controller,
            "workdir": workdir,
            "llm": info,
        }


def dump_events(controller: Any, *, only: tuple[str, ...] = ("error", "step", "verification")) -> None:
    print("\n  事件流（关键项）：")
    for event in controller.events:
        if event.kind not in only:
            continue
        text = (event.message or "").replace("\n", " ")[:150]
        print(f"    [{event.kind:12}] {text}")


def dump_attempts(plan: Any) -> None:
    print("\n  各步骤的尝试记录：")
    for step in plan.steps:
        print(f"    [{step.status.value}] {step.id} {step.description[:44]}")
        print(f"        工具调用 {step.tool_calls}  重试 {step.retry_count}")
        if step.error:
            print(f"        错误：{step.error[:160]}")
        for record in step.attempts:
            tools = "、".join(record.get("tools_used") or []) or "（没有调用工具）"
            print(
                f"        · 第 {record['attempt']} 次：{tools}  "
                f"sig={str(record['signature'])[:40]}  与上次相同={record.get('same_as_previous')}"
            )
            if record.get("analysis"):
                print(f"          分析：{str(record['analysis'])[:150]}")


async def scenario_a() -> bool:
    line("场景 A —— 正常任务：不触发恢复，正常完成")
    record = await run_scenario(
        "a",
        "在工作目录下创建 notes/recovery-a.md，内容写三行关于「重试机制」的说明，然后确认文件存在。",
    )
    result, controller = record["result"], record["controller"]
    plan = result.plan

    dump_events(controller)
    dump_attempts(plan)

    total_attempts = sum(len(step.attempts) for step in plan.steps)
    recovered = any(event.kind == "error" for event in controller.events)
    print(f"\n  终态 {result.state.value}  工具调用 {result.tool_calls}  重试 {result.retries}")
    print(f"  步骤：{plan.done_steps}/{len(plan.steps)} 完成")

    checks = {
        "终态为 COMPLETED": result.state.value == "COMPLETED",
        "没有进入恢复（无 error 事件）": not recovered,
        "重试次数为 0": result.retries == 0,
        "没有任何步骤记录失败尝试": total_attempts == 0,
    }
    for name, ok in checks.items():
        print(f"    [{'OK' if ok else 'X'}] {name}")
    return all(checks.values())


async def scenario_b() -> bool:
    line("场景 B —— 某一步必然失败：第 2 次尝试必须与第 1 次不同")
    denied = r"C:\Windows\System32\aiworkbench-blocked.txt"
    record = await run_scenario(
        "b",
        f"把「恢复机制验收」写入 {denied}。这个文件必须写入成功，不允许用其它路径替代。",
    )
    result, controller = record["result"], record["controller"]
    plan = result.plan

    dump_events(controller)
    dump_attempts(plan)

    failing = [step for step in plan.steps if step.attempts]
    print(f"\n  终态 {result.state.value}  工具调用 {result.tool_calls}  重试 {result.retries}")

    if not failing:
        print("    [X] 没有任何步骤产生失败尝试 —— 场景没有构造成功")
        return False

    step = failing[0]
    first, second = step.attempts[0], step.attempts[1] if len(step.attempts) > 1 else None
    print(f"\n  第一个发生重试的步骤：{step.id} —— {step.description[:60]}")
    print(f"    第 1 次：{first.get('tools_used') or '（无工具）'}  sig={str(first['signature'])[:48]}")
    if second:
        print(f"    第 2 次：{second.get('tools_used') or '（无工具）'}  sig={str(second['signature'])[:48]}")
    else:
        print("    第 2 次：没有发生")

    checks = {
        "第 1 次尝试失败并产生了分析": bool(step.attempts) and bool(first.get("error")),
        "恢复分析被记录进尝试历史": bool(first.get("analysis")),
        "存在第 2 次尝试": second is not None,
        "第 2 次与第 1 次内容不同": bool(second and second["signature"] != first["signature"]),
        # Per step, not in total: `max_retries` bounds one step's retries, and a
        # run with several failing steps legitimately has more in total.
        "单步重试次数未超过 max_retries": max(
            (s.retry_count for s in plan.steps), default=0
        )
        <= 3,
        "没有撞上全局上限": result.state.value != "LIMIT_REACHED" and result.tool_calls < 100,
    }
    for name, ok in checks.items():
        print(f"    [{'OK' if ok else 'X'}] {name}")
    return all(checks.values())


async def scenario_c() -> bool:
    line("场景 C —— 某一步连续失败：达到上限后停止该步骤，不烧穿全局预算")
    record = await run_scenario(
        "c",
        "在工作目录下创建 notes/recovery-c.md 并写入一段说明，然后确认文件存在。",
        wrap=FailingRegistry,
    )
    result, controller = record["result"], record["controller"]
    plan = result.plan

    dump_events(controller)
    dump_attempts(plan)

    failing = [step for step in plan.steps if step.status.value == "FAILED"]
    print(f"\n  终态 {result.state.value}  工具调用 {result.tool_calls}  重试 {result.retries}")

    stopped_reasons = [
        event.message for event in controller.events
        if event.kind == "error"
        and ("无效重试" in event.message or "恢复分析失败" in event.message or "单步上限" in event.message)
    ]
    retry_cap = [
        event.message for event in controller.events
        if event.kind == "step" and "次重试后仍失败" in event.message
    ]

    checks = {
        "有步骤最终 FAILED": bool(failing),
        # Per step: the point is that *a* step cannot keep retrying forever, not
        # that the run as a whole has a small retry count.
        "单步重试次数未超过 max_retries": max(
            (s.retry_count for s in plan.steps), default=0
        )
        <= 3,
        "没有撞上全局上限（未烧穿 100 次）": result.state.value != "LIMIT_REACHED",
        "工具调用总数远低于全局上限": result.tool_calls < 40,
        "停止原因被明确记录（无效重试 / 恢复失败 / 单步上限 / 重试用尽）": bool(
            stopped_reasons or retry_cap
        ),
    }
    for name, ok in checks.items():
        print(f"    [{'OK' if ok else 'X'}] {name}")
    print(f"\n  停止原因：{stopped_reasons or retry_cap or '（无）'}")
    print(f"  该步骤实际花费：{[s.tool_calls for s in plan.steps]} 次工具调用")
    return all(checks.values())


async def main() -> int:
    line("恢复机制验收 —— 三个场景，真实运行")
    results = {
        "场景 A 正常任务不触发恢复": await scenario_a(),
        "场景 B 失败后第 2 次尝试内容不同": await scenario_b(),
        "场景 C 达上限后停止该步骤": await scenario_c(),
    }

    line("结论")
    for name, ok in results.items():
        print(f"  [{'PASS' if ok else 'FAILED'}] {name}")

    verdict = {
        "scenarios": {name: ("PASS" if ok else "FAILED") for name, ok in results.items()},
        "all_passed": all(results.values()),
    }
    (SCRATCH / "verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  结论已写入 {SCRATCH / 'verdict.json'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

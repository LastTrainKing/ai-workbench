"""Demo 3 — the agent finds and fixes a real bug, and the fix is checked independently.

This is the "action layer" claim in its hardest form: not "it can call a tool" but
"it can be handed a broken project and leave it working". The important word is
*independently*: nothing here trusts the agent's account of what it did. The test
suite is run before and after, the test file is hashed, and `git diff --stat` is
read to see what actually changed.

    before   pytest exits 1, two tests fail
    the agent runs, with no hint about where the bug is
    after    pytest exits 0, only `src/` changed, the tests are byte-identical

The broken project is built in `.runtime/demo3/` and torn down afterwards, because
the rule for this demo is explicit: manufacture the fault somewhere disposable, not
in the project being delivered.

Run:  python scripts/demo_3_autonomous_repair.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DEMO = ROOT / ".runtime" / "demo3"
PROJECT = DEMO / "stats-project"

GOAL = (
    "这个项目里的测试有失败。请自己找出原因，修改 src 目录下的代码把问题修好，"
    "让 python -m pytest 全部通过。注意：不要修改 tests 目录下的任何文件 —— "
    "测试写的是期望的行为，代码要迁就测试。"
)

#: Two independent bugs, so the fix is a real change rather than a one-liner.
SOURCE = '''"""Small statistics helpers."""

from __future__ import annotations


def average(values: list[float]) -> float:
    """The arithmetic mean. An empty list averages to 0.0."""
    return sum(values) / len(values)


def median(values: list[float]) -> float:
    """The middle value, or the mean of the two middle values."""
    ordered = sorted(values)
    return ordered[len(ordered) // 2]
'''

TESTS = '''"""Behaviour the helpers are expected to have."""

from __future__ import annotations

from src.stats import average, median


def test_average_of_numbers() -> None:
    assert average([1, 2, 3]) == 2.0


def test_average_of_empty_is_zero() -> None:
    assert average([]) == 0.0


def test_median_of_odd_length() -> None:
    assert median([3, 1, 2]) == 2


def test_median_of_even_length() -> None:
    assert median([1, 2, 3, 4]) == 2.5
'''

PYPROJECT = """[project]
name = "stats-project"
version = "0.1.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
"""

FAILING = {"test_average_of_empty_is_zero", "test_median_of_even_length"}


def line(title: str) -> None:
    print(f"\n{'=' * 78}\n  {title}\n{'=' * 78}")


def _force_remove(path: Path) -> None:
    """Remove a tree, clearing read-only bits (git objects are written read-only)."""
    import os
    import stat

    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if path.exists():
        shutil.rmtree(path, onexc=on_error)


def build_project() -> None:
    """Create the broken project and put it under version control."""
    _force_remove(DEMO)
    (PROJECT / "src").mkdir(parents=True)
    (PROJECT / "tests").mkdir(parents=True)
    (PROJECT / "src" / "__init__.py").write_text("", encoding="utf-8")
    (PROJECT / "src" / "stats.py").write_text(SOURCE, encoding="utf-8")
    (PROJECT / "tests" / "test_stats.py").write_text(TESTS, encoding="utf-8")
    (PROJECT / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (PROJECT / "README.md").write_text(
        "# stats-project\n\nSmall statistics helpers.\n", encoding="utf-8"
    )

    for command in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=demo@example.com", "-c", "user.name=demo", "commit", "-q", "-m", "initial"],
    ):
        subprocess.run(command, cwd=PROJECT, check=True, capture_output=True)


def run_pytest() -> dict[str, Any]:
    """Run the project's own interpreter, exactly as the agent's tool does."""
    from app.ai.tools.builtin.shell import resolve_interpreter

    exe = resolve_interpreter("python", PROJECT)
    result = subprocess.run(
        [exe, "-m", "pytest", "-q"],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (result.stdout or "") + (result.stderr or "")
    return {
        "interpreter": exe,
        "exit_code": result.returncode,
        "output": output[-2500:],
        "failed_tests": sorted({name for name in FAILING if name in output})
        if result.returncode
        else [],
    }


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return (result.stdout or "") + (result.stderr or "")


async def drive_agent() -> dict[str, Any]:
    """Hand the broken project to the real agent and record what it did."""
    from app.ai.agent.autonomous import AutonomousAgent
    from app.ai.agent.control import RunController
    from app.ai.providers.factory import build_llm_provider
    from app.ai.tools.audit import make_audit_sink
    from app.ai.tools.confirmation import get_broker
    from app.ai.tools.executor import ResourceLimits, ToolExecutor
    from app.ai.tools.policy_store import build_policy, capability_flags
    from app.ai.tools.registry import build_computer_registry
    from app.ai.tools.unified import UnifiedToolRegistry
    from app.infrastructure.database import session_scope
    from app.models.user import User
    from sqlalchemy import select

    async with session_scope() as session:
        owner = (await session.execute(select(User).limit(1))).scalar_one()
        policy = build_policy()
        policy.workspace = PROJECT
        policy.allowed_paths = [*policy.allowed_paths, PROJECT]

        business = __import__("app.ai.agent.tools", fromlist=["get_tool_registry"]).get_tool_registry()
        computer = build_computer_registry(policy, enabled=capability_flags())
        limits = ResourceLimits(
            max_output_bytes=policy.max_output_bytes,
            max_files_modified=policy.max_files_modified,
            max_tool_calls=policy.max_tool_calls,
            max_runtime_seconds=policy.max_runtime_seconds,
        )
        executor = ToolExecutor(policy, audit=make_audit_sink(session))
        registry = UnifiedToolRegistry(
            business, computer, executor, confirm=get_broker().request, limits=limits
        )
        llm, info = build_llm_provider()
        controller = RunController(run_id="demo3-repair")
        agent = AutonomousAgent(
            llm=llm,
            registry=registry,
            policy=policy,
            controller=controller,
            session=session,
            owner_id=owner.id,
            limits=limits,
            working_directory=str(PROJECT),
            deliverable_kind="code",
        )

        print(f"  模型：{info.kind} / {info.model}")
        result = await agent.run(GOAL)
        print(f"  终态：{result.state.value}  工具调用 {result.tool_calls}  重试 {result.retries}")
        print("  步骤：")
        for step in (result.plan.steps if result.plan else []):
            print(f"    [{step.status.value}] {step.description[:64]}")
            if step.error:
                print(f"        {step.error[:120]}")
        print("  工具轨迹：")
        for event in controller.events:
            if event.kind == "tool":
                print(f"    {event.message[:110]}")
        return {"state": result.state.value, "answer": result.answer, "tool_calls": result.tool_calls}


async def main() -> int:
    line("Demo 3 —— Agent 自主修复（隔离目录）")
    build_project()
    print(f"  项目：{PROJECT.relative_to(ROOT)}")

    line("修复前")
    before = run_pytest()
    print(f"  解释器：{before['interpreter']}")
    print(f"  pytest 退出码：{before['exit_code']}（应为非 0）")
    print(f"  失败的用例：{before['failed_tests']}")
    tests_before = digest(PROJECT / "tests" / "test_stats.py")
    source_before = digest(PROJECT / "src" / "stats.py")
    print(f"  tests/test_stats.py sha256：{tests_before[:16]}…")
    print(f"  src/stats.py        sha256：{source_before[:16]}…")

    line("让 Agent 自己修")
    agent_record = await drive_agent()

    line("修复后（独立核验，不看 agent 的自述）")
    after = run_pytest()
    tests_after = digest(PROJECT / "tests" / "test_stats.py")
    source_after = digest(PROJECT / "src" / "stats.py")
    print(f"  pytest 退出码：{after['exit_code']}")
    print(f"  tests/test_stats.py sha256：{tests_after[:16]}…")
    print(f"  src/stats.py        sha256：{source_after[:16]}…")
    print("\n  git status --porcelain：")
    status = git(["status", "--porcelain"])
    for row in status.strip().splitlines():
        print(f"    {row}")
    print("\n  git diff --stat：")
    diff_stat = git(["diff", "--stat"])
    for row in diff_stat.strip().splitlines():
        print(f"    {row}")

    line("结论")
    checks = {
        "修复前 pytest 确实失败": before["exit_code"] != 0,
        "修复前失败的正是预期的那两个用例": set(before["failed_tests"]) == FAILING,
        "修复后 pytest 退出码为 0": after["exit_code"] == 0,
        "测试文件未被改动（sha256 一致）": tests_before == tests_after,
        "源码确实被改了（sha256 不一致）": source_before != source_after,
        "git diff 只改了 src/": all(
            row.split()[-1].startswith("src/") for row in diff_stat.strip().splitlines() if "|" in row
        ),
        "原失败用例现在通过": after["exit_code"] == 0 and not after["failed_tests"],
    }
    for name, ok in checks.items():
        print(f"    [{'OK' if ok else 'X'}] {name}")

    record = {
        "goal": GOAL,
        "before": before,
        "after": after,
        "agent": agent_record,
        "tests_sha256": {"before": tests_before, "after": tests_after},
        "source_sha256": {"before": source_before, "after": source_after},
        "git_status": status,
        "git_diff_stat": diff_stat,
        "checks": checks,
        "passed": all(checks.values()),
    }
    # Written outside the disposable project, so it survives the teardown.
    out = ROOT / ".runtime" / "demo3-record.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  完整记录：{out.relative_to(ROOT)}")

    if record["passed"]:
        # Torn down only on success: a failed run is evidence and is left where it
        # can be looked at rather than tidied away.
        _force_remove(DEMO)
        print("  已清理隔离目录")
    else:
        print(f"  未通过，隔离目录保留在 {DEMO.relative_to(ROOT)} 供排查")
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

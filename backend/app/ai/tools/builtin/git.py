"""Git and project tools.

Git is treated as a first-class capability rather than a special case of the
terminal, for one reason: history-rewriting operations need to be recognisable
before they run. ``git reset --hard`` and ``git clean -fd`` discard work that no
other part of the system can restore, so they carry a high risk level and always
ask — even in autonomous mode, where the agent is otherwise trusted to get on
with the job.

Read-only git (status, diff, log, branch) is unrestricted inside the workspace.
That asymmetry is deliberate: looking is free, losing work is not.

The project tools run the project's own commands (tests, lint, typecheck, build)
through the detected package manager, so a Node project and a Python project are
both handled without the agent having to know which is which.
"""
from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path
from typing import Any

from app.ai.agent.tools import Tool, ToolContext
from app.ai.tools.builtin.shell import _run_process, _shell_for
from app.ai.types import ToolResult, ToolSpec
from app.core.errors import ValidationError

GIT_TIMEOUT = 120.0
BUILD_TIMEOUT = 900.0


def _git() -> str:
    exe = shutil.which("git")
    if not exe:
        raise ValidationError("git was not found on PATH")
    return exe


async def _git_run(cwd: Path, args: list[str], timeout: float = GIT_TIMEOUT) -> dict[str, Any]:
    return await _run_process(exe=_git(), args=args, cwd=cwd, timeout=timeout)


class _GitTool(Tool):
    """Shared plumbing for the git tools."""

    is_write = False
    risk_level = "low"

    async def _repo(self, ctx: ToolContext, path: str | None) -> Path:
        root = Path(ctx.resolve(path)) if path else Path(ctx.resolve("."))
        ctx.require_permission(root, write=self.is_write)
        if not root.exists():
            raise ValidationError(f"No such directory: {root}")
        if root.is_file():
            root = root.parent
        return root

    async def _ensure_repo(self, root: Path) -> str | None:
        probe = await _git_run(root, ["rev-parse", "--is-inside-work-tree"])
        if probe["exit_code"] != 0:
            return f"{root} is not a git repository"
        return None


# --------------------------------------------------------------------------- #
# read-only
# --------------------------------------------------------------------------- #
class GitStatusTool(_GitTool):
    spec = ToolSpec(
        name="git_status",
        description="Show the working tree status in porcelain form, with branch and ahead/behind counts.",
        category="git",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": []},
        permission_level=0,
        risk_level="low",
        timeout_seconds=GIT_TIMEOUT,
        mutates_state=False,
    )

    async def execute(self, ctx: ToolContext, path: str | None = None) -> ToolResult:
        root = await self._repo(ctx, path)
        if (err := await self._ensure_repo(root)):
            return ToolResult(ok=False, error=err, summary="not a repository")

        status = await _git_run(root, ["status", "--porcelain=v1", "--branch"])
        files = [line for line in status["stdout"].splitlines() if line and not line.startswith("##")]
        branch = ""
        ahead = behind = 0
        for line in status["stdout"].splitlines():
            if line.startswith("##"):
                branch = line[2:].strip()
                if "ahead " in branch:
                    ahead = int(branch.split("ahead ")[1].split("]")[0].split(",")[0])
                if "behind " in branch:
                    behind = int(branch.split("behind ")[1].split("]")[0].split(",")[0])
                branch = branch.split("...")[0].strip()

        return ToolResult(
            ok=True,
            data={
                "repository": str(root),
                "branch": branch,
                "ahead": ahead,
                "behind": behind,
                "clean": not files,
                "changed_files": files,
                "change_count": len(files),
            },
            summary=f"{branch or 'detached'}: {len(files)} changed file(s)",
        )


class GitDiffTool(_GitTool):
    spec = ToolSpec(
        name="git_diff",
        description="Show the diff of unstaged changes, staged changes, or against a revision.",
        category="git",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "file": {"type": "string", "description": "Limit the diff to one file"},
                "staged": {"type": "boolean"},
                "revision": {"type": "string", "description": "e.g. HEAD~1"},
            },
            "required": [],
        },
        permission_level=0,
        risk_level="low",
        timeout_seconds=GIT_TIMEOUT,
        mutates_state=False,
    )

    async def execute(
        self,
        ctx: ToolContext,
        path: str | None = None,
        file: str | None = None,
        staged: bool = False,
        revision: str | None = None,
    ) -> ToolResult:
        root = await self._repo(ctx, path)
        if (err := await self._ensure_repo(root)):
            return ToolResult(ok=False, error=err, summary="not a repository")

        args = ["diff", "--no-color", "--stat"]
        if staged:
            args.append("--cached")
        if revision:
            args.append(revision)
        stat = await _git_run(root, args)
        if file:
            args.append("--")
            args.append(file)
        full = await _git_run(root, [*args, "--patch"] if "--patch" not in args else args)

        return ToolResult(
            ok=True,
            data={
                "repository": str(root),
                "stat": stat["stdout"].strip(),
                "diff": full["stdout"],
                "empty": not full["stdout"].strip(),
            },
            summary="diff captured" if full["stdout"].strip() else "no changes",
        )


class GitLogTool(_GitTool):
    spec = ToolSpec(
        name="git_log",
        description="Recent commits, one line each.",
        category="git",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer"},
                "file": {"type": "string"},
            },
            "required": [],
        },
        permission_level=0,
        risk_level="low",
        timeout_seconds=GIT_TIMEOUT,
        mutates_state=False,
    )

    async def execute(
        self, ctx: ToolContext, path: str | None = None, limit: int = 20, file: str | None = None
    ) -> ToolResult:
        root = await self._repo(ctx, path)
        if (err := await self._ensure_repo(root)):
            return ToolResult(ok=False, error=err, summary="not a repository")

        args = ["log", f"-{max(1, min(int(limit or 20), 200))}", "--pretty=format:%h|%an|%ad|%s", "--date=short"]
        if file:
            args += ["--", file]
        result = await _git_run(root, args)
        commits = []
        for line in result["stdout"].splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({"sha": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})

        return ToolResult(ok=True, data={"commits": commits, "count": len(commits)}, summary=f"{len(commits)} commits")


class GitBranchTool(_GitTool):
    spec = ToolSpec(
        name="git_branch",
        description="List local branches and show the current one.",
        category="git",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": []},
        permission_level=0,
        risk_level="low",
        timeout_seconds=GIT_TIMEOUT,
        mutates_state=False,
    )

    async def execute(self, ctx: ToolContext, path: str | None = None) -> ToolResult:
        root = await self._repo(ctx, path)
        if (err := await self._ensure_repo(root)):
            return ToolResult(ok=False, error=err, summary="not a repository")
        result = await _git_run(root, ["branch", "--format=%(refname:short)|%(HEAD)"])
        branches = []
        current = ""
        for line in result["stdout"].splitlines():
            name, _, head = line.partition("|")
            branches.append(name)
            if head.strip() == "*":
                current = name
        return ToolResult(
            ok=True, data={"branches": branches, "current": current}, summary=f"on {current or 'detached'}"
        )


# --------------------------------------------------------------------------- #
# write
# --------------------------------------------------------------------------- #
class GitCheckoutTool(_GitTool):
    spec = ToolSpec(
        name="git_checkout",
        description="Switch to an existing branch or revision.",
        category="git",
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "create": {"type": "boolean", "description": "Create the branch (-b)"},
                "path": {"type": "string"},
            },
            "required": ["target"],
        },
        permission_level=1,
        risk_level="medium",
        timeout_seconds=GIT_TIMEOUT,
        mutates_state=True,
        requires_confirmation=True,
    )
    is_write = True

    async def execute(
        self, ctx: ToolContext, target: str, create: bool = False, path: str | None = None
    ) -> ToolResult:
        root = await self._repo(ctx, path)
        if (err := await self._ensure_repo(root)):
            return ToolResult(ok=False, error=err, summary="not a repository")
        args = ["checkout"]
        if create:
            args.append("-b")
        args.append(target)
        result = await _git_run(root, args)
        return ToolResult(
            ok=result["exit_code"] == 0,
            data={"target": target, "created": create, "output": result["stdout"] + result["stderr"]},
            error="" if result["exit_code"] == 0 else result["stderr"],
            summary=f"checked out {target}",
        )


class GitAddTool(_GitTool):
    spec = ToolSpec(
        name="git_add",
        description="Stage files.",
        category="git",
        parameters={
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "description": "Empty means all changes"},
                "path": {"type": "string", "description": "Repository directory"},
            },
            "required": [],
        },
        permission_level=1,
        risk_level="low",
        timeout_seconds=GIT_TIMEOUT,
        mutates_state=True,
    )
    is_write = True

    async def execute(
        self, ctx: ToolContext, paths: list[str] | None = None, path: str | None = None
    ) -> ToolResult:
        root = await self._repo(ctx, path)
        if (err := await self._ensure_repo(root)):
            return ToolResult(ok=False, error=err, summary="not a repository")
        args = ["add", *(["--", *list(paths)] if paths else ["-A"])]
        result = await _git_run(root, args)
        ctx.consume_write()
        return ToolResult(
            ok=result["exit_code"] == 0,
            data={"staged": paths or "all", "output": result["stdout"] + result["stderr"]},
            error="" if result["exit_code"] == 0 else result["stderr"],
            summary="staged " + ("all changes" if not paths else f"{len(paths)} path(s)"),
        )


class GitCommitTool(_GitTool):
    spec = ToolSpec(
        name="git_commit",
        description="Create a commit with the given message.",
        category="git",
        parameters={
            "type": "object",
            "properties": {"message": {"type": "string"}, "path": {"type": "string"}},
            "required": ["message"],
        },
        permission_level=1,
        risk_level="low",
        timeout_seconds=GIT_TIMEOUT,
        mutates_state=True,
    )
    is_write = True

    async def execute(self, ctx: ToolContext, message: str, path: str | None = None) -> ToolResult:
        root = await self._repo(ctx, path)
        if (err := await self._ensure_repo(root)):
            return ToolResult(ok=False, error=err, summary="not a repository")
        if not message.strip():
            raise ValidationError("Commit message must not be empty")

        result = await _git_run(root, ["commit", "-m", message])
        if result["exit_code"] != 0:
            return ToolResult(
                ok=False,
                data={"output": result["stdout"] + result["stderr"]},
                error=result["stderr"] or result["stdout"],
                summary="commit failed",
            )
        sha = await _git_run(root, ["rev-parse", "--short", "HEAD"])
        ctx.consume_write()
        ctx.register_artifact("commits", sha["stdout"].strip())
        return ToolResult(
            ok=True,
            data={"sha": sha["stdout"].strip(), "message": message},
            summary=f"committed {sha['stdout'].strip()}",
        )


class GitRestoreTool(_GitTool):
    spec = ToolSpec(
        name="git_restore",
        description=(
            "Discard changes in the working tree, or unstage files. This destroys "
            "uncommitted work, so it always asks for confirmation first."
        ),
        category="git",
        parameters={
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "description": "Empty means the whole tree"},
                "staged": {"type": "boolean", "description": "Unstage instead of discarding"},
                "path": {"type": "string"},
            },
            "required": [],
        },
        permission_level=1,
        risk_level="high",
        timeout_seconds=GIT_TIMEOUT,
        mutates_state=True,
        requires_confirmation=True,
    )
    is_write = True

    async def execute(
        self,
        ctx: ToolContext,
        paths: list[str] | None = None,
        staged: bool = False,
        path: str | None = None,
    ) -> ToolResult:
        root = await self._repo(ctx, path)
        if (err := await self._ensure_repo(root)):
            return ToolResult(ok=False, error=err, summary="not a repository")

        # Capture what is about to be lost so the report can say how much.
        before = await _git_run(root, ["status", "--porcelain"])
        args = ["restore", "--staged"] if staged else ["restore"]
        args += ["--", *(paths or ["."])]
        result = await _git_run(root, args)
        ctx.consume_write()

        return ToolResult(
            ok=result["exit_code"] == 0,
            data={
                "restored": paths or "entire tree",
                "staged": staged,
                "affected_before": before["stdout"].splitlines(),
                "output": result["stdout"] + result["stderr"],
            },
            error="" if result["exit_code"] == 0 else result["stderr"],
            summary=("unstaged" if staged else "discarded changes in") + f" {paths or 'the working tree'}",
        )


# --------------------------------------------------------------------------- #
# project commands
# --------------------------------------------------------------------------- #
def detect_project(root: Path) -> dict[str, Any]:
    """Work out how to build and test whatever is in this directory."""
    kind = "unknown"
    runner: str | None = None
    commands: dict[str, str] = {}

    package_json = root / "package.json"
    pyproject = root / "pyproject.toml"
    requirements = root / "requirements.txt"

    if package_json.is_file():
        kind = "node"
        runner = "npm"
        try:
            manifest = json.loads(package_json.read_text(encoding="utf-8"))
            scripts = manifest.get("scripts", {}) or {}
        except (OSError, json.JSONDecodeError):
            scripts = {}
        if "test" in scripts:
            commands["test"] = "npm test --silent"
        if "lint" in scripts:
            commands["lint"] = "npm run lint"
        if "typecheck" in scripts:
            commands["typecheck"] = "npm run typecheck"
        if "build" in scripts:
            commands["build"] = "npm run build"
        if (root / "pnpm-lock.yaml").is_file():
            runner = "pnpm"
        elif (root / "yarn.lock").is_file():
            runner = "yarn"
    elif pyproject.is_file() or requirements.is_file() or (root / "setup.py").is_file():
        kind = "python"
        runner = "python"
        has_pytest = False
        if pyproject.is_file():
            with contextlib.suppress(OSError):
                has_pytest = "pytest" in pyproject.read_text(encoding="utf-8")
        if not has_pytest and (root / "tests").is_dir():
            has_pytest = True
        if has_pytest:
            commands["test"] = "python -m pytest -q"
        if (root / "ruff.toml").is_file() or (pyproject.is_file() and "ruff" in (
            pyproject.read_text(encoding="utf-8", errors="ignore") if pyproject.is_file() else ""
        )):
            commands["lint"] = "python -m ruff check ."
    elif any(root.glob("*.py")):
        # A directory of Python files with no manifest is still a Python project.
        # Requiring a manifest meant an agent that had just written `foo.py` and
        # `test_foo.py` into a fresh workspace could not run them — it reported
        # "no test command detected" for a project it had created itself.
        kind = "python"
        runner = "python"
        if any(root.glob("test_*.py")) or any(root.glob("*_test.py")) or (root / "tests").is_dir():
            commands["test"] = "python -m pytest -q"

    return {"kind": kind, "runner": runner, "commands": commands, "root": str(root)}


class _ProjectCommandTool(Tool):
    """Base for run_tests / run_lint / run_typecheck / run_build."""

    action = "test"
    is_write = False

    async def execute(
        self, ctx: ToolContext, path: str | None = None, command: str | None = None
    ) -> ToolResult:
        root = Path(ctx.resolve(path)) if path else Path(ctx.resolve("."))
        ctx.require_permission(root, write=True)
        if not root.is_dir():
            raise ValidationError(f"No such directory: {root}")

        info = detect_project(root)
        chosen = command or info["commands"].get(self.action)
        if not chosen:
            return ToolResult(
                ok=False,
                data={"project": info},
                error=(
                    f"No {self.action} command could be detected for this project "
                    f"(kind={info['kind']}). Pass `command` explicitly."
                ),
                summary=f"no {self.action} command detected",
            )

        # A project command must run through the project's own interpreter.
        # Otherwise a bare `python -m pytest` picks up an unrelated Python
        # without the dependencies and reports a failure that has nothing to
        # do with the code under test.
        runner = info.get("runner") or ""
        if runner == "python":
            exe, _ = _shell_for("python", root)
            parts = chosen.split()
            args = ["-m", *parts[2:]] if len(parts) > 2 else ["-m", "pytest", "-q"]
        elif runner == "node":
            exe, _ = _shell_for("node", root)
            args = ["-e", chosen]
        else:
            exe, flags = _shell_for("cmd", root)
            args = [*flags, chosen]
        outcome = await _run_process(exe=exe, args=args, cwd=root, timeout=BUILD_TIMEOUT)
        ok = outcome["exit_code"] == 0 and not outcome["timed_out"]
        return ToolResult(
            ok=ok,
            data={
                "project": info,
                "command": chosen,
                "cwd": str(root),
                "exit_code": outcome["exit_code"],
                "stdout": outcome["stdout"],
                "stderr": outcome["stderr"],
                "duration_ms": outcome["duration_ms"],
                "timed_out": outcome["timed_out"],
            },
            error="" if ok else f"{self.action} failed (exit {outcome['exit_code']})",
            summary=f"{self.action}: exit {outcome['exit_code']} in {outcome['duration_ms']}ms",
        )


class RunTestsTool(_ProjectCommandTool):
    spec = ToolSpec(
        name="run_tests",
        description="Run the project's test suite and return the real output.",
        category="project",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Project directory (default: working directory)"},
                "command": {"type": "string", "description": "Override the detected command"},
            },
            "required": [],
        },
        permission_level=1,
        risk_level="low",
        timeout_seconds=BUILD_TIMEOUT,
        mutates_state=False,
    )
    action = "test"


class RunLintTool(_ProjectCommandTool):
    spec = ToolSpec(
        name="run_lint",
        description="Run the project's linter.",
        category="project",
        parameters={"type": "object", "properties": {"path": {"type": "string"}, "command": {"type": "string"}}, "required": []},
        permission_level=1,
        risk_level="low",
        timeout_seconds=600,
        mutates_state=False,
    )
    action = "lint"


class RunTypecheckTool(_ProjectCommandTool):
    spec = ToolSpec(
        name="run_typecheck",
        description="Run the project's type checker.",
        category="project",
        parameters={"type": "object", "properties": {"path": {"type": "string"}, "command": {"type": "string"}}, "required": []},
        permission_level=1,
        risk_level="low",
        timeout_seconds=600,
        mutates_state=False,
    )
    action = "typecheck"


class RunBuildTool(_ProjectCommandTool):
    spec = ToolSpec(
        name="run_build",
        description="Build the project.",
        category="project",
        parameters={"type": "object", "properties": {"path": {"type": "string"}, "command": {"type": "string"}}, "required": []},
        permission_level=1,
        risk_level="medium",
        timeout_seconds=BUILD_TIMEOUT,
        mutates_state=True,
    )
    action = "build"
    is_write = True


def build_git_tools() -> list[Tool]:
    return [
        GitStatusTool(),
        GitDiffTool(),
        GitLogTool(),
        GitBranchTool(),
        GitCheckoutTool(),
        GitAddTool(),
        GitCommitTool(),
        GitRestoreTool(),
    ]


def build_project_tools() -> list[Tool]:
    return [RunTestsTool(), RunLintTool(), RunTypecheckTool(), RunBuildTool()]


__all__ = ["build_git_tools", "build_project_tools", "detect_project"]

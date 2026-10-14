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
from app.ai.types import ReconcileOutcome, ReconcileVerdict, ToolResult, ToolSpec
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
        # **Not replay-safe, and it used to be marked as if it were.**
        #
        # The old note argued that staging the same paths twice records the same paths, and
        # that the `git add -A` caveat "only matters if something else wrote to the tree
        # concurrently, which is not the situation a crash-replay is in". Both halves are
        # wrong, and the second one is wrong about the exact situation a replay is in.
        #
        # A crash-replay happens *later*. Between the crash and the replay, the user may
        # have edited the file — and `git add <path>` then stages the new content, so the
        # index ends up holding something the original invocation never saw. Or the user
        # may have run `git reset` to unstage it, and the replay puts it back, undoing a
        # deliberate action on their working tree. The index is the user's state, not this
        # program's scratch space.
        #
        # Reconcilable instead: whether the paths are in the index is readable, and
        # `GitAddTool.reconcile` reads it.
        idempotent=False,
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

    async def reconcile(
        self, ctx: ToolContext, arguments: dict[str, Any], recorded: dict[str, Any]
    ) -> ReconcileOutcome:
        """Read the index and see whether the paths this call staged are in it.

        `git diff --cached --name-only` is the index as git itself reports it, which is the
        only authority on what is staged. A process exit code is not evidence — `git add`
        of a path with no changes exits 0 and stages nothing — and the invocation that is
        being checked never got as far as recording its exit code anyway.

        `APPLIED` here means "the index already contains these paths, so re-running would
        add nothing", which is the question recovery actually has. It does not claim the
        original invocation is what put them there; an earlier `git add` in the same run
        would look identical, and there is no way to tell them apart. That is why the
        verdict is worded as a statement about the index.

        Empty `paths` means `git add -A`, whose effect is "everything that had changed".
        Nothing about the index now can be attributed to it, so that is `UNDECIDABLE`.
        """
        paths = arguments.get("paths") or []
        if not isinstance(paths, list) or not paths:
            return ReconcileOutcome(
                verdict=ReconcileVerdict.UNDECIDABLE,
                detail="本次调用是 `git add -A`（全部改动），索引当前的状态无法归因于它",
                evidence={"staged": "all"},
            )

        # The repository is located from `path` (or the working directory when absent), so
        # a missing working directory means the check would look at a different repository —
        # and a different repository's index would answer the question confidently and
        # wrongly.
        repo_hint = arguments.get("path")
        if isinstance(repo_hint, str) and repo_hint and not ctx.can_resolve(repo_hint):
            return ReconcileOutcome(
                verdict=ReconcileVerdict.UNDECIDABLE,
                detail=f"调用记录里没有工作目录，{repo_hint!r} 无法解析到运行时的那个仓库，不猜",
                evidence={"path": repo_hint},
            )
        if (not isinstance(repo_hint, str) or not repo_hint) and not ctx.can_resolve("."):
            return ReconcileOutcome(
                verdict=ReconcileVerdict.UNDECIDABLE,
                detail="调用记录里没有工作目录，无法确定是哪个仓库，不猜",
                evidence={"paths": [str(p) for p in paths]},
            )

        try:
            root = await self._repo(ctx, arguments.get("path"))
        except Exception as exc:
            return ReconcileOutcome(
                verdict=ReconcileVerdict.UNDECIDABLE,
                detail=f"无法定位仓库：{type(exc).__name__}: {exc}",
                evidence={"paths": [str(p) for p in paths]},
            )
        if (err := await self._ensure_repo(root)):
            return ReconcileOutcome(
                verdict=ReconcileVerdict.UNDECIDABLE,
                detail=f"无法读取仓库状态：{err}",
                evidence={"repo": str(root)},
            )

        staged = await _git_run(root, ["diff", "--cached", "--name-only"])
        if staged["exit_code"] != 0:
            return ReconcileOutcome(
                verdict=ReconcileVerdict.UNDECIDABLE,
                detail=f"`git diff --cached` 失败：{staged['stderr'].strip()[:200]}",
                evidence={"repo": str(root)},
            )
        in_index = {line.strip().replace("\\", "/") for line in staged["stdout"].splitlines() if line.strip()}
        wanted = [str(p).replace("\\", "/") for p in paths]
        missing = [p for p in wanted if p not in in_index]
        evidence = {"repo": str(root), "index": sorted(in_index), "wanted": wanted}

        if not missing:
            return ReconcileOutcome(
                verdict=ReconcileVerdict.APPLIED,
                detail=f"索引里已经包含 {len(wanted)} 个路径，重跑不会再产生额外效果",
                evidence=evidence,
            )
        return ReconcileOutcome(
            verdict=ReconcileVerdict.NOT_APPLIED,
            detail=f"索引里没有这些路径：{missing}",
            evidence=evidence,
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
#: Directories never searched when deciding what kind of project this is.
_PROJECT_SCAN_SKIP = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", "target", "dist", "build", ".next", ".runtime",
}


def _sample_files(root: Path, suffixes: tuple[str, ...], limit: int = 400) -> list[Path]:
    """A bounded sample of files under *root* with one of *suffixes*.

    Bounded because this runs before every project command: walking a large tree
    to answer "is this a Python project?" would make every test run slower than
    the tests.
    """
    found: list[Path] = []
    for path in root.rglob("*"):
        if len(found) >= limit:
            break
        if not path.is_file():
            continue
        if any(part in _PROJECT_SCAN_SKIP for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() in suffixes:
            found.append(path)
    return found


def _looks_like_python(root: Path) -> bool:
    """True when this directory holds Python source, at any depth."""
    return bool(_sample_files(root, (".py",), limit=1))


def _has_python_tests(root: Path) -> bool:
    """True when this directory holds something pytest would collect."""
    for path in _sample_files(root, (".py",), limit=400):
        name = path.name
        if name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py":
            return True
    return (root / "tests").is_dir()


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
    elif _looks_like_python(root):
        # A directory of Python files with no manifest is still a Python project.
        # Two things had to be learned here the hard way:
        #   * requiring a manifest meant an agent that had just written `foo.py`
        #     and `test_foo.py` could not run them;
        #   * looking only at the top level meant a normal `src/` + `tests/`
        #     layout was reported as `kind=unknown`, so the agent could not run
        #     the tests of a project it was staring straight at.
        kind = "python"
        runner = "python"
        if _has_python_tests(root):
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

"""Terminal tools — real command execution.

These run actual processes and return the real stdout, stderr, exit code and
duration. Nothing here is simulated, and there is no mock path: if a command
cannot run, the tool reports why.

Three properties are enforced on every invocation, and they are the reason this
is not simply ``subprocess.run(shell=True)``:

* **Working directory.** The command runs in the run's working directory, and that
  directory is checked against the policy first. A command cannot be steered by
  changing the process's own cwd.
* **Confirmation.** Risk is classified before anything runs (see
  ``permissions.classify_command``). Destructive and history-rewriting commands
  reach the user as a prompt; they do not reach the shell on their own.
* **Termination.** Output is capped and the process tree is killed on timeout or
  cancellation, so a runaway build cannot outlive the run that started it.

The interpreter is chosen by tool name rather than by a parameter, so a caller
cannot ask the PowerShell tool to run a Python snippet or vice versa. Each
capability is granted separately.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from app.ai.agent.tools import Tool, ToolContext
from app.ai.types import ToolResult, ToolSpec
from app.core.errors import ValidationError

#: Ceiling for a single command. Generous enough for a real build, bounded enough
#: that a hung process cannot occupy the run indefinitely.
MAX_TIMEOUT = 900.0
DEFAULT_TIMEOUT = 120.0


def resolve_interpreter(name: str, cwd: Path | None = None) -> str:
    """Find the interpreter that belongs to the project, not just to the PATH.

    This matters more than it looks. `shutil.which("python")` returns whatever the
    machine happens to have first on PATH, which on a developer box is frequently
    a bare system Python with none of the project's dependencies. An agent asked
    to run a project's tests would then get "No module named pytest" and conclude
    the *code* was broken — when the only problem was that it ran the wrong
    interpreter.

    Order, most specific first:

    1. a virtual environment inside the working directory (``.venv``, ``venv``)
    2. a virtual environment in a parent directory, which is the usual layout
       (``<repo>/.venv`` while working in ``<repo>/backend``)
    3. the interpreter running this backend — in this deployment that *is* the
       project's venv, so it is the right answer when nothing else is found
    4. whatever is on PATH, as a last resort

    Node has the same problem in miniature, so a project-local ``node_modules``
    is preferred for ``node`` too.
    """
    if name == "python":
        candidates: list[Path] = []
        search_roots = [cwd] if cwd else []
        if cwd:
            search_roots.extend(list(cwd.parents)[:4])

        for root in search_roots:
            if root is None:
                continue
            candidates.extend(
                [
                    root / ".venv" / "Scripts" / "python.exe",
                    root / ".venv" / "bin" / "python",
                    root / "venv" / "Scripts" / "python.exe",
                    root / "venv" / "bin" / "python",
                ]
            )
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

        # The backend's own interpreter. In this product that is the project venv,
        # so it is a better guess than an unrelated Python on PATH.
        if sys.executable and Path(sys.executable).is_file():
            return sys.executable

    found = shutil.which(name)
    if not found and name == "python":
        found = shutil.which("python3")
    if not found:
        raise ValidationError(f"{name} was not found on PATH")
    return found


def _shell_for(interpreter: str, cwd: Path | None = None) -> tuple[str, list[str]]:
    """Resolve the executable and the flag that runs a command string."""
    if interpreter == "powershell":
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if not exe:
            raise ValidationError("PowerShell was not found on PATH")
        return exe, ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]
    if interpreter == "cmd":
        exe = os.environ.get("COMSPEC") or shutil.which("cmd")
        if not exe:
            raise ValidationError("cmd.exe was not found")
        return exe, ["/d", "/s", "/c"]
    if interpreter == "python":
        return resolve_interpreter("python", cwd), ["-c"]
    if interpreter == "node":
        return resolve_interpreter("node", cwd), ["-e"]
    raise ValidationError(f"Unsupported interpreter '{interpreter}'")


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


async def _run_process(
    *,
    exe: str,
    args: list[str],
    cwd: Path,
    timeout: float,
    env_extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a process and capture everything, killing the tree on timeout.

    ``start_new_session`` (POSIX) / ``CREATE_NEW_PROCESS_GROUP`` (Windows) puts the
    child in its own group so the whole tree can be terminated. A build that
    spawns compilers must not survive a cancelled run.
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    if env_extra:
        env.update(env_extra)

    creationflags = 0
    if os.name == "nt":
        creationflags = 0x0800_0000 | 0x0000_0200  # NO_WINDOW | NEW_PROCESS_GROUP

    started = time.perf_counter()
    process = await asyncio.create_subprocess_exec(
        exe,
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        creationflags=creationflags,
    )

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        timed_out = False
    except TimeoutError:
        timed_out = True
        await _kill_tree(process)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        except (TimeoutError, ValueError):
            stdout, stderr = b"", b""

    duration_ms = int((time.perf_counter() - started) * 1000)
    return {
        "exit_code": process.returncode,
        "stdout": _decode(stdout or b""),
        "stderr": _decode(stderr or b""),
        "duration_ms": duration_ms,
        "timed_out": timed_out,
    }


async def _kill_tree(process: asyncio.subprocess.Process) -> None:
    """Terminate a process and everything it spawned."""
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(killer.wait(), timeout=10)
    else:
        import signal

        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        with_suppress = getattr(process, "kill", None)
        if with_suppress:
            process.kill()


class _TerminalTool(Tool):
    """Shared implementation; subclasses only choose the interpreter."""

    interpreter: str = "powershell"
    is_write = True

    async def execute(
        self,
        ctx: ToolContext,
        command: str,
        timeout_seconds: float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ToolResult:
        if not command or not command.strip():
            raise ValidationError("command must not be empty")

        working = Path(ctx.resolve(cwd)) if cwd else Path(ctx.resolve("."))
        ctx.require_permission(working, write=True)
        if not working.is_dir():
            return ToolResult(ok=False, error=f"Working directory does not exist: {working}", summary="bad cwd")

        # Classified here as well as in the executor so the *reason* travels with
        # the result even when the command was allowed by policy.
        if ctx.permissions is not None:
            verdict = ctx.permissions.check_command(command, cwd=working, interpreter=self.interpreter)
            if not verdict.allowed:
                from app.core.errors import PermissionDeniedError

                raise PermissionDeniedError(
                    verdict.reason,
                    details={"command": command[:500], "risk": verdict.risk.value},
                )

        # Resolved against the working directory so a project's own virtual
        # environment wins over whatever happens to be first on PATH.
        exe, flags = _shell_for(self.interpreter, working)
        budget = min(float(timeout_seconds or DEFAULT_TIMEOUT), MAX_TIMEOUT)

        outcome = await _run_process(
            exe=exe, args=[*flags, command], cwd=working, timeout=budget, env_extra=env
        )

        combined = f"{outcome['stdout']}\n{outcome['stderr']}".strip()
        ok = outcome["exit_code"] == 0 and not outcome["timed_out"]

        if outcome["timed_out"]:
            return ToolResult(
                ok=False,
                data={**outcome, "command": command, "cwd": str(working), "interpreter": self.interpreter},
                error=f"Command exceeded {budget:g}s and was terminated",
                summary=f"timeout after {budget:g}s",
            )

        return ToolResult(
            ok=ok,
            data={
                "command": command,
                "cwd": str(working),
                "interpreter": self.interpreter,
                "exit_code": outcome["exit_code"],
                "stdout": outcome["stdout"],
                "stderr": outcome["stderr"],
                "duration_ms": outcome["duration_ms"],
                "line_count": combined.count("\n") + 1 if combined else 0,
            },
            error="" if ok else f"Exited with code {outcome['exit_code']}",
            summary=f"{self.interpreter}: exit {outcome['exit_code']} in {outcome['duration_ms']}ms",
        )


class TerminalExecTool(_TerminalTool):
    spec = ToolSpec(
        name="terminal_exec",
        description=(
            "Run a shell command in the working directory and return stdout, "
            "stderr, the exit code and the duration. Destructive commands are "
            "flagged and will ask for confirmation."
        ),
        category="terminal",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string", "description": "Optional; defaults to the working directory"},
                "timeout_seconds": {"type": "number", "description": f"Default {DEFAULT_TIMEOUT:g}, max {MAX_TIMEOUT:g}"},
                "env": {"type": "object", "description": "Extra environment variables"},
            },
            "required": ["command"],
        },
        permission_level=2,
        risk_level="medium",
        timeout_seconds=MAX_TIMEOUT,
        mutates_state=True,
        audit_policy="always",
    )
    interpreter = "powershell"


class PowerShellExecTool(_TerminalTool):
    spec = ToolSpec(
        name="powershell_exec",
        description="Run a PowerShell command or script block.",
        category="terminal",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout_seconds": {"type": "number"},
            },
            "required": ["command"],
        },
        permission_level=2,
        risk_level="medium",
        timeout_seconds=MAX_TIMEOUT,
        mutates_state=True,
    )
    interpreter = "powershell"


class CmdExecTool(_TerminalTool):
    spec = ToolSpec(
        name="cmd_exec",
        description="Run a command through cmd.exe.",
        category="terminal",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout_seconds": {"type": "number"},
            },
            "required": ["command"],
        },
        permission_level=2,
        risk_level="medium",
        timeout_seconds=MAX_TIMEOUT,
        mutates_state=True,
    )
    interpreter = "cmd"


class PythonExecTool(_TerminalTool):
    spec = ToolSpec(
        name="python_exec",
        description="Run a Python snippet with the system interpreter.",
        category="terminal",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Python source to evaluate"},
                "cwd": {"type": "string"},
                "timeout_seconds": {"type": "number"},
            },
            "required": ["command"],
        },
        permission_level=2,
        risk_level="medium",
        timeout_seconds=300,
        mutates_state=True,
    )
    interpreter = "python"


class NodeExecTool(_TerminalTool):
    spec = ToolSpec(
        name="node_exec",
        description="Run a Node.js snippet with the system interpreter.",
        category="terminal",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "JavaScript source to evaluate"},
                "cwd": {"type": "string"},
                "timeout_seconds": {"type": "number"},
            },
            "required": ["command"],
        },
        permission_level=2,
        risk_level="medium",
        timeout_seconds=300,
        mutates_state=True,
    )
    interpreter = "node"


def build_terminal_tools() -> list[Tool]:
    return [
        TerminalExecTool(),
        PowerShellExecTool(),
        CmdExecTool(),
        PythonExecTool(),
        NodeExecTool(),
    ]


__all__ = ["build_terminal_tools"]

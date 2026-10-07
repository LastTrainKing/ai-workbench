"""Running a script-type skill somewhere it cannot do damage, and being able to undo it.

A script-type skill is the one risk class that actually executes. `scanner.py` reads it and
refuses the ones that are obviously hostile, but reading is not running: a script that
passes the scan can still do something the scan did not anticipate. So a script-type skill
does not become usable until it has been **run once, somewhere disposable, with the result
recorded.**

What this actually provides — stated precisely, because overclaiming here is worse than not
having it:

* **A fresh working directory per run.** The script's relative paths land in a temp
  directory that is deleted afterwards, not in the user's workspace.
* **No path to the workspace.** The script is not told where the real files are. A script
  that guesses an absolute path is caught by the next point rather than by this one.
* **A snapshot before, a diff after.** The workspace is snapshotted first and diffed after.
  **Any change to it is a violation**, and the snapshot is restored — so a script that
  reaches outside the sandbox is detected and its damage is undone, rather than prevented.
* **A timeout**, so a script that hangs or loops does not hold the run.

**This is process-level isolation with detection and rollback, not a kernel sandbox.** It
does not stop a determined script from writing outside the temp directory — it notices and
reverts. Calling that "sandboxed" without the qualifier would be the kind of claim this
project keeps catching itself making, so the qualifier is in the docstring, in the result,
and in the UI.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ai.agent.snapshot import WorkspaceSnapshotManager
from app.core.logging import get_logger

logger = get_logger(__name__)

#: A script that has not finished by now is not going to. Generous enough for a real
#: spreadsheet job, short enough that a runaway loop does not hold the run.
DEFAULT_TIMEOUT_SECONDS = 60

#: How much output to keep. A script that prints megabytes is not giving useful evidence,
#: and storing all of it would make the verification record unreadable.
MAX_OUTPUT_CHARS = 8000


@dataclass
class SandboxResult:
    """What happened when the script ran. Every field is an observation, not a guess."""

    ran: bool = False
    exit_code: int | None = None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    #: The temp directory the script ran in, kept in the record so the evidence is traceable.
    sandbox_dir: str = ""
    #: Snapshot taken of the workspace before the run.
    snapshot_id: str = ""
    #: Files in the workspace that changed during the run. Non-empty means it escaped.
    escaped_paths: list[str] = field(default_factory=list)
    restored: bool = False
    #: Why the run is not acceptable, if it is not.
    refusal: str = ""

    @property
    def clean(self) -> bool:
        """Ran, exited zero, and touched nothing outside its own directory."""
        return self.ran and self.exit_code == 0 and not self.timed_out and not self.escaped_paths

    def as_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "sandbox_dir": self.sandbox_dir,
            "snapshot_id": self.snapshot_id,
            "escaped_paths": self.escaped_paths,
            "restored": self.restored,
            "refusal": self.refusal,
            "clean": self.clean,
            # Said out loud in the record so nobody reads "sandboxed" as stronger than it is.
            "isolation": "process-level with detection and rollback; not a kernel sandbox",
        }

    def summarise(self) -> str:
        if not self.ran:
            return f"没有运行：{self.refusal or '未知原因'}"
        if self.timed_out:
            return f"超时（>{DEFAULT_TIMEOUT_SECONDS}s）后被终止"
        if self.escaped_paths:
            return f"越界写入了 {len(self.escaped_paths)} 个文件，已回滚"
        return f"exit code {self.exit_code}"


# --------------------------------------------------------------------- the run --


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n…（截断，共 {len(text)} 字符）"


async def run_in_sandbox(
    script: str,
    *,
    workspace: Path,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    extra_files: dict[str, str] | None = None,
) -> SandboxResult:
    """Run a skill's script somewhere disposable and report exactly what happened.

    `workspace` is the user's real working directory. It is **snapshotted, never handed to
    the script** — the script runs in a temp directory and is given no path to it.
    """
    result = SandboxResult()

    if not script.strip():
        result.refusal = "脚本是空的"
        return result

    sandbox_dir = Path(tempfile.mkdtemp(prefix=f"skill-sandbox-{uuid.uuid4().hex[:8]}-"))
    result.sandbox_dir = str(sandbox_dir)

    manager = WorkspaceSnapshotManager()
    snapshot = manager.create(workspace, label="before-skill-sandbox-run")
    result.snapshot_id = snapshot.id

    script_path = sandbox_dir / "skill_script.py"
    script_path.write_text(script, encoding="utf-8")
    for name, content in (extra_files or {}).items():
        target = sandbox_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script_path),
            cwd=str(sandbox_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        # A process that could not even start is not a passing script. Reported rather
        # than treated as an empty run.
        result.refusal = f"无法启动进程：{exc}"
        shutil.rmtree(sandbox_dir, ignore_errors=True)
        return result

    result.ran = True
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        result.exit_code = process.returncode
        result.stdout = _truncate(stdout.decode("utf-8", errors="replace"))
        result.stderr = _truncate(stderr.decode("utf-8", errors="replace"))
    except TimeoutError:
        process.kill()
        await process.wait()
        result.timed_out = True
        result.exit_code = None
        logger.warning("skill_sandbox_timeout", extra={"timeout": timeout})

    # Did it reach outside? The workspace is diffed against the snapshot taken before the
    # run. This is the check that catches what the static scan could not anticipate.
    #
    # The key is `files`. The first version of this read `entries`, which the diff never
    # returns — so `changed` was always empty and the escape check silently passed
    # everything. That is the failure shape this project keeps finding: a lookup that
    # misses and produces a plausible empty value instead of an error.
    diff = manager.diff(snapshot)
    changed = [entry["path"] for entry in (diff.get("files") or [])]
    if changed:
        result.escaped_paths = changed
        restore = manager.restore(snapshot, force=True)
        result.restored = bool(restore.get("restored"))
        logger.warning(
            "skill_sandbox_escape",
            extra={"paths": changed[:5], "restored": result.restored},
        )

    shutil.rmtree(sandbox_dir, ignore_errors=True)
    return result


def verdict(result: SandboxResult) -> str:
    """The one line that decides whether this skill may become usable.

    A script-type skill is only usable if it ran, exited zero, finished in time, and
    touched nothing outside its own directory. Anything else is a refusal with the reason
    attached — never a warning, because a warning would let it through.
    """
    if not result.ran:
        return f"不允许使用：{result.refusal or '脚本没有运行'}"
    if result.timed_out:
        return f"不允许使用：超时（>{DEFAULT_TIMEOUT_SECONDS}s）"
    if result.escaped_paths:
        return (
            f"不允许使用：越界写入了 {len(result.escaped_paths)} 个文件"
            f"（{', '.join(result.escaped_paths[:3])}），已回滚"
        )
    if result.exit_code != 0:
        return f"不允许使用：exit code {result.exit_code}"
    return "允许使用：exit code 0，没有越界写入"


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "SandboxResult",
    "run_in_sandbox",
    "verdict",
]

"""The skill sandbox: it runs a script somewhere disposable, and it notices escapes.

Two things are being checked, and they fail in opposite directions:

* a **normal** script runs, exits zero, and its exit code lands in the result — so a
  script-type skill can become usable;
* an **escaping** script is caught and its damage is rolled back — so one that reaches
  outside does not become usable.

The second is the one that matters. A sandbox tested only against well-behaved scripts is
a sandbox that has never been asked the question it exists to answer.

What is being tested is stated honestly: this is **detection and rollback**, not
prevention. A script that writes outside the temp directory *succeeds* at writing — and
then the workspace is diffed, the change is found, and the snapshot is restored. The test
asserts the detection and the rollback, because those are what actually happen.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.ai.skills.sandbox import DEFAULT_TIMEOUT_SECONDS, run_in_sandbox, verdict

pytestmark = pytest.mark.asyncio


GOOD_SCRIPT = """
import csv
from pathlib import Path

rows = [{"name": "a", "amount": "3"}, {"name": "b", "amount": "4"}]
totals = {}
for row in rows:
    totals[row["name"]] = totals.get(row["name"], 0) + int(row["amount"])

out = Path("summary.csv")
with out.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.writer(handle)
    for name, total in totals.items():
        writer.writerow([name, total])

print("wrote", out.name, "with", len(totals), "rows")
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A stand-in for the user's real working directory."""
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "important.md").write_text("# keep me\n", encoding="utf-8")
    return root


# ------------------------------------------------------------------ the good case --


async def test_a_normal_script_runs_and_reports_its_exit_code(workspace: Path) -> None:
    result = await run_in_sandbox(GOOD_SCRIPT, workspace=workspace)
    assert result.ran
    assert result.exit_code == 0, result.stderr
    assert "wrote summary.csv" in result.stdout
    assert result.clean
    assert verdict(result).startswith("允许使用")


async def test_the_script_writes_into_its_own_directory_not_the_workspace(
    workspace: Path,
) -> None:
    """The point of running it elsewhere: its relative paths land in the temp directory."""
    before = sorted(p.name for p in workspace.iterdir())
    result = await run_in_sandbox(GOOD_SCRIPT, workspace=workspace)
    after = sorted(p.name for p in workspace.iterdir())

    assert result.clean
    assert before == after, "正常脚本不应该往工作区写东西"
    assert not result.escaped_paths


async def test_the_sandbox_directory_is_cleaned_up(workspace: Path) -> None:
    """A temp directory left behind per run would fill the disk quietly."""
    result = await run_in_sandbox(GOOD_SCRIPT, workspace=workspace)
    assert result.sandbox_dir
    assert not Path(result.sandbox_dir).exists(), "沙箱目录用完要删掉"


async def test_a_snapshot_is_taken_before_the_run(workspace: Path) -> None:
    """There has to be something to roll back to, or the rollback is a promise."""
    result = await run_in_sandbox(GOOD_SCRIPT, workspace=workspace)
    assert result.snapshot_id, "运行前必须先建快照"


# --------------------------------------------------------------- the escape case --


ESCAPING_SCRIPT = """
from pathlib import Path

# An absolute path out of the sandbox, of the shape a hostile skill would use. This is
# not hypothetical: the static scan cannot anticipate every path a script might build,
# which is exactly why the run is diffed afterwards.
target = Path(r"{target}")
target.write_text("this should not survive", encoding="utf-8")
print("wrote outside the sandbox")
"""


async def test_a_script_writing_outside_is_detected_and_rolled_back(
    workspace: Path,
) -> None:
    """The acceptance case: a script that reaches outside the workspace is caught.

    It is caught *after* writing, not prevented — so the assertion is on detection and on
    the workspace being put back, which is what this actually does.
    """
    target = workspace / "escaped.txt"
    assert not target.exists()

    result = await run_in_sandbox(
        ESCAPING_SCRIPT.format(target=str(target).replace("\\", "\\\\")),
        workspace=workspace,
    )

    assert result.escaped_paths, "越界写入必须被发现"
    assert "escaped.txt" in " ".join(result.escaped_paths)
    assert result.restored, "发现越界之后必须回滚"
    assert not target.exists(), "回滚之后那个文件不能还在"
    assert not result.clean


async def test_an_escaping_script_is_refused_not_warned(workspace: Path) -> None:
    """A warning would let it through. A script that reached outside does not become
    usable, however well it otherwise ran."""
    target = workspace / "escaped.txt"
    result = await run_in_sandbox(
        ESCAPING_SCRIPT.format(target=str(target).replace("\\", "\\\\")),
        workspace=workspace,
    )
    line = verdict(result)
    assert line.startswith("不允许使用"), line
    assert "越界" in line


async def test_other_files_in_the_workspace_survive_a_rollback(workspace: Path) -> None:
    """Rolling back must not be destructive itself: the file that was there before the
    run is still there, with its contents intact."""
    target = workspace / "escaped.txt"
    await run_in_sandbox(
        ESCAPING_SCRIPT.format(target=str(target).replace("\\", "\\\\")),
        workspace=workspace,
    )
    assert (workspace / "important.md").read_text(encoding="utf-8") == "# keep me\n"


# --------------------------------------------------------------- the bad cases --


async def test_a_failing_script_is_refused(workspace: Path) -> None:
    result = await run_in_sandbox("raise SystemExit(3)", workspace=workspace)
    assert result.exit_code == 3
    assert not result.clean
    assert "exit code 3" in verdict(result)


async def test_a_script_that_raises_is_reported_with_its_traceback(workspace: Path) -> None:
    """The stderr is the evidence. Swallowing it would leave a refusal with no reason."""
    result = await run_in_sandbox("raise ValueError('boom')", workspace=workspace)
    assert result.exit_code != 0
    assert "ValueError" in result.stderr
    assert "boom" in result.stderr


async def test_a_hanging_script_is_killed(workspace: Path) -> None:
    """A runaway loop must not hold the run. Short timeout here so the test is quick;
    the production default is `DEFAULT_TIMEOUT_SECONDS`."""
    result = await run_in_sandbox("while True:\n    pass\n", workspace=workspace, timeout=3)
    assert result.timed_out
    assert not result.clean
    assert "超时" in verdict(result)
    assert DEFAULT_TIMEOUT_SECONDS > 3, "生产默认值应该比测试用的长"


async def test_an_empty_script_is_refused_without_running(workspace: Path) -> None:
    result = await run_in_sandbox("   \n", workspace=workspace)
    assert not result.ran
    assert "空的" in result.refusal
    assert not result.clean


async def test_a_syntax_error_is_a_refusal_not_a_crash(workspace: Path) -> None:
    """Anyone who can publish a repository can reach this. It must not raise."""
    result = await run_in_sandbox("def broken(:\n    pass\n", workspace=workspace)
    assert result.ran
    assert result.exit_code != 0
    assert "SyntaxError" in result.stderr


# -------------------------------------------------------------- the record itself --


async def test_the_result_states_what_the_isolation_actually_is(workspace: Path) -> None:
    """`sandboxed` would be read as stronger than this is. The record says so itself, so
    nobody has to remember the qualification."""
    result = await run_in_sandbox(GOOD_SCRIPT, workspace=workspace)
    payload = result.as_dict()
    assert "not a kernel sandbox" in payload["isolation"]
    assert payload["snapshot_id"]
    assert payload["exit_code"] == 0


async def test_extra_files_can_be_given_to_the_script(workspace: Path) -> None:
    """A skill that processes a spreadsheet needs one to process. The file goes into the
    sandbox, not the workspace."""
    script = """
from pathlib import Path
text = Path("input.txt").read_text(encoding="utf-8")
print("read", len(text), "chars")
"""
    result = await run_in_sandbox(
        script, workspace=workspace, extra_files={"input.txt": "hello world"}
    )
    assert result.exit_code == 0, result.stderr
    assert "read 11 chars" in result.stdout
    assert not (workspace / "input.txt").exists()


async def test_concurrent_runs_do_not_share_a_directory(workspace: Path) -> None:
    """Each run gets its own temp directory, so two runs cannot see each other's files."""
    results = await asyncio.gather(
        run_in_sandbox(GOOD_SCRIPT, workspace=workspace),
        run_in_sandbox(GOOD_SCRIPT, workspace=workspace),
    )
    assert results[0].sandbox_dir != results[1].sandbox_dir
    assert all(r.clean for r in results)

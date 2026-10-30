"""Checking the world instead of guessing, and the boundary of what can be checked.

`UNKNOWN` is the honest state after an interruption: the tool may or may not have had its
effect. These tests are about the next step — whether a later process can *settle* it by
looking, and what happens when it cannot.

Two properties are load-bearing and each has its own test:

* **Nothing is re-run.** A reconciliation pass reads; it does not write to the target. The
  tests assert the target is byte-identical afterwards, because a "check" that quietly
  re-applied the operation would look exactly like a successful one from the database.
* **A check that cannot tell says so.** `file_write` over a file whose content has changed
  is the case that matters: the intended bytes are absent, which looks like "did not
  happen", but a later write would look identical. Reporting `NOT_APPLIED` there would
  invite a replay that destroys whatever is in the file now.
"""

from __future__ import annotations

import hashlib
import subprocess
import uuid
from pathlib import Path

import pytest

from app.ai.agent.tools import ToolContext
from app.ai.tools.permissions import AgentPolicy
from app.ai.tools.reconcile import context_for, reconcile_call, reconcile_unknown_calls
from app.ai.tools.registry import build_computer_registry, tool_for
from app.ai.types import ReconcileVerdict
from app.domain.enums import ToolCallStatus
from app.infrastructure.database import session_scope
from app.models.ai import AIRun, ToolCall


def _write_exact(path: Path, text: str) -> None:
    """Write the bytes `file_write` would have written.

    **Not `write_text`.** On Windows `write_text` translates LF to CRLF, so a test that
    set the file up that way would be comparing a 7-byte file against a 6-byte intent and
    reporting `UNDECIDABLE` for a call that had in fact landed. The tool uses
    `write_bytes(data)` and does no translation, so the fixture has to match the tool — the
    first version of these tests failed for exactly that reason, and the failure was in the
    fixture rather than in the check.
    """
    path.write_bytes(text.encode("utf-8"))


def _registry():
    return build_computer_registry(AgentPolicy.from_settings(mode="autonomous", level=3))


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext(session=None, owner_id=uuid.uuid4(), working_directory=str(workspace))


def _call(tool: str, arguments: dict, *, working_directory: str = "") -> ToolCall:
    """An invocation row in the state an interrupted one is left in."""
    return ToolCall(
        ai_run_id=uuid.uuid4(),
        step_index=1,
        tool_name=tool,
        arguments=arguments,
        result={},
        status=ToolCallStatus.UNKNOWN.value,
        error_message="",
        latency_ms=0,
        mutates_state=True,
        invocation_id=f"inv_{uuid.uuid4().hex}",
        working_directory=working_directory or None,
    )


async def _check(tool: str, arguments: dict, workspace: Path) -> tuple[ReconcileVerdict, str]:
    call = _call(tool, arguments, working_directory=str(workspace))
    report = await reconcile_call(call, registry=_registry(), ctx=_ctx(workspace))
    return report.verdict, report.detail


# --------------------------------------------------------------------------- #
# file_write
# --------------------------------------------------------------------------- #
async def test_a_file_holding_the_intended_bytes_is_applied(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    _write_exact(target, "hello\n")
    verdict, _detail = await _check(
        "file_write", {"path": "out.txt", "content": "hello\n"}, tmp_path
    )
    assert verdict is ReconcileVerdict.APPLIED


async def test_a_missing_file_is_not_applied(tmp_path: Path) -> None:
    verdict, detail = await _check("file_write", {"path": "gone.txt", "content": "x"}, tmp_path)
    assert verdict is ReconcileVerdict.NOT_APPLIED
    assert "gone.txt" in detail


async def test_a_file_with_different_content_is_undecidable_not_not_applied(tmp_path: Path) -> None:
    """The one that matters.

    The intended bytes are not there. That is consistent with "the write never happened"
    **and** with "the write happened and something overwrote it afterwards", and those two
    have opposite safe responses. Only one of them is knowable, so the answer is that it is
    not knowable.
    """
    target = tmp_path / "out.txt"
    _write_exact(target, "something else entirely\n")
    verdict, detail = await _check(
        "file_write", {"path": "out.txt", "content": "hello\n"}, tmp_path
    )
    assert verdict is ReconcileVerdict.UNDECIDABLE, detail
    assert verdict is not ReconcileVerdict.NOT_APPLIED


async def test_a_relative_path_without_a_recorded_working_directory_is_not_checked(
    tmp_path: Path,
) -> None:
    """A check against the wrong directory is worse than no check.

    `out.txt` resolves against the *process's* directory when no working directory is
    recorded. If a file happens to be there, the check would report a confident verdict
    about a file the invocation never mentioned.
    """
    call = _call("file_write", {"path": "out.txt", "content": "hello\n"})  # no working_directory
    _write_exact(tmp_path / "out.txt", "hello\n")
    # The context is built the way production builds it — from the row. Handing the check a
    # working directory the row does not have would be testing a state that cannot occur.
    ctx = context_for(None, call, owner_id=uuid.uuid4())
    report = await reconcile_call(call, registry=_registry(), ctx=ctx)
    assert report.verdict is ReconcileVerdict.UNDECIDABLE
    assert "工作目录" in report.detail
    assert report.status_after == ToolCallStatus.UNKNOWN.value


async def test_an_absolute_path_needs_no_working_directory(tmp_path: Path) -> None:
    target = tmp_path / "absolute.txt"
    _write_exact(target, "hi\n")
    call = _call("file_write", {"path": str(target), "content": "hi\n"})
    report = await reconcile_call(call, registry=_registry(), ctx=_ctx(tmp_path))
    assert report.verdict is ReconcileVerdict.APPLIED


# --------------------------------------------------------------------------- #
# directory_create
# --------------------------------------------------------------------------- #
async def test_an_existing_directory_is_applied(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    verdict, _detail = await _check("directory_create", {"path": "sub"}, tmp_path)
    assert verdict is ReconcileVerdict.APPLIED


async def test_a_missing_directory_is_not_applied(tmp_path: Path) -> None:
    verdict, _detail = await _check("directory_create", {"path": "absent"}, tmp_path)
    assert verdict is ReconcileVerdict.NOT_APPLIED


async def test_a_file_where_the_directory_should_be_is_undecidable(tmp_path: Path) -> None:
    _write_exact(tmp_path / "thing", "not a directory")
    verdict, detail = await _check("directory_create", {"path": "thing"}, tmp_path)
    assert verdict is ReconcileVerdict.UNDECIDABLE
    assert "不是目录" in detail


# --------------------------------------------------------------------------- #
# git_add
# --------------------------------------------------------------------------- #
def _git_repo(path: Path) -> None:
    def run(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(path),
            capture_output=True,
            check=True,
            env={
                "PATH": __import__("os").environ.get("PATH", ""),
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.com",
                "HOME": str(path),
            },
        )

    run("init", "-q")
    (path / "a.txt").write_text("a\n", encoding="utf-8")
    (path / "b.txt").write_text("b\n", encoding="utf-8")


async def test_paths_in_the_index_are_applied(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    subprocess.run(
        ["git", "add", "--", "a.txt"], cwd=str(tmp_path), capture_output=True, check=True
    )
    verdict, detail = await _check("git_add", {"paths": ["a.txt"]}, tmp_path)
    assert verdict is ReconcileVerdict.APPLIED, detail


async def test_paths_absent_from_the_index_are_not_applied(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    verdict, detail = await _check("git_add", {"paths": ["b.txt"]}, tmp_path)
    assert verdict is ReconcileVerdict.NOT_APPLIED
    assert "b.txt" in detail


async def test_staging_everything_is_undecidable(tmp_path: Path) -> None:
    """`git add -A` has no target to check, so the index cannot be attributed to it."""
    _git_repo(tmp_path)
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), capture_output=True, check=True)
    verdict, detail = await _check("git_add", {"paths": []}, tmp_path)
    assert verdict is ReconcileVerdict.UNDECIDABLE
    assert "-A" in detail


# --------------------------------------------------------------------------- #
# The default, and the boundary
# --------------------------------------------------------------------------- #
async def test_a_tool_with_no_check_is_undecidable(tmp_path: Path) -> None:
    """Twenty-odd tools have no check, and "nobody wrote one" is not "nothing happened"."""
    verdict, detail = await _check("file_append", {"path": "x.txt", "content": "y"}, tmp_path)
    assert verdict is ReconcileVerdict.UNDECIDABLE
    assert "对账" in detail


async def test_an_unknown_tool_name_is_undecidable(tmp_path: Path) -> None:
    verdict, _detail = await _check("no_such_tool", {}, tmp_path)
    assert verdict is ReconcileVerdict.UNDECIDABLE


async def test_a_check_that_raises_does_not_decide_anything(tmp_path: Path) -> None:
    """A check that could not run has established nothing, and must not report otherwise."""

    class Exploding:
        spec = type("S", (), {"name": "exploding", "mutates_state": True})()

        async def reconcile(self, ctx, arguments, recorded):
            raise RuntimeError("boom")

    class Registry:
        def get(self, name):
            return Exploding() if name == "exploding" else None

    call = _call("exploding", {}, working_directory=str(tmp_path))
    report = await reconcile_call(call, registry=Registry(), ctx=_ctx(tmp_path))
    assert report.verdict is ReconcileVerdict.UNDECIDABLE
    assert "boom" in report.detail
    assert report.status_after == ToolCallStatus.UNKNOWN.value


# --------------------------------------------------------------------------- #
# What reconciliation does to the record
# --------------------------------------------------------------------------- #
async def test_applied_sets_succeeded_but_does_not_invent_a_finish_time(tmp_path: Path) -> None:
    """`finished_at` stays NULL. The tool's own process never observed the end.

    `reconciled_at` is the column that records what happened here, and the two must not be
    collapsed: a row that claims a finish time it never had is a row that lies about the
    one thing the record exists to distinguish.
    """
    target = tmp_path / "out.txt"
    _write_exact(target, "hello\n")
    call = _call(
        "file_write", {"path": "out.txt", "content": "hello\n"}, working_directory=str(tmp_path)
    )
    call.started_at = None
    report = await reconcile_call(call, registry=_registry(), ctx=_ctx(tmp_path))
    assert report.status_after == ToolCallStatus.SUCCEEDED.value
    assert call.finished_at is None
    assert call.reconciled_at is not None


async def test_undecidable_leaves_the_row_alone(tmp_path: Path) -> None:
    """No timestamp, so a later pass with a better context can try again."""
    call = _call("file_append", {"path": "x.txt", "content": "y"}, working_directory=str(tmp_path))
    await reconcile_call(call, registry=_registry(), ctx=_ctx(tmp_path))
    assert call.status == ToolCallStatus.UNKNOWN.value
    assert call.reconciled_at is None


# --------------------------------------------------------------------------- #
# Checking never changes the target
# --------------------------------------------------------------------------- #
async def test_reconciliation_does_not_touch_the_file(tmp_path: Path) -> None:
    """The whole point: a check is a read.

    A "check" that re-applied the operation would look identical in the database — status
    `SUCCEEDED`, timestamp set — and would have destroyed whatever was in the file.
    """
    target = tmp_path / "out.txt"
    _write_exact(target, "current content\n")
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    await _check("file_write", {"path": "out.txt", "content": "different content\n"}, tmp_path)
    after = hashlib.sha256(target.read_bytes()).hexdigest()
    assert before == after
    assert target.read_text(encoding="utf-8") == "current content\n"


# --------------------------------------------------------------------------- #
# The pass over a run
# --------------------------------------------------------------------------- #
async def test_only_unknown_rows_are_checked(client) -> None:
    """A `SUCCEEDED` row is evidence, not a question, and must not be re-examined.

    Checking it would be worse than wasteful: a `SUCCEEDED` call whose file was deleted
    afterwards would be re-classified as `NOT_APPLIED`, and the record of a call that
    definitely ran would be overwritten by a check of the world as it is now.
    """
    name = f"reconcile_{uuid.uuid4().hex[:8]}"
    response = await client.post(
        "/auth/register",
        json={"email": f"{name}@aiworkbench.dev", "username": name, "password": "reconcile123"},
    )
    assert response.status_code == 201, response.text
    owner = uuid.UUID(response.json()["data"]["user"]["id"])

    import tempfile

    workspace = Path(tempfile.mkdtemp(prefix="aw-reconcile-"))
    _write_exact(workspace / "made.txt", "expected\n")

    async with session_scope() as session:
        run = AIRun(
            run_id=f"run_{uuid.uuid4().hex[:16]}",
            owner_id=owner,
            run_type="AGENT",
            status="INTERRUPTED",
        )
        session.add(run)
        await session.flush()
        ai_run_id = run.id

        unknown = ToolCall(
            ai_run_id=ai_run_id,
            step_index=1,
            tool_name="file_write",
            arguments={"path": "made.txt", "content": "expected\n"},
            result={},
            status=ToolCallStatus.UNKNOWN.value,
            mutates_state=True,
            invocation_id=f"inv_{uuid.uuid4().hex}",
            working_directory=str(workspace),
        )
        done = ToolCall(
            ai_run_id=ai_run_id,
            step_index=2,
            tool_name="file_write",
            arguments={"path": "made.txt", "content": "expected\n"},
            result={"ok": True},
            status=ToolCallStatus.SUCCEEDED.value,
            mutates_state=True,
            invocation_id=f"inv_{uuid.uuid4().hex}",
            working_directory=str(workspace),
        )
        session.add_all([unknown, done])
        await session.flush()
        unknown_id, done_id = unknown.id, done.id

    async with session_scope() as session:
        reports = await reconcile_unknown_calls(session, registry=_registry(), ai_run_id=ai_run_id)

    assert [r.tool_name for r in reports] == ["file_write"]
    assert reports[0].status_before == ToolCallStatus.UNKNOWN.value
    assert reports[0].status_after == ToolCallStatus.SUCCEEDED.value

    async with session_scope() as session:
        rows = {
            row.id: row
            for row in (
                await session.execute(
                    ToolCall.__table__.select().where(ToolCall.id.in_([unknown_id, done_id]))
                )
            ).mappings()
        }
    assert rows[unknown_id]["status"] == ToolCallStatus.SUCCEEDED.value
    assert rows[unknown_id]["reconciled_at"] is not None
    assert rows[unknown_id]["finished_at"] is None
    assert rows[done_id]["status"] == ToolCallStatus.SUCCEEDED.value
    assert rows[done_id]["reconciled_at"] is None, "a settled row was re-examined"


@pytest.mark.parametrize("tool_name", ["file_write", "directory_create", "git_add"])
def test_the_three_tools_the_recovery_can_settle_are_the_three_that_override_reconcile(
    tool_name: str,
) -> None:
    from app.ai.agent.tools import Tool

    tool = tool_for(_registry(), tool_name)
    assert tool is not None
    assert type(tool).reconcile is not Tool.reconcile


def test_context_for_uses_the_recorded_working_directory() -> None:
    """The row is the only witness to where the run was working."""
    call = _call("file_write", {"path": "x.txt"}, working_directory=r"C:\somewhere\else")
    ctx = context_for(None, call, owner_id=uuid.uuid4())
    assert ctx.working_directory == r"C:\somewhere\else"

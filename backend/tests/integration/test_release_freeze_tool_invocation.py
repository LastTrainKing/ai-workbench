"""What survives a crash in the middle of a tool call.

The defect this covers: `tool_calls` rows were written **after** the tool returned. So
the window between "the tool started" and "its outcome was recorded" left no row at all.
A process killed inside that window produced an agent run with no evidence it had ever
called anything — and re-running the goal repeated the tool. For a `file_append` or a
`git_commit` that is a duplicated side effect that nothing in the system could detect,
because nothing had been written down.

The fix is an ordering, and an ordering can only be tested by stopping a real process
between the two statements. Every window below is exercised by spawning
`_invocation_child.py`, letting it reach a named point, and **killing it from outside**
with `Popen.kill()` — not by raising inside the same process, which would run the cleanup
a crash does not get.

Windows 7 and 8 are not crash windows: they are the two ways a *record* can be wrong even
when nothing crashes (a repeated idempotency key, and two legitimate calls of the same
tool being merged into one). They are tested in-process because there is nothing to crash.

`file_append` is the tool under test throughout: it mutates state and is **not**
idempotent, so a duplicate shows up in the file itself and not only in the database.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select

from app.infrastructure.database import session_scope
from app.models.ai import AgentRun, AIRun, ToolCall

CHILD = Path(__file__).with_name("_invocation_child.py")
BACKEND = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
async def owner(client: httpx.AsyncClient):
    """A real account, registered over HTTP, removed afterwards."""
    name = f"inv_{uuid.uuid4().hex[:8]}"
    response = await client.post(
        "/auth/register",
        json={"email": f"{name}@aiworkbench.dev", "username": name, "password": "invoke1234"},
    )
    assert response.status_code == 201, response.text
    owner_id = uuid.UUID(response.json()["data"]["user"]["id"])
    try:
        yield owner_id
    finally:
        async with session_scope() as session:
            await session.execute(delete(AIRun).where(AIRun.owner_id == owner_id))


async def _make_run(owner_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID, str]:
    """A committed run and agent run, the way the executor leaves them before step 1."""
    async with session_scope() as session:
        run = AIRun(
            run_id=f"run_{uuid.uuid4().hex[:16]}",
            owner_id=owner_id,
            run_type="AGENT",
            status="RUNNING",
        )
        session.add(run)
        await session.flush()
        agent = AgentRun(
            ai_run_id=run.id, owner_id=owner_id, goal="故障注入：工具调用窗口", status="RUNNING"
        )
        session.add(agent)
        await session.flush()
        return run.id, agent.id, run.run_id


async def _calls(ai_run_id: uuid.UUID) -> list[ToolCall]:
    async with session_scope() as session:
        return list(
            (
                await session.execute(
                    select(ToolCall)
                    .where(ToolCall.ai_run_id == ai_run_id)
                    .order_by(ToolCall.step_index)
                )
            ).scalars()
        )


def _kill_at(
    window: int,
    *,
    ai_run_id: uuid.UUID,
    agent_run_id: uuid.UUID,
    owner_id: uuid.UUID,
    run_id: str,
    workspace: Path,
    target: Path,
    timeout: float = 120.0,
) -> None:
    """Spawn the child, wait until it *says* it reached the window, then kill it.

    The sentinel is written and fsynced before the child blocks, so the kill can never
    land before the window is reached — which would make the assertions vacuous.
    """
    sentinel = workspace / f"reached-{window}.txt"
    sentinel.unlink(missing_ok=True)
    argv = [
        sys.executable,
        str(CHILD),
        "--window",
        str(window),
        "--ai-run-id",
        str(ai_run_id),
        "--agent-run-id",
        str(agent_run_id),
        "--owner-id",
        str(owner_id),
        "--run-id",
        run_id,
        "--workspace",
        str(workspace),
        "--target",
        str(target),
        "--sentinel",
        str(sentinel),
    ]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        argv,
        cwd=str(BACKEND),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if sentinel.exists():
                return
            if proc.poll() is not None:
                out, err = proc.communicate()
                raise AssertionError(
                    f"window {window}: the child exited on its own "
                    f"(rc={proc.returncode}) instead of reaching the window.\n"
                    f"STDOUT:\n{out}\nSTDERR:\n{err}"
                )
            time.sleep(0.1)
        raise AssertionError(f"window {window}: the child never reached the window")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=30)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    directory = tmp_path / "agent-workspace"
    directory.mkdir()
    return directory


# --------------------------------------------------------------------------- #
# 1. Killed before the intent was committed
# --------------------------------------------------------------------------- #
async def test_window_1_before_the_intent_is_committed_leaves_no_row(
    owner: uuid.UUID, workspace: Path
) -> None:
    """Nothing durable was written, so nothing is claimed. The file must be untouched.

    This is the window the fix does *not* cover, and saying so is the point: the intent
    lives in a transaction, and a transaction that never committed leaves no trace. What
    the fix changes is that this window is now only the microseconds between `flush()` and
    `commit()` — and that everything after the commit *is* covered.
    """
    ai_run_id, agent_run_id, run_id = await _make_run(owner)
    target = workspace / "log.txt"

    _kill_at(
        1,
        ai_run_id=ai_run_id,
        agent_run_id=agent_run_id,
        owner_id=owner,
        run_id=run_id,
        workspace=workspace,
        target=target,
    )

    assert await _calls(ai_run_id) == [], "an uncommitted intent must not be visible"
    assert not target.exists(), "the tool never ran, so the file must not exist"


# --------------------------------------------------------------------------- #
# 2. Killed after the intent was committed, before the tool started
# --------------------------------------------------------------------------- #
async def test_window_2_intent_committed_before_the_tool_runs(
    owner: uuid.UUID, workspace: Path
) -> None:
    ai_run_id, agent_run_id, run_id = await _make_run(owner)
    target = workspace / "log.txt"

    _kill_at(
        2,
        ai_run_id=ai_run_id,
        agent_run_id=agent_run_id,
        owner_id=owner,
        run_id=run_id,
        workspace=workspace,
        target=target,
    )

    calls = await _calls(ai_run_id)
    assert len(calls) == 1, f"the committed intent must survive the crash; got {len(calls)}"
    call = calls[0]
    assert call.status == "RUNNING"
    assert call.started_at is not None, "the window has to be dated to be useful"
    assert call.finished_at is None, "no outcome was written, so the window is still open"
    assert call.invocation_id, "recovery cannot name the invocation without an id"
    assert call.mutates_state is True, "file_append changes a file"
    assert not target.exists(), "the tool had not started"


# --------------------------------------------------------------------------- #
# 3. Killed after the side effect, before the outcome was written
# --------------------------------------------------------------------------- #
async def test_window_3_side_effect_landed_without_an_outcome(
    owner: uuid.UUID, workspace: Path
) -> None:
    """The case the whole mechanism exists for: the write happened, and the record does
    not know it. The row says `RUNNING`, which is exactly the truth."""
    ai_run_id, agent_run_id, run_id = await _make_run(owner)
    target = workspace / "log.txt"

    _kill_at(
        3,
        ai_run_id=ai_run_id,
        agent_run_id=agent_run_id,
        owner_id=owner,
        run_id=run_id,
        workspace=workspace,
        target=target,
    )

    assert target.exists(), "the append ran before the process was killed"
    assert target.read_text(encoding="utf-8") == "w3\n"

    calls = await _calls(ai_run_id)
    assert len(calls) == 1
    assert calls[0].status == "RUNNING", "the outcome was never written"
    assert calls[0].finished_at is None
    assert calls[0].mutates_state is True


# --------------------------------------------------------------------------- #
# 4. Killed after the outcome was committed
# --------------------------------------------------------------------------- #
async def test_window_4_a_committed_outcome_is_decided(owner: uuid.UUID, workspace: Path) -> None:
    ai_run_id, agent_run_id, run_id = await _make_run(owner)
    target = workspace / "log.txt"

    _kill_at(
        4,
        ai_run_id=ai_run_id,
        agent_run_id=agent_run_id,
        owner_id=owner,
        run_id=run_id,
        workspace=workspace,
        target=target,
    )

    calls = await _calls(ai_run_id)
    assert len(calls) == 1
    call = calls[0]
    assert call.status == "SUCCEEDED"
    assert call.finished_at is not None, "the outcome closed the window"
    assert call.latency_ms >= 0
    assert target.read_text(encoding="utf-8") == "w4\n"


# --------------------------------------------------------------------------- #
# 5. Killed after the tool failed, before the failure was written
# --------------------------------------------------------------------------- #
async def test_window_5_a_failure_that_was_never_written(owner: uuid.UUID, workspace: Path) -> None:
    ai_run_id, agent_run_id, run_id = await _make_run(owner)
    target = workspace / "log.txt"

    _kill_at(
        5,
        ai_run_id=ai_run_id,
        agent_run_id=agent_run_id,
        owner_id=owner,
        run_id=run_id,
        workspace=workspace,
        target=target,
    )

    calls = await _calls(ai_run_id)
    assert len(calls) == 1
    assert calls[0].tool_name == "file_delete"
    assert calls[0].status == "RUNNING", "the failure was not written either"
    assert calls[0].mutates_state is True
    assert not target.exists()


# --------------------------------------------------------------------------- #
# 6. Recovery after the restart
# --------------------------------------------------------------------------- #
async def test_recovery_closes_the_open_windows_and_leaves_decided_ones_alone(
    owner: uuid.UUID, workspace: Path
) -> None:
    """`RUNNING` at startup is not running, and it is not `FAILED` either.

    Three runs are crashed at three windows, and a fourth is left `SUCCEEDED`. Recovery
    must convert exactly the open ones to `UNKNOWN` — a status that asserts nothing the
    system cannot know — and must not touch the one whose outcome was written.
    """
    from app.ai.tracing import resolve_orphan_tool_calls

    run_2 = await _make_run(owner)
    run_3 = await _make_run(owner)
    run_5 = await _make_run(owner)
    run_4 = await _make_run(owner)
    target = workspace / "log.txt"

    for window, (ai_run_id, agent_run_id, run_id) in (
        (2, run_2),
        (3, run_3),
        (5, run_5),
        (4, run_4),
    ):
        _kill_at(
            window,
            ai_run_id=ai_run_id,
            agent_run_id=agent_run_id,
            owner_id=owner,
            run_id=run_id,
            workspace=workspace,
            target=target,
        )

    async with session_scope() as session:
        resolved = await resolve_orphan_tool_calls(session)
        await session.commit()

    assert len(resolved) == 3, f"exactly the three open windows must be closed; got {len(resolved)}"
    assert all(call.status == "UNKNOWN" for call in resolved)

    for ai_run_id, expected in (
        (run_2[0], "UNKNOWN"),
        (run_3[0], "UNKNOWN"),
        (run_5[0], "UNKNOWN"),
        (run_4[0], "SUCCEEDED"),
    ):
        calls = await _calls(ai_run_id)
        assert len(calls) == 1
        assert calls[0].status == expected, (
            f"run {ai_run_id}: expected {expected}, got {calls[0].status}"
        )

    # `finished_at` stays NULL on an `UNKNOWN`: stamping it would claim the invocation
    # ended, and the whole point is that its end was never observed.
    unknown = await _calls(run_3[0])
    assert unknown[0].finished_at is None


async def test_an_unknown_call_is_reported_as_an_undetermined_write(
    owner: uuid.UUID, workspace: Path
) -> None:
    """Recovery is only useful if the answer reaches a caller. This is that answer."""
    from app.ai.tracing import resolve_orphan_tool_calls, unresolved_tool_calls

    ai_run_id, agent_run_id, run_id = await _make_run(owner)
    target = workspace / "log.txt"
    _kill_at(
        3,
        ai_run_id=ai_run_id,
        agent_run_id=agent_run_id,
        owner_id=owner,
        run_id=run_id,
        workspace=workspace,
        target=target,
    )

    async with session_scope() as session:
        await resolve_orphan_tool_calls(session)
        await session.commit()

    async with session_scope() as session:
        pending = await unresolved_tool_calls(session, ai_run_id)

    assert len(pending) == 1
    assert pending[0].tool_name == "file_append"
    assert pending[0].mutates_state is True
    assert "无法确认" in pending[0].error_message


# --------------------------------------------------------------------------- #
# 7. The same invocation id submitted twice
# --------------------------------------------------------------------------- #
async def test_the_same_invocation_id_does_not_create_a_second_row(
    owner: uuid.UUID, workspace: Path
) -> None:
    """An idempotency key that is accepted twice must read as one invocation.

    Without this, a retry that repeats an already-accepted call would look like a second
    execution — and the difference between "it ran once" and "it ran twice" is the whole
    question being asked.
    """
    from app.ai.tracing import begin_tool_call, finish_tool_call

    ai_run_id, agent_run_id, _run_id = await _make_run(owner)
    key = f"inv_{uuid.uuid4().hex}"

    async with session_scope() as session:
        run = await session.get(AIRun, ai_run_id)
        first = await begin_tool_call(
            session,
            run=run,
            tool_name="file_append",
            arguments={"path": "log.txt", "content": "once\n"},
            step_index=1,
            mutates_state=True,
            agent_run_id=agent_run_id,
            invocation_id=key,
        )
        await session.commit()
        first_db_id = first.id

    async with session_scope() as session:
        run = await session.get(AIRun, ai_run_id)
        second = await begin_tool_call(
            session,
            run=run,
            tool_name="file_append",
            arguments={"path": "log.txt", "content": "once\n"},
            step_index=1,
            mutates_state=True,
            agent_run_id=agent_run_id,
            invocation_id=key,
        )
        await finish_tool_call(session, second, ok=True, result={"ok": True})
        await session.commit()
        second_db_id = second.id

    assert first_db_id == second_db_id, "the same key must resolve to the same row"
    calls = await _calls(ai_run_id)
    assert len(calls) == 1, f"expected one row for one key, got {len(calls)}"
    assert calls[0].status == "SUCCEEDED"


# --------------------------------------------------------------------------- #
# 8. Two legitimate calls of the same tool are not merged
# --------------------------------------------------------------------------- #
async def test_two_different_calls_of_one_tool_stay_two_rows(
    owner: uuid.UUID, workspace: Path
) -> None:
    """The guard on the guard for window 7: de-duplication is by key, not by tool name.

    A run that appends to the same file twice is doing two things. Collapsing them would
    lose one, and the file would disagree with the record.
    """
    from app.ai.tracing import begin_tool_call, finish_tool_call

    ai_run_id, agent_run_id, _run_id = await _make_run(owner)

    async with session_scope() as session:
        run = await session.get(AIRun, ai_run_id)
        for index, content in enumerate(("first\n", "second\n"), start=1):
            call = await begin_tool_call(
                session,
                run=run,
                tool_name="file_append",
                arguments={"path": "log.txt", "content": content},
                step_index=index,
                mutates_state=True,
                agent_run_id=agent_run_id,
            )
            await finish_tool_call(session, call, ok=True, result={"ok": True})
        await session.commit()

    calls = await _calls(ai_run_id)
    assert len(calls) == 2, f"two calls must be two rows, got {len(calls)}"
    assert calls[0].invocation_id != calls[1].invocation_id
    assert [c.status for c in calls] == ["SUCCEEDED", "SUCCEEDED"]
    assert calls[0].arguments["content"] == "first\n"
    assert calls[1].arguments["content"] == "second\n"


# --------------------------------------------------------------------------- #
# The ordering itself, asserted from a second connection
# --------------------------------------------------------------------------- #
class _OneCallLLM:
    """Asks for one tool call, then answers. The smallest thing that drives the executor."""

    model = "fake-tool-caller"

    def __init__(self, tool_name: str, arguments: dict) -> None:
        self._tool_name = tool_name
        self._arguments = arguments
        self._turn = 0
        #: Filled in by `chat` on turn 1: what a *different session* could see at the
        #: moment the tool was about to run.
        self.visible_to_another_session: list[str] | None = None

    async def chat(self, messages, **kwargs):
        from app.ai.types import ChatResult, ToolCallRequest

        self._turn += 1
        if self._turn > 1:
            return ChatResult(content="完成")
        return ChatResult(
            content="",
            tool_calls=[
                ToolCallRequest(id="call-1", name=self._tool_name, arguments=self._arguments)
            ],
        )


async def test_the_executor_commits_the_intent_before_the_tool_runs(
    owner: uuid.UUID, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering, proved the only way that means anything: from another connection.

    A flush would put the row in this session's transaction, where a second session sees
    nothing. So the fake registry — standing in for the real tool — opens its **own**
    session and looks for the invocation. If the executor had only flushed, it would find
    nothing, and the assertion would fail.
    """
    from app.ai.agent import executor as executor_module
    from app.ai.agent.executor import AgentExecutor
    from app.ai.providers.factory import ProviderInfo
    from app.ai.tools.permissions import AgentPolicy
    from app.ai.types import ToolResult

    observed: dict[str, object] = {}

    class _Registry:
        def __init__(self) -> None:
            self.specs_list = [
                type(
                    "S",
                    (),
                    {
                        "name": "file_append",
                        "description": "append",
                        "parameters": {"properties": {"path": {}, "content": {}}},
                        "category": "filesystem",
                        "mutates_state": True,
                        "requires_confirmation": False,
                        "risk_level": "low",
                        "idempotent": False,
                    },
                )()
            ]

        def names(self) -> list[str]:
            return ["file_append"]

        def specs(self) -> list[object]:
            return self.specs_list

        def get_spec(self, name: str):
            return self.specs_list[0] if name == "file_append" else None

        async def execute(self, ctx, name, arguments):
            # A **separate** session: the point is what another connection can see. The
            # row is found through the run's own public id, so nothing is smuggled in
            # from the test that the real tool would not have had.
            async with session_scope() as other:
                run_id = (
                    await other.execute(select(AIRun.id).where(AIRun.run_id == ctx.run_id))
                ).scalar_one()
                rows = list(
                    (
                        await other.execute(
                            select(ToolCall).where(
                                ToolCall.ai_run_id == run_id,
                                ToolCall.status == "RUNNING",
                            )
                        )
                    ).scalars()
                )
            observed["visible"] = [r.tool_name for r in rows]
            observed["finished_at"] = [r.finished_at for r in rows]
            observed["mutates_state"] = [r.mutates_state for r in rows]
            observed["invocation_id"] = [r.invocation_id for r in rows]
            return ToolResult(ok=True, data={"path": str(arguments.get("path"))}, summary="ok")

    registry = _Registry()
    llm = _OneCallLLM("file_append", {"path": "out.txt", "content": "x\n"})

    monkeypatch.setattr(
        executor_module, "build_llm_provider", lambda: (llm, ProviderInfo(kind="offline", model="fake-tool-caller"))
    )

    async with session_scope() as session:
        await AgentExecutor(registry=registry, max_steps=2).run(
            session, owner_id=owner, goal="写一个文件"
        )

    assert observed.get("visible") == ["file_append"], (
        "at the moment the tool ran, its intent was not visible to another connection — "
        "which means the executor flushed instead of committing, and a crash would have "
        "left nothing behind"
    )
    assert observed.get("finished_at") == [None], "the outcome cannot exist yet"
    assert observed.get("mutates_state") == [True]
    assert observed.get("invocation_id") and observed["invocation_id"][0], (
        "the invocation has to be nameable before it runs, or recovery cannot refer to it"
    )


async def test_the_executor_closes_the_window_when_the_tool_returns(
    owner: uuid.UUID, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: after the call, the row is decided and durable."""
    from app.ai.agent import executor as executor_module
    from app.ai.agent.executor import AgentExecutor
    from app.ai.providers.factory import ProviderInfo
    from app.ai.types import ToolResult

    class _Registry:
        def __init__(self) -> None:
            self.specs_list = [
                type(
                    "S",
                    (),
                    {
                        "name": "file_append",
                        "description": "append",
                        "parameters": {"properties": {}},
                        "category": "filesystem",
                        "mutates_state": True,
                        "requires_confirmation": False,
                        "risk_level": "low",
                        "idempotent": False,
                    },
                )()
            ]

        def names(self) -> list[str]:
            return ["file_append"]

        def specs(self) -> list[object]:
            return self.specs_list

        def get_spec(self, name: str):
            return self.specs_list[0]

        async def execute(self, ctx, name, arguments):
            return ToolResult(ok=True, data={}, summary="ok", latency_ms=7)

    registry = _Registry()
    llm = _OneCallLLM("file_append", {"path": "out.txt", "content": "x\n"})
    monkeypatch.setattr(
        executor_module, "build_llm_provider", lambda: (llm, ProviderInfo(kind="offline", model="fake-tool-caller"))
    )

    async with session_scope() as session:
        outcome = await AgentExecutor(registry=registry, max_steps=2).run(
            session, owner_id=owner, goal="写一个文件"
        )

    async with session_scope() as session:
        run = (
            await session.execute(select(AIRun).where(AIRun.run_id == outcome.run_id))
        ).scalar_one()
        rows = list(
            (await session.execute(select(ToolCall).where(ToolCall.ai_run_id == run.id))).scalars()
        )

    assert len(rows) == 1
    assert rows[0].status == "SUCCEEDED"
    assert rows[0].finished_at is not None
    assert rows[0].latency_ms == 7
    assert rows[0].invocation_id


async def test_a_duplicate_call_is_recorded_as_skipped_not_as_a_second_execution(
    owner: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop guard refuses the repeat. The record must say `SKIPPED`, and the tool
    must have run exactly once — a `SKIPPED` row that hid a real execution would be worse
    than no row."""
    from app.ai.agent import executor as executor_module
    from app.ai.agent.executor import AgentExecutor
    from app.ai.providers.factory import ProviderInfo
    from app.ai.types import ChatResult, ToolCallRequest, ToolResult

    executed: list[dict] = []

    class _Registry:
        def __init__(self) -> None:
            self.specs_list = [
                type(
                    "S",
                    (),
                    {
                        "name": "file_append",
                        "description": "append",
                        "parameters": {"properties": {}},
                        "category": "filesystem",
                        "mutates_state": True,
                        "requires_confirmation": False,
                        "risk_level": "low",
                        "idempotent": False,
                    },
                )()
            ]

        def names(self) -> list[str]:
            return ["file_append"]

        def specs(self) -> list[object]:
            return self.specs_list

        def get_spec(self, name: str):
            return self.specs_list[0]

        async def execute(self, ctx, name, arguments):
            executed.append(arguments)
            return ToolResult(ok=True, data={}, summary="ok")

    class _Repeater:
        """Asks for the *same* call three times, so the loop guard must fire."""

        model = "fake-repeater"

        async def chat(self, messages, **kwargs):
            return ChatResult(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="c", name="file_append", arguments={"path": "a.txt", "content": "x"}
                    )
                ],
            )

    registry = _Registry()
    monkeypatch.setattr(
        executor_module,
        "build_llm_provider",
        lambda: (_Repeater(), ProviderInfo(kind="offline", model="fake-tool-caller")),
    )

    async with session_scope() as session:
        outcome = await AgentExecutor(registry=registry, max_steps=3).run(
            session, owner_id=owner, goal="重复调用同一个工具"
        )

    assert len(executed) == 1, f"the tool must run exactly once; ran {len(executed)} times"

    async with session_scope() as session:
        run = (
            await session.execute(select(AIRun).where(AIRun.run_id == outcome.run_id))
        ).scalar_one()
        rows = list(
            (
                await session.execute(
                    select(ToolCall)
                    .where(ToolCall.ai_run_id == run.id)
                    .order_by(ToolCall.step_index)
                )
            ).scalars()
        )

    statuses = [r.status for r in rows]
    assert statuses == ["SUCCEEDED", "SKIPPED"], f"expected one run and one refusal, got {statuses}"
    assert rows[1].finished_at is not None, "a refusal is decided immediately"

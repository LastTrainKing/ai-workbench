"""Provider failure behaviours, tested against a real local endpoint — no API key.

The six behaviours the requirements list (empty response, malformed response, loop
detection, step ceiling, mid-run process death, invalid JSON) all live in the seam between
the transport, the provider and the executor. A monkeypatched provider tests none of them.

So each test starts `_stub_provider.StubProvider` — a real HTTP server on loopback — points
the account's provider configuration at it, and drives the **real** `AgentExecutor` through
the **real** `OpenAICompatibleProvider`. What is asserted is the resulting database state,
not that a function returned.

Why a loopback endpoint is allowed while every other provider is blocked is explained in
`app.ai.providers.guard`: the guard's rule is "anything that could leave the machine", and a
stub on `127.0.0.1` cannot.

No key is used, none is needed, and the string below is deliberately marked synthetic so the
repository's own secret scanner does not flag it.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.ai.agent.executor import AgentExecutor
from app.ai.providers.factory import reset_provider_cache
from app.core.errors import AIResponseFormatError
from app.core.security import hash_password
from app.infrastructure.database import session_scope
from app.models.ai import AgentRun, AIRun
from app.models.task import Task
from app.models.user import User

from ._stub_provider import (
    StubProvider,
    answer,
    empty,
    no_choices,
    repeated_tool_call,
    varying_tool_call,
)

#: Credential-shaped and obviously synthetic, so the repository's secret scanner can tell
#: it apart from a real key. The stub ignores it; the provider refuses to start without one.
STUB_KEY = "sk-fake-stub-0000000000-not-a-real-credential"

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- fixtures --


async def _make_account() -> uuid.UUID:
    name = f"stub_{uuid.uuid4().hex[:8]}"
    async with session_scope() as session:
        user = User(
            email=f"{name}@aiworkbench.dev",
            username=name,
            display_name="Stub Provider Test",
            hashed_password=hash_password("stub1234"),
        )
        session.add(user)
        await session.flush()
        return user.id


async def _drop_account(owner: uuid.UUID) -> None:
    async with session_scope() as session:
        await session.execute(delete(Task).where(Task.owner_id == owner))
        await session.execute(delete(AgentRun).where(AgentRun.owner_id == owner))
        await session.execute(delete(AIRun).where(AIRun.owner_id == owner))
        await session.execute(delete(User).where(User.id == owner))


def _point_at(owner: uuid.UUID, base_url: str, *, timeout: float = 20.0) -> None:
    """Write the stub endpoint into this account's own provider configuration.

    Through `runtime_config.save`, which is the same path the Settings UI uses — so this
    also exercises that the per-account store accepts a loopback http endpoint and that the
    factory picks the change up.
    """
    from app.ai.runtime_config import save, set_current_owner

    save(
        owner,
        {
            "provider": "openai_compatible",
            "base_url": base_url,
            "api_key": STUB_KEY,
            "model": "stub-model",
            "timeout_seconds": timeout,
        },
    )
    set_current_owner(owner)
    reset_provider_cache()


@pytest.fixture
async def account():
    """A throwaway account, removed afterwards."""
    owner = await _make_account()
    from app.ai.runtime_config import set_current_owner

    set_current_owner(owner)
    try:
        yield owner
    finally:
        reset_provider_cache()
        await _drop_account(owner)


async def _read_run(owner: uuid.UUID) -> tuple[AIRun | None, AgentRun | None]:
    async with session_scope() as session:
        run = (
            (
                await session.execute(
                    select(AIRun).where(AIRun.owner_id == owner).order_by(AIRun.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        agent = None
        if run is not None:
            agent = (
                (await session.execute(select(AgentRun).where(AgentRun.ai_run_id == run.id)))
                .scalars()
                .first()
            )
        return run, agent


async def _run_agent(owner: uuid.UUID, *, max_steps: int = 3):
    async with session_scope() as session:
        outcome = await AgentExecutor(max_steps=max_steps).run(
            session, owner_id=owner, goal="把当前项目里未完成的任务列出来"
        )
        await session.commit()
        return outcome


# ------------------------------------------------------- 1. empty response --


async def test_empty_response_is_incomplete_not_a_success(account: uuid.UUID) -> None:
    """The model returns HTTP 200 with empty content.

    The whole point: the run produced a message explaining itself, and an explanation is not
    a result. Before the fix this was recorded as `SUCCEEDED`.
    """
    with StubProvider([empty()]) as stub:
        _point_at(account, stub.base_url)
        outcome = await _run_agent(account)

    run, agent = await _read_run(account)
    assert stub.call_count == 1, "one completion was requested"
    assert outcome.stopped_reason == "empty_response"
    assert outcome.status == "INCOMPLETE"
    assert run is not None and run.status == "INCOMPLETE"
    assert agent is not None and agent.status == "INCOMPLETE"
    assert run.result_payload["status_reason"] == "empty_response"
    assert outcome.final_answer, "the explanation is still shown to the user"
    assert "空内容" in outcome.final_answer


# --------------------------------------------------- 2. malformed response --


async def test_response_without_choices_is_a_classified_format_error(account: uuid.UUID) -> None:
    """HTTP 200, valid JSON, and the expected `choices` array is simply absent.

    This must not be mistaken for an empty answer — "the model said nothing" and "the
    response was not the shape we asked for" are different facts, and only one of them is
    the model's doing.
    """
    with StubProvider([no_choices()]) as stub:
        _point_at(account, stub.base_url)
        with pytest.raises(AIResponseFormatError) as caught:
            await _run_agent(account)

    assert "no choices" in str(caught.value)
    run, agent = await _read_run(account)
    assert run is not None
    assert run.status == "FAILED", f"expected FAILED, got {run.status}"
    assert run.error_code, "the failure must be recorded with a code"
    assert "no choices" in (run.error_message or "")
    assert agent is not None and agent.status == "FAILED"


# ------------------------------------------------------------ 3. invalid JSON --


async def test_non_json_body_is_a_classified_format_error(account: uuid.UUID) -> None:
    """The body is not JSON at all — a proxy error page, a truncated stream, anything.

    Distinct from case 2 in the transport: there the body parsed and the shape was wrong,
    here the parse itself fails.
    """
    with StubProvider([answer()], raw_body="<html>502 Bad Gateway</html>") as stub:
        _point_at(account, stub.base_url)
        with pytest.raises(AIResponseFormatError) as caught:
            await _run_agent(account)

    assert "not valid JSON" in str(caught.value) or "JSON" in str(caught.value)
    run, _agent = await _read_run(account)
    assert run is not None and run.status == "FAILED"
    assert run.error_code
    assert run.status != "SUCCEEDED"


# --------------------------------------------------------- 4. loop detection --


async def test_repeated_identical_tool_call_is_detected_and_stopped(account: uuid.UUID) -> None:
    """The model asks for the same tool with the same arguments, forever.

    The executor's loop guard has to notice. Without it the run would burn the whole step
    budget doing the same thing, and — before the status fix — report success for it.
    """
    with StubProvider([repeated_tool_call("project_list")]) as stub:
        _point_at(account, stub.base_url)
        outcome = await _run_agent(account, max_steps=6)

    run, agent = await _read_run(account)
    assert outcome.stopped_reason == "loop_detected"
    assert outcome.status == "INCOMPLETE"
    assert run is not None and run.status == "INCOMPLETE"
    assert agent is not None and agent.status == "INCOMPLETE"
    # The guard fired before the ceiling: the stub was asked once, the duplicate was
    # refused without a second request.
    assert stub.call_count == 2, (
        f"expected 2 completions (1 real + 1 duplicate), got {stub.call_count}"
    )
    assert "重复调用" in outcome.final_answer


# ------------------------------------------------------------ 5. step ceiling --


async def test_step_ceiling_stops_the_run_and_says_so(account: uuid.UUID) -> None:
    """A different tool call every time, so the loop guard never fires and the ceiling is
    what stops it. The distinction between this and case 4 is the point: both end the run,
    and they are not the same ending."""
    with StubProvider([varying_tool_call("project_list")]) as stub:
        _point_at(account, stub.base_url)
        outcome = await _run_agent(account, max_steps=3)

    run, agent = await _read_run(account)
    assert outcome.stopped_reason == "max_steps"
    assert outcome.status == "INCOMPLETE"
    assert len(outcome.steps) == 3
    assert run is not None and run.status == "INCOMPLETE"
    assert agent is not None and agent.status == "INCOMPLETE"
    assert "最大步数" in outcome.final_answer


# --------------------------------------------------- 6. process killed mid-run --


async def test_a_killed_process_leaves_an_interrupted_run(account: uuid.UUID) -> None:
    """A real child process, killed while the run is in flight.

    Not an exception raised in-process: the reconcile path exists for a row whose process
    stopped existing, and that is a different event. The child is spawned, the parent waits
    until the run row says RUNNING, kills it, and then runs the same reconciliation the
    application runs at startup.
    """
    # Slow enough that the child is reliably mid-call when it is killed.
    with StubProvider([varying_tool_call("project_list")], delay_seconds=2.5) as stub:
        _point_at(account, stub.base_url, timeout=30.0)

        child = subprocess.Popen(
            [sys.executable, str(HERE / "_agent_child.py"), str(account), "5"],
            env={**os.environ, "AI_PROVIDER": "openai_compatible"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(HERE.parents[1]),
        )
        try:
            running = await _wait_for_running(account, timeout=60)
            assert running is not None, "the child never produced a RUNNING run to kill"
        finally:
            child.kill()
            child.wait(timeout=30)

        # The process is gone. Now the startup reconciliation runs, exactly as it does
        # when the application starts after a crash.
        from app.main import _reconcile_interrupted_agent_runs

        await _reconcile_interrupted_agent_runs()

        run, agent = await _read_run(account)
        assert run is not None
        assert run.status == "INTERRUPTED", f"expected INTERRUPTED, got {run.status}"
        assert agent is not None and agent.status == "INTERRUPTED"
        assert run.error_message, "an interrupted run has to say why"
        # And the scratchpad state agrees with the status, which is the invariant that was
        # broken before: the row said FAILED while the state still said EXECUTING.
        assert agent.scratchpad.get("state") == "INTERRUPTED"


async def _wait_for_running(owner: uuid.UUID, *, timeout: float) -> AIRun | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        async with session_scope() as session:
            row = (
                (
                    await session.execute(
                        select(AIRun).where(
                            AIRun.owner_id == owner,
                            AIRun.run_type == "AGENT",
                            AIRun.status == "RUNNING",
                        )
                    )
                )
                .scalars()
                .first()
            )
        if row is not None:
            return row
        await asyncio.sleep(0.5)
    return None


# ------------------------------------------- adjacent cases, same harness, no key --

# The previous round listed these as closable without a key. They are the same harness, so
# they are closed here rather than carried as "blocked" for no reason.


async def test_provider_http_error_is_classified(account: uuid.UUID) -> None:
    """A 500 from the provider is a provider error, not a format error and not a success."""
    from app.core.errors import AIProviderError

    with StubProvider([answer()], status=500) as stub:
        _point_at(account, stub.base_url)
        with pytest.raises(AIProviderError):
            await _run_agent(account)

    run, _agent = await _read_run(account)
    assert run is not None and run.status in {"FAILED", "TIMEOUT"}
    assert run.status != "SUCCEEDED"


async def test_provider_timeout_is_classified_not_a_hang(account: uuid.UUID) -> None:
    """A provider that never answers must time out, and the timeout must be recorded.

    The client timeout is 1s and the stub sleeps 4s, so the outcome is deterministic.
    """
    from app.core.errors import AITimeoutError

    with StubProvider([answer()], delay_seconds=4.0) as stub:
        _point_at(account, stub.base_url, timeout=1.0)
        started = time.perf_counter()
        with pytest.raises(AITimeoutError):
            await _run_agent(account)
        elapsed = time.perf_counter() - started

    assert elapsed < 30, f"the call took {elapsed:.1f}s — it should have given up"
    run, _agent = await _read_run(account)
    assert run is not None
    assert run.status in {"TIMEOUT", "FAILED"}
    assert run.status != "SUCCEEDED"


async def test_no_key_reaches_the_stub_that_is_not_ours(account: uuid.UUID) -> None:
    """The stub is on loopback and the account's key is synthetic.

    A guard on the guard: if a real vendor URL ever appeared in this account's config, the
    provider guard would block it. Asserted rather than assumed, because the loopback
    exemption added to `guard()` is the one thing standing between these tests and a real
    call.
    """
    from app.ai.providers.guard import BlockedProvider, guard

    class _Vendor:
        name = "openai_compatible"
        model = "gpt-4o-mini"
        base_url = "https://api.deepseek.com/v1"

    blocked = guard(_Vendor(), kind="openai_compatible")
    assert isinstance(blocked, BlockedProvider), "a vendor endpoint must still be blocked"

    class _Local:
        name = "openai_compatible"
        model = "stub-model"
        base_url = "http://127.0.0.1:12345/v1"

    assert guard(_Local(), kind="openai_compatible") is not None
    assert not isinstance(guard(_Local(), kind="openai_compatible"), BlockedProvider)

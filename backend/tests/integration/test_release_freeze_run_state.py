"""Release-freeze regression tests — run state semantics and the job attempt bound.

Two confirmed defects are reproduced here.

**A stopped agent was written as a success.** `AgentExecutor` ended with
``agent_run.status = SUCCEEDED if final_answer else FAILED`` and then called
``recorder.finish(status=SUCCEEDED)`` unconditionally. Since every non-`final_answer`
ending produced explanatory text, hitting the step ceiling, tripping the loop guard and
getting an empty model response were all recorded as ``SUCCEEDED``. The test drives a
real executor against a stub model that loops until `max_steps` and asserts the row says
``INCOMPLETE``.

**A crashed job was retried forever.** ``requeue_stale`` moved every stale ``RUNNING``
job back to ``QUEUED`` without consulting ``attempts``/``max_attempts``, so a job that
crashed the worker on every attempt was never retired. The tests below cover both sides:
attempts remaining -> requeued with a backoff; attempts spent -> terminal ``FAILED``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.ai.agent import executor as executor_module
from app.ai.agent.executor import (
    AgentExecutor,
    AgentStopReason,
    _as_stop_reason,
    _verify_deliverables,
    _STOP_REASON_STATUS,
)
from app.ai.types import ChatResult, ToolCallRequest, ToolResult, ToolSpec
from app.domain.enums import DocumentStatus, JobStatus, RunStatus
from app.infrastructure.database import session_scope
from app.models.job import Job
from app.models.knowledge import Document, KnowledgeBase
from app.models.user import User
from app.repositories.ai_repo import JobRepository

# --------------------------------------------------------------- stop reason mapping --


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (AgentStopReason.FINAL_ANSWER, RunStatus.SUCCEEDED),
        (AgentStopReason.MAX_STEPS, RunStatus.INCOMPLETE),
        (AgentStopReason.LOOP_DETECTED, RunStatus.INCOMPLETE),
        (AgentStopReason.EMPTY_RESPONSE, RunStatus.INCOMPLETE),
        (AgentStopReason.TOOL_VERIFICATION_FAILED, RunStatus.FAILED),
        (AgentStopReason.CANCELLED, RunStatus.CANCELLED),
        (AgentStopReason.TIMEOUT, RunStatus.TIMEOUT),
        (AgentStopReason.INTERRUPTED, RunStatus.INTERRUPTED),
        (AgentStopReason.ERROR, RunStatus.FAILED),
    ],
)
def test_stop_reason_maps_to_the_right_status(reason: AgentStopReason, expected: RunStatus) -> None:
    assert _STOP_REASON_STATUS[reason] is expected


def test_only_final_answer_can_succeed() -> None:
    """The whole point: 'I stopped' is not 'I finished'."""
    succeeding = [r for r, s in _STOP_REASON_STATUS.items() if s is RunStatus.SUCCEEDED]
    assert succeeding == [AgentStopReason.FINAL_ANSWER]


def test_unknown_stop_reason_fails_rather_than_succeeding() -> None:
    assert _as_stop_reason("something_new") is AgentStopReason.ERROR
    assert _as_stop_reason("max_steps") is AgentStopReason.MAX_STEPS


# ------------------------------------------------------------- deliverable verification --


def test_missing_deliverable_fails_verification(tmp_path: Any) -> None:
    missing = tmp_path / "not-written.md"
    ok, problems = _verify_deliverables({"files": [str(missing)]})
    assert ok is False
    assert problems and "不存在" in problems[0]


def test_empty_deliverable_fails_verification(tmp_path: Any) -> None:
    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    ok, problems = _verify_deliverables({"files": [str(empty)]})
    assert ok is False
    assert problems and "空文件" in problems[0]


def test_real_deliverable_passes_verification(tmp_path: Any) -> None:
    real = tmp_path / "report.md"
    real.write_text("# 目录\n", encoding="utf-8")
    ok, problems = _verify_deliverables({"files": [str(real)]})
    assert ok is True and problems == []


def test_entity_ids_are_not_treated_as_files() -> None:
    """A created task's id is not a path and must not be reported as a missing file."""
    ok, problems = _verify_deliverables({"tasks": [str(uuid.uuid4())], "notes": ["abc123"]})
    assert ok is True and problems == []


# ------------------------------------------------------------- executor, end to end --


@dataclass
class _StubInfo:
    kind: str = "offline"


class _StubRegistry:
    """One tool that always succeeds, so the loop is bounded only by `max_steps`."""

    def __init__(self) -> None:
        self.calls = 0
        self._spec = ToolSpec(
            name="noop",
            description="does nothing",
            parameters={"type": "object", "properties": {"n": {"type": "integer"}}},
            category="knowledge",
        )

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def names(self) -> list[str]:
        return [self._spec.name]

    async def execute(self, _ctx: Any, _name: str, _arguments: dict[str, Any]) -> ToolResult:
        self.calls += 1
        return ToolResult(ok=True, summary="ok", latency_ms=1, data={"n": self.calls})


class _LoopingLLM:
    """Always asks for another tool call, with different arguments each turn."""

    model = "stub-model"

    def __init__(self) -> None:
        self.turns = 0

    async def chat(self, _messages: Any, **_kwargs: Any) -> ChatResult:
        self.turns += 1
        return ChatResult(
            content="",
            tool_calls=[
                ToolCallRequest(id=f"call-{self.turns}", name="noop", arguments={"n": self.turns})
            ],
        )


class _AnsweringLLM:
    """Answers immediately with prose and no tool call."""

    model = "stub-model"

    async def chat(self, _messages: Any, **_kwargs: Any) -> ChatResult:
        return ChatResult(content="这是最终答复。")


@pytest.fixture
def _stub_provider(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    """Replace the provider factory with a controllable stub."""

    def install(llm: Any) -> None:
        monkeypatch.setattr(
            executor_module, "build_llm_provider", lambda: (llm, _StubInfo())
        )

    return install


async def _make_owner() -> uuid.UUID:
    async with session_scope() as session:
        user = User(
            email=f"runstate_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
            username=f"runstate_{uuid.uuid4().hex[:8]}",
            display_name="Run State",
            hashed_password="x",
        )
        session.add(user)
        await session.flush()
        return user.id


async def test_agent_hitting_max_steps_is_incomplete_not_succeeded(
    _stub_provider: Any,
) -> None:
    """The defect: the agent produced an explanation, so the run said SUCCEEDED."""
    _stub_provider(_LoopingLLM())
    owner = await _make_owner()
    registry = _StubRegistry()

    async with session_scope() as session:
        outcome = await AgentExecutor(registry=registry, max_steps=3).run(
            session, owner_id=owner, goal="do something endless"
        )
        await session.commit()

    assert outcome.stopped_reason == AgentStopReason.MAX_STEPS.value
    assert outcome.status == RunStatus.INCOMPLETE.value
    # The explanation is still shown to the user; it just is not a success.
    assert outcome.final_answer
    assert registry.calls == 3

    async with session_scope() as session:
        from sqlalchemy import select

        from app.models.ai import AgentRun, AIRun

        run = (
            await session.execute(select(AIRun).where(AIRun.owner_id == owner))
        ).scalars().one()
        agent = (
            await session.execute(select(AgentRun).where(AgentRun.owner_id == owner))
        ).scalars().one()
        # The three places the status is visible must agree.
        assert run.status == RunStatus.INCOMPLETE.value
        assert agent.status == RunStatus.INCOMPLETE.value
        assert run.result_payload["stopped_reason"] == AgentStopReason.MAX_STEPS.value


async def test_agent_that_answers_normally_succeeds(_stub_provider: Any) -> None:
    """The other half: a real final answer is still a success."""
    _stub_provider(_AnsweringLLM())
    owner = await _make_owner()

    async with session_scope() as session:
        outcome = await AgentExecutor(registry=_StubRegistry(), max_steps=3).run(
            session, owner_id=owner, goal="answer a question"
        )
        await session.commit()

    assert outcome.stopped_reason == AgentStopReason.FINAL_ANSWER.value
    assert outcome.status == RunStatus.SUCCEEDED.value


class _OneCallThenAnswerLLM:
    """Asks for one tool call, then answers. Ends on `final_answer`."""

    model = "stub-model"

    def __init__(self) -> None:
        self.turns = 0

    async def chat(self, _messages: Any, **_kwargs: Any) -> ChatResult:
        self.turns += 1
        if self.turns == 1:
            return ChatResult(
                content="",
                tool_calls=[ToolCallRequest(id="c1", name="noop", arguments={})],
            )
        return ChatResult(content="报告已经写好了。")


class _ArtifactRegistry(_StubRegistry):
    """Registers a deliverable path on the run's context when its tool runs."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = path

    async def execute(self, ctx: Any, name: str, arguments: dict[str, Any]) -> ToolResult:
        ctx.register_artifact("files", self.path)
        return await super().execute(ctx, name, arguments)


async def test_claimed_deliverable_that_does_not_exist_is_not_a_success(
    _stub_provider: Any, tmp_path: Any
) -> None:
    """§4.4: success for a delivery task is the file, not the sentence about it.

    The model answers normally, so the stop reason is `final_answer` — but the file it
    claims to have produced was never written. The run must not be a success.
    """
    _stub_provider(_OneCallThenAnswerLLM())
    owner = await _make_owner()
    ghost = tmp_path / "ghost-report.md"  # never written

    async with session_scope() as session:
        outcome = await AgentExecutor(registry=_ArtifactRegistry(str(ghost)), max_steps=3).run(
            session, owner_id=owner, goal="写一份报告"
        )
        await session.commit()

    assert outcome.stopped_reason == AgentStopReason.FINAL_ANSWER.value
    assert outcome.status == RunStatus.INCOMPLETE.value

    async with session_scope() as session:
        from sqlalchemy import select

        from app.models.ai import AIRun

        run = (
            await session.execute(select(AIRun).where(AIRun.owner_id == owner))
        ).scalars().one()
        assert run.status == RunStatus.INCOMPLETE.value
        assert run.result_payload["verified"] is False
        assert run.result_payload["verification_problems"]


# ------------------------------------------------------------------ job attempt bound --


async def _seed_job(
    *,
    attempts: int,
    max_attempts: int,
    age_seconds: int = 3600,
    document_id: uuid.UUID | None = None,
) -> uuid.UUID:
    async with session_scope() as session:
        job = await JobRepository(session).enqueue(
            job_type="INGEST_DOCUMENT",
            payload={"document_id": str(document_id)} if document_id else {"document_id": "x"},
            max_attempts=max_attempts,
        )
        job.status = JobStatus.RUNNING.value
        job.attempts = attempts
        job.started_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
        job.locked_by = "dead-worker"
        await session.flush()
        return job.id


async def test_stale_job_with_attempts_left_is_requeued_with_backoff() -> None:
    job_id = await _seed_job(attempts=1, max_attempts=3)
    async with session_scope() as session:
        requeued = await JobRepository(session).requeue_stale(900)
    assert requeued == 1

    async with session_scope() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.QUEUED.value
        assert job.locked_by == ""
        # Backoff, not "right now": a crash loop must be slowed, not spun.
        assert job.scheduled_at > datetime.now(UTC)


async def test_stale_job_with_attempts_spent_becomes_terminal_failed() -> None:
    """The defect: this job used to be requeued forever."""
    job_id = await _seed_job(attempts=3, max_attempts=3)

    async with session_scope() as session:
        requeued = await JobRepository(session).requeue_stale(900)
    assert requeued == 0  # nothing went back to the queue

    async with session_scope() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED.value
        assert job.finished_at is not None
        assert job.error_message
        assert job.locked_by == ""


async def test_exhausted_job_is_not_claimed_again() -> None:
    """A retired job must not be picked up by the next worker pass."""
    await _seed_job(attempts=3, max_attempts=3)
    async with session_scope() as session:
        await JobRepository(session).requeue_stale(900)
    async with session_scope() as session:
        claimed = await JobRepository(session).claim_next("worker-2")
    assert claimed is None


async def test_exhausted_job_does_not_leave_its_document_processing() -> None:
    """Document and job must not disagree about whether work is still happening."""
    async with session_scope() as session:
        user = User(
            email=f"doc_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
            username=f"doc_{uuid.uuid4().hex[:8]}",
            display_name="Doc",
            hashed_password="x",
        )
        session.add(user)
        await session.flush()
        kb = KnowledgeBase(owner_id=user.id, name="kb")
        session.add(kb)
        await session.flush()
        document = Document(
            owner_id=user.id,
            knowledge_base_id=kb.id,
            title="stuck",
            content_hash=uuid.uuid4().hex,
            status=DocumentStatus.PROCESSING.value,
        )
        session.add(document)
        await session.flush()
        document_id = document.id

    await _seed_job(attempts=3, max_attempts=3, document_id=document_id)

    async with session_scope() as session:
        await JobRepository(session).requeue_stale(900)

    async with session_scope() as session:
        refreshed = await session.get(Document, document_id)
        assert refreshed is not None
        assert refreshed.status == DocumentStatus.FAILED.value


async def test_fresh_running_job_is_left_alone() -> None:
    """Only genuinely stale jobs are touched."""
    job_id = await _seed_job(attempts=0, max_attempts=3, age_seconds=5)
    async with session_scope() as session:
        requeued = await JobRepository(session).requeue_stale(900)
    assert requeued == 0
    async with session_scope() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.RUNNING.value

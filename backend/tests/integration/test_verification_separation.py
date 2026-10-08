"""Two questions, two fields.

`Deliverable.verification` answers *is this file any good?* — it exists, it reads
back, it has the page count it claims.

The producing run's state answers a different question: *did the run get all the
way through?* A file can be perfectly good while the run that made it did not
finish. Both are true at once, and the bug this file pins down is that they used
to be the same field.

The observed failure: a five-step run produced a real 35,700-byte, 9-slide deck,
the agent's final verification call failed on a mistyped path, the run ended
`PARTIAL` — and the deliverable was stamped 「未校验」. The user's own reading of
that screen was "it is telling me my file is bad". It was not; the file was fine
and the run was not, and the record had collapsed the two.

The regression assertions are deliberately *bidirectional*: the file verdict must
be True **and** the run state must be reported as not-ok. A test that only checked
"verified is True" would pass on a system that had simply stopped recording run
state at all.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.security import hash_password
from app.infrastructure.database import session_scope
from app.models.delivery import BuildRun, BuildStatus, Deliverable, describe_run_state
from app.models.user import User
from app.services.delivery_service import DeliveryService


async def _seed(*, status: str, suffix: str) -> tuple[uuid.UUID, uuid.UUID, Path]:
    """A user, a run in `status`, and a real readable file attached to it."""
    storage = Path(settings.storage_dir) / "verification-separation"
    storage.mkdir(parents=True, exist_ok=True)
    target = storage / f"report_{suffix}.md"
    # Real content, because the readback check asserts the file is non-empty.
    target.write_text("# 目录\n\n第一章\n第二章\n第三章\n", encoding="utf-8")

    async with session_scope() as session:
        user = User(
            email=f"verify_{suffix}@aiworkbench.dev",
            username=f"verify_{suffix}",
            display_name="Verify Test",
            hashed_password=hash_password("test1234"),
        )
        session.add(user)
        await session.flush()

        run = BuildRun(
            user_id=user.id,
            goal="[verify-separation] 做一份目录",
            title="verify-separation",
            deliverable_kind="md",
            status=status,
        )
        session.add(run)
        await session.flush()

        item = Deliverable(
            user_id=user.id,
            build_run_id=run.id,
            path=str(target),
            kind="md",
            title="report",
            size_bytes=target.stat().st_size,
        )
        session.add(item)
        await session.flush()
        return user.id, item.id, target


@pytest.mark.asyncio
async def test_a_good_file_from_an_unfinished_run_is_still_verified():
    """The regression, stated as both facts at once."""
    owner_id, item_id, _ = await _seed(status=BuildStatus.PARTIAL.value, suffix=uuid.uuid4().hex[:8])

    async with session_scope() as session:
        result = await DeliveryService(session).verify_deliverable(
            owner_id=owner_id, deliverable_id=item_id
        )

    # 1. The file's own verdict is decided by the file-level checks, and they pass.
    assert result["passed"] is True, result
    assert result["failed_count"] == 0, result
    assert all(check["ok"] for check in result["checks"]), result

    # 2. The run's state is reported, separately, and it is not ok.
    assert result["run_state"]["state"] == BuildStatus.PARTIAL.value, result
    assert result["run_state"]["ok"] is False, result
    assert "文件本身是好的" in result["run_state"]["detail"], result

    # 3. The run state is NOT one of the checks. This is the shape of the bug:
    #    a failed `run_state` check forced `passed` false.
    assert not [c for c in result["checks"] if c["name"] == "run_state"], result

    # 4. And the stored row agrees with the returned result.
    async with session_scope() as session:
        row = await session.get(Deliverable, item_id)
        assert row is not None
        assert row.verified is True
        assert row.verification["run_state"]["state"] == BuildStatus.PARTIAL.value


@pytest.mark.asyncio
async def test_a_completed_run_reports_a_good_file_and_an_ok_run():
    """The other half. A fix that always said `ok: False` would fail here."""
    owner_id, item_id, _ = await _seed(
        status=BuildStatus.COMPLETED.value, suffix=uuid.uuid4().hex[:8]
    )

    async with session_scope() as session:
        result = await DeliveryService(session).verify_deliverable(
            owner_id=owner_id, deliverable_id=item_id
        )

    assert result["passed"] is True
    assert result["run_state"]["ok"] is True
    assert result["run_state"]["state"] == BuildStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_a_broken_file_is_not_verified_even_when_the_run_completed():
    """The file verdict must still be able to fail.

    Without this, "verified is decided by file checks" could be satisfied by
    hardcoding `passed = True`, and the suite would not notice.
    """
    owner_id, item_id, target = await _seed(
        status=BuildStatus.COMPLETED.value, suffix=uuid.uuid4().hex[:8]
    )
    target.unlink()  # the file the row points at is now gone

    async with session_scope() as session:
        result = await DeliveryService(session).verify_deliverable(
            owner_id=owner_id, deliverable_id=item_id
        )

    assert result["passed"] is False
    assert result["failed_count"] >= 1
    assert any(c["name"] == "file_exists" and not c["ok"] for c in result["checks"])
    # The run finished fine; that is still reported, and it does not rescue the file.
    assert result["run_state"]["ok"] is True

    async with session_scope() as session:
        row = await session.get(Deliverable, item_id)
        assert row is not None and row.verified is False


@pytest.mark.asyncio
async def test_a_deliverable_with_no_run_has_no_run_state():
    """Absence must read as "the question does not apply", not as a false value."""
    async with session_scope() as session:
        user = User(
            email=f"norun_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
            username=f"norun_{uuid.uuid4().hex[:8]}",
            display_name="No Run",
            hashed_password=hash_password("test1234"),
        )
        session.add(user)
        await session.flush()
        storage = Path(settings.storage_dir) / "verification-separation"
        storage.mkdir(parents=True, exist_ok=True)
        target = storage / f"handmade_{uuid.uuid4().hex[:6]}.md"
        target.write_text("手写的文件\n", encoding="utf-8")
        item = Deliverable(
            user_id=user.id, build_run_id=None, path=str(target), kind="md", title="handmade"
        )
        session.add(item)
        await session.flush()
        result = await DeliveryService(session).verify_deliverable(
            owner_id=user.id, deliverable_id=item.id
        )

    assert result["passed"] is True
    assert "run_state" not in result


def test_run_state_wording_is_derived_from_the_enum():
    """Every terminal state must have a sentence, and only COMPLETED is ok.

    Written as a loop over the enum rather than three hand-picked cases: a new
    state added later fails here instead of silently falling through to the
    generic branch and being described as "还在进行中".
    """
    for state in BuildStatus:
        note = describe_run_state(state.value)
        assert note["state"] == state.value
        assert note["detail"], state
        if state.is_terminal:
            assert note["ok"] is (state is BuildStatus.COMPLETED), state

    # An unrecognised value must not be reported as ok, and must not raise.
    unknown = describe_run_state("SOMETHING_NEW")
    assert unknown["ok"] is False
    assert "SOMETHING_NEW" in unknown["detail"]

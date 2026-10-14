"""The unified result contract: one shape for "what came out of that run".

§8.1 and §8.2. Two properties are what this file exists to pin down, and both are
about the difference between a record and a claim:

* **Actions are computed against the disk, not the row.** A deliverable whose file was
  deleted must not keep a working 「打开」 button. The test deletes the file *after*
  registering it, which is the only way to reproduce the condition the user hits.
* **`next_actions` and `artifacts[].actions` do not overlap.** The two lists used to be
  one list rendered twice; a button offered in two places is a button that will drift.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.security import hash_password
from app.infrastructure.database import session_scope
from app.models.delivery import BuildRun, BuildStatus, Deliverable
from app.models.user import User
from app.services.delivery_service import DeliveryService


def _scratch(name: str) -> Path:
    folder = Path(settings.storage_dir) / "run-outcome"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


async def _seed(suffix: str, *, with_deleted: bool = False) -> tuple[uuid.UUID, uuid.UUID, Path]:
    """A completed run with one real markdown file, and optionally a vanished one."""
    kept = _scratch(f"kept_{suffix}.md")
    kept.write_text("# 报告\n\n正文\n", encoding="utf-8")

    gone = _scratch(f"gone_{suffix}.pptx")
    gone.write_bytes(b"PK\x03\x04placeholder")

    async with session_scope() as session:
        user = User(
            email=f"outcome_{suffix}@aiworkbench.dev",
            username=f"outcome_{suffix}",
            display_name="Outcome Test",
            hashed_password=hash_password("test1234"),
        )
        session.add(user)
        await session.flush()

        run = BuildRun(
            user_id=user.id,
            goal="[outcome] 写一份报告",
            title="outcome",
            deliverable_kind="mixed",
            status=BuildStatus.COMPLETED.value,
            result_summary="产出 1 个文件",
        )
        session.add(run)
        await session.flush()

        session.add(
            Deliverable(
                user_id=user.id,
                build_run_id=run.id,
                path=str(kept),
                kind="md",
                title="报告",
                size_bytes=kept.stat().st_size,
                verification={
                    "checks": [
                        {"name": "file_exists", "outcome": "pass", "ok": True, "detail": "12 字节"}
                    ],
                    "passed": True,
                    "check_count": 1,
                    "failed_count": 0,
                    "unverified_count": 0,
                },
                verified=True,
            )
        )
        if with_deleted:
            session.add(
                Deliverable(
                    user_id=user.id,
                    build_run_id=run.id,
                    path=str(gone),
                    kind="pptx",
                    title="演示稿",
                    size_bytes=gone.stat().st_size,
                )
            )
        await session.flush()
        run_id = run.id
        owner_id = user.id

    if with_deleted:
        gone.unlink()

    return owner_id, run_id, kept


@pytest.mark.asyncio
async def test_the_outcome_carries_artifacts_evidence_and_actions():
    suffix = uuid.uuid4().hex[:8]
    owner_id, run_id, _ = await _seed(suffix)

    async with session_scope() as session:
        outcome = await DeliveryService(session).outcome_for_run(
            owner_id=owner_id, build_run_id=run_id
        )

    assert outcome["status"] == BuildStatus.COMPLETED.value
    assert outcome["goal"] == "[outcome] 写一份报告"
    assert len(outcome["artifacts"]) == 1

    artifact = outcome["artifacts"][0]
    assert artifact["title"] == "报告"
    assert artifact["file_state"]["exists"] is True
    assert artifact["verified"] is True

    # The evidence travels beside the artifact, keyed by id rather than by position.
    assert [v["artifact_id"] for v in outcome["verification"]] == [artifact["id"]]
    assert outcome["verification"][0]["passed"] is True
    assert outcome["verification"][0]["check_count"] == 1

    # A markdown file can be previewed; it exists, so everything is available.
    actions = {a["id"]: a for a in artifact["actions"]}
    assert actions["preview"]["enabled"] is True
    assert actions["open"]["enabled"] is True
    assert actions["regenerate"]["enabled"] is True

    # Run-level actions are the ones that are not about a single file.
    assert [a["id"] for a in outcome["next_actions"]] == ["verify", "open_deliverables"]
    assert outcome["diagnostic_ref"]["build_run_id"] == str(run_id)


@pytest.mark.asyncio
async def test_a_deleted_file_disables_its_buttons_and_is_named_in_the_summary():
    """The condition §8.3 requires the UI to stop lying about."""
    suffix = uuid.uuid4().hex[:8]
    owner_id, run_id, _ = await _seed(suffix, with_deleted=True)

    async with session_scope() as session:
        outcome = await DeliveryService(session).outcome_for_run(
            owner_id=owner_id, build_run_id=run_id
        )

    assert len(outcome["artifacts"]) == 2
    vanished = next(a for a in outcome["artifacts"] if a["title"] == "演示稿")

    assert vanished["file_state"]["exists"] is False
    assert vanished["file_state"]["reason"] != ""

    actions = {a["id"]: a for a in vanished["actions"]}
    assert actions["open"]["enabled"] is False
    assert actions["preview"]["enabled"] is False
    # The reason is attached to the action, so it arrives before the click.
    assert actions["open"]["reason"] == vanished["file_state"]["reason"]

    # The summary counts it rather than reporting a clean delivery.
    assert "已经不在了" in outcome["summary"]
    assert "演示稿" in outcome["summary"]


@pytest.mark.asyncio
async def test_a_non_text_kind_has_no_preview_and_says_why():
    """A disabled button with a reason beats a button that opens nothing."""
    suffix = uuid.uuid4().hex[:8]
    owner_id, run_id, _ = await _seed(suffix, with_deleted=True)

    async with session_scope() as session:
        outcome = await DeliveryService(session).outcome_for_run(
            owner_id=owner_id, build_run_id=run_id
        )

    deck = next(a for a in outcome["artifacts"] if a["kind"] == "pptx")
    # Restore the file so the only reason left is the format.
    Path(deck["path"]).write_bytes(b"PK\x03\x04placeholder")
    try:
        async with session_scope() as session:
            outcome = await DeliveryService(session).outcome_for_run(
                owner_id=owner_id, build_run_id=run_id
            )
        deck = next(a for a in outcome["artifacts"] if a["kind"] == "pptx")
        preview = next(a for a in deck["actions"] if a["id"] == "preview")
        assert preview["enabled"] is False
        assert "系统程序" in preview["reason"]
        assert deck["file_state"]["exists"] is True
    finally:
        Path(deck["path"]).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Preview
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_preview_returns_the_text():
    suffix = uuid.uuid4().hex[:8]
    owner_id, run_id, _ = await _seed(suffix)

    async with session_scope() as session:
        service = DeliveryService(session)
        outcome = await service.outcome_for_run(owner_id=owner_id, build_run_id=run_id)
        payload = await service.read_text(
            owner_id=owner_id, deliverable_id=uuid.UUID(outcome["artifacts"][0]["id"])
        )

    assert payload["text"] == "# 报告\n\n正文\n"
    assert payload["truncated"] is False
    assert payload["reason"] == ""


@pytest.mark.asyncio
async def test_preview_of_a_deleted_file_says_so_rather_than_showing_nothing():
    suffix = uuid.uuid4().hex[:8]
    owner_id, run_id, _ = await _seed(suffix)
    Path(_scratch(f"kept_{suffix}.md")).unlink()

    async with session_scope() as session:
        service = DeliveryService(session)
        outcome = await service.outcome_for_run(owner_id=owner_id, build_run_id=run_id)
        payload = await service.read_text(
            owner_id=owner_id, deliverable_id=uuid.UUID(outcome["artifacts"][0]["id"])
        )

    assert payload["text"] == ""
    assert "不在了" in payload["reason"]


@pytest.mark.asyncio
async def test_preview_reports_truncation_instead_of_ending_silently():
    suffix = uuid.uuid4().hex[:8]
    owner_id, run_id, _ = await _seed(suffix)
    Path(_scratch(f"kept_{suffix}.md")).write_text("字" * 500, encoding="utf-8")

    async with session_scope() as session:
        service = DeliveryService(session)
        outcome = await service.outcome_for_run(owner_id=owner_id, build_run_id=run_id)
        payload = await service.read_text(
            owner_id=owner_id,
            deliverable_id=uuid.UUID(outcome["artifacts"][0]["id"]),
            max_chars=100,
        )

    assert payload["truncated"] is True
    assert len(payload["text"]) == 100


@pytest.mark.asyncio
async def test_preview_refuses_a_mis_encoded_file_by_name():
    suffix = uuid.uuid4().hex[:8]
    owner_id, run_id, _ = await _seed(suffix)
    Path(_scratch(f"kept_{suffix}.md")).write_bytes("中文".encode("gbk"))

    async with session_scope() as session:
        service = DeliveryService(session)
        outcome = await service.outcome_for_run(owner_id=owner_id, build_run_id=run_id)
        payload = await service.read_text(
            owner_id=owner_id, deliverable_id=uuid.UUID(outcome["artifacts"][0]["id"])
        )

    assert payload["text"] == ""
    assert "UTF-8" in payload["reason"]


@pytest.mark.asyncio
async def test_another_account_cannot_read_the_outcome():
    """Owner scoping is checked on the read path too, not only on the write path."""
    suffix = uuid.uuid4().hex[:8]
    _, run_id, _ = await _seed(suffix)
    stranger = uuid.uuid4()

    async with session_scope() as session:
        with pytest.raises(NotFoundError):
            await DeliveryService(session).outcome_for_run(
                owner_id=stranger, build_run_id=run_id
            )

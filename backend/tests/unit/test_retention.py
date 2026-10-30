"""Retention: the rules, and the two ways they could quietly destroy something.

A retention rule is easy to write and easy to write *wrongly*, and the failure is not an
error — it is the quiet disappearance of the one snapshot that could have undone a run.
The assertions here are mostly about what is **not** deleted:

* a pinned snapshot is never a candidate, however old or however far down the list;
* the newest snapshot for a run is never a candidate — it is the version rollback uses, so
  deleting it makes the run unrollbackable while a "version" is nominally retained;
* a snapshot whose run is still going is never a candidate;
* a snapshot inside *either* limit is kept when `require_both_limits` is on;
* an unparseable timestamp is kept.

And about the archive actually being restorable. The first version wrote only
`manifest.json` into a JSONL file; a snapshot directory is `manifest.json` **plus `files/`**,
so that "archive" restored a snapshot that could not restore anything. These tests use real
directories with real content, and check the content comes back byte-for-byte.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.ai.agent.retention import (
    CleanupItem,
    CleanupPlan,
    RetentionPolicy,
    apply_audit_cleanup,
    apply_snapshot_cleanup,
    plan_audit_cleanup,
    plan_snapshot_cleanup,
    restore_snapshot_archive,
    verify_snapshot_archive,
)

NOW = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)


@dataclass
class FakeSnapshot:
    id: str
    created_at: str
    run_id: str = ""
    total_bytes: int = 100
    pinned: bool = False


def _snapshot(
    snapshot_id: str, *, days_old: float, run_id: str = "", pinned: bool = False
) -> FakeSnapshot:
    # Snapshots are written with `time.strftime("%Y-%m-%d %H:%M:%S")` — **local** time with
    # no offset. Producing a UTC string here would test a format the product never writes,
    # and would hide the timezone bug this used to have.
    created = (NOW - timedelta(days=days_old)).astimezone()
    return FakeSnapshot(
        id=snapshot_id,
        created_at=created.strftime("%Y-%m-%d %H:%M:%S"),
        run_id=run_id,
        pinned=pinned,
    )


def _policy(**overrides) -> RetentionPolicy:
    base = {
        "snapshot_max_count": 2,
        "snapshot_max_age_days": 10,
        "require_both_limits": True,
    }
    base.update(overrides)
    return RetentionPolicy(**base)


# --------------------------------------------------------------------------- #
# What is protected
# --------------------------------------------------------------------------- #
def test_a_pinned_snapshot_is_never_a_candidate() -> None:
    """Pinning is the user saying "keep this". No threshold overrides it."""
    snapshots = [
        _snapshot("new", days_old=1),
        _snapshot("mid", days_old=5),
        _snapshot("old", days_old=90, pinned=True),
    ]
    plan = plan_snapshot_cleanup(snapshots, _policy(), now=NOW)

    assert "old" not in [item.id for item in plan.candidates]
    protected = next(item for item in plan.protected if item.id == "old")
    assert "用户明确保留" in protected.reason


def test_the_newest_snapshot_for_a_run_is_never_a_candidate() -> None:
    """It is the version rollback restores from.

    Deleting it would leave the run unrollbackable even though older snapshots of the same
    run were kept — "a version is retained" and "the run can be undone" are not the same
    statement, and only the second one matters.
    """
    snapshots = [
        _snapshot("run-a-old", days_old=200, run_id="run-a"),
        _snapshot("run-a-new", days_old=100, run_id="run-a"),
    ]
    plan = plan_snapshot_cleanup(snapshots, _policy(snapshot_max_count=1), now=NOW)

    assert "run-a-new" not in [item.id for item in plan.candidates]
    assert "run-a-old" in [item.id for item in plan.candidates]
    protected = next(item for item in plan.protected if item.id == "run-a-new")
    assert "唯一可回滚" in protected.reason


def test_a_snapshot_whose_run_is_still_going_is_protected() -> None:
    """A live run's snapshot is the only way to undo it, so it stays.

    Both runs have an older sibling, so "the newest for its run" is not what protects
    them — the difference between the two is only whether the run is still going.
    """
    snapshots = [
        _snapshot("live-old", days_old=400, run_id="run-live"),
        _snapshot("live-new", days_old=300, run_id="run-live"),
        _snapshot("dead-old", days_old=400, run_id="run-dead"),
        _snapshot("dead-new", days_old=300, run_id="run-dead"),
    ]
    plan = plan_snapshot_cleanup(
        snapshots, _policy(snapshot_max_count=1), protected_run_ids={"run-live"}, now=NOW
    )

    candidates = [item.id for item in plan.candidates]
    assert "live-old" not in candidates, "a live run's older snapshot was offered up"
    assert "dead-old" in candidates, "a finished run's older snapshot should be reclaimable"
    protected = next(item for item in plan.protected if item.id == "live-old")
    assert "运行仍在进行" in protected.reason


def test_within_either_limit_is_kept_when_both_are_required() -> None:
    """Old but still inside the count limit, or new but beyond the count limit — both stay.

    The conservative reading of "supports count and time": exceeding one bound is not
    enough, because a snapshot that is old but among the most recent few is one somebody
    may still be looking at.
    """
    snapshots = [
        _snapshot("recent-1", days_old=1),
        _snapshot("recent-2", days_old=2),
        _snapshot("old-but-in-count", days_old=900),
    ]
    plan = plan_snapshot_cleanup(snapshots, _policy(snapshot_max_count=5), now=NOW)
    assert plan.candidates == [], [item.id for item in plan.candidates]

    many = [_snapshot(f"s{index}", days_old=1) for index in range(6)]
    plan = plan_snapshot_cleanup(many, _policy(snapshot_max_count=2), now=NOW)
    assert plan.candidates == [], [item.id for item in plan.candidates]


def test_only_exceeding_both_limits_makes_a_candidate() -> None:
    snapshots = [
        _snapshot("keep-1", days_old=1),
        _snapshot("keep-2", days_old=2),
        _snapshot("candidate", days_old=100),
    ]
    plan = plan_snapshot_cleanup(snapshots, _policy(), now=NOW)

    assert [item.id for item in plan.candidates] == ["candidate"]
    reason = plan.candidates[0].reason
    assert "超出数量上限" in reason and "天" in reason
    assert plan.candidates[0].run_id == ""


def test_an_unparseable_timestamp_is_kept() -> None:
    """A date nobody can read is a reason to leave something alone, not to delete it."""
    snapshots = [
        _snapshot("keep-1", days_old=1),
        _snapshot("keep-2", days_old=2),
        FakeSnapshot(id="mystery", created_at="not a date"),
    ]
    plan = plan_snapshot_cleanup(snapshots, _policy(), now=NOW)

    assert "mystery" not in [item.id for item in plan.candidates]
    protected = next(item for item in plan.protected if item.id == "mystery")
    assert "无法解析" in protected.reason


def test_turning_off_both_limits_reclaims_more() -> None:
    """The stricter rule is a choice, and the difference is visible."""
    snapshots = [
        _snapshot("keep-1", days_old=1),
        _snapshot("keep-2", days_old=2),
        _snapshot("old", days_old=100),
    ]
    strict = plan_snapshot_cleanup(snapshots, _policy(), now=NOW)
    loose = plan_snapshot_cleanup(snapshots, _policy(require_both_limits=False), now=NOW)
    assert len(loose.candidates) >= len(strict.candidates)
    assert "old" in [item.id for item in loose.candidates]


def test_a_zero_limit_really_does_select_everything_unprotected() -> None:
    """The age rule has to actually fire, or the protection rules are untested.

    This is the test that would have caught the timezone bug. Snapshots are written in
    local time; the parser used to stamp `tzinfo=UTC` on them, so on a UTC+8 machine every
    snapshot looked eight hours old *in the future*. A negative age is below any limit, so
    every snapshot counted as "within the age range" and the age rule never applied to
    anything. It failed safe — nothing was deleted — which is exactly why it went unnoticed
    until a test set the limit to zero and still got no candidates.
    """
    snapshots = [_snapshot("a", days_old=1), _snapshot("b", days_old=2), _snapshot("c", days_old=3)]
    plan = plan_snapshot_cleanup(
        snapshots, _policy(snapshot_max_count=0, snapshot_max_age_days=0), now=NOW
    )
    assert len(plan.candidates) == 3, [item.id for item in plan.candidates]
    assert all("超出数量上限" in item.reason for item in plan.candidates)


def test_a_snapshot_taken_just_now_is_within_a_one_day_limit() -> None:
    """The other side of the same boundary, so the fix is not "everything is old now"."""
    snapshots = [_snapshot("fresh", days_old=0.01), _snapshot("old", days_old=5)]
    plan = plan_snapshot_cleanup(
        snapshots, _policy(snapshot_max_count=0, snapshot_max_age_days=1), now=NOW
    )
    assert [item.id for item in plan.candidates] == ["old"]


def test_the_plan_reports_what_it_would_free() -> None:
    snapshots = [
        _snapshot("a", days_old=1),
        _snapshot("b", days_old=2),
        _snapshot("c", days_old=100),
    ]
    plan = plan_snapshot_cleanup(snapshots, _policy(), now=NOW)
    payload = plan.as_dict()
    assert payload["total"] == 3
    assert payload["reclaimable_bytes"] == 100
    assert payload["candidates"][0]["reason"]
    assert payload["policy"]["snapshot_max_count"] == 2


def test_planning_deletes_nothing(tmp_path: Path) -> None:
    """The whole point of separating decide from do."""
    root = _make_snapshot_root(tmp_path, ["a", "b", "c"])
    manager = RealManager(root)
    before = _tree_snapshot(root)

    snapshots = [
        _snapshot("a", days_old=1),
        _snapshot("b", days_old=2),
        _snapshot("c", days_old=100),
    ]
    plan = plan_snapshot_cleanup(snapshots, _policy(), now=NOW)
    assert plan.as_dict()["candidate_count"] == 1

    assert _tree_snapshot(root) == before, "planning changed the snapshot store"
    assert manager.deleted == []


# --------------------------------------------------------------------------- #
# Real directories
# --------------------------------------------------------------------------- #
def _make_snapshot_root(tmp_path: Path, ids: list[str]) -> Path:
    """A snapshot store with real `manifest.json` + `files/` content."""
    root = tmp_path / "snapshots"
    for snapshot_id in ids:
        directory = root / snapshot_id
        (directory / "files").mkdir(parents=True, exist_ok=True)
        (directory / "files" / "note.txt").write_text(f"content of {snapshot_id}", encoding="utf-8")
        (directory / "manifest.json").write_text(
            json.dumps({"id": snapshot_id, "root": "C:/ws", "files": [{"path": "note.txt"}]}),
            encoding="utf-8",
        )
    return root


def _tree_snapshot(root: Path) -> dict[str, str]:
    """path → content, for comparing a store before and after."""
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return out


class RealManager:
    """A manager backed by real directories, standing in for `WorkspaceSnapshotManager`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.deleted: list[str] = []

    def get(self, snapshot_id: str):
        directory = self.root / snapshot_id
        if not directory.is_dir():
            return None

        class _View:
            def __init__(self, path: Path) -> None:
                self.directory = path

            def as_dict(self, *, include_files: bool = False) -> dict:
                return {"id": self.directory.name}

        return _View(directory)

    def delete(self, snapshot_id: str) -> bool:
        directory = self.root / snapshot_id
        if not directory.is_dir():
            return False
        shutil.rmtree(directory)
        self.deleted.append(snapshot_id)
        return True


def _plan_for(ids: list[str], **overrides) -> CleanupPlan:
    snapshots = [
        _snapshot(ids[0], days_old=1),
        _snapshot(ids[1], days_old=2),
        _snapshot(ids[2], days_old=100),
    ]
    return plan_snapshot_cleanup(snapshots, _policy(**overrides), now=NOW)


def test_apply_archives_the_whole_directory_then_deletes(tmp_path: Path) -> None:
    """`manifest.json` alone is not an archive — `files/` is what a restore reads."""
    root = _make_snapshot_root(tmp_path, ["keep-1", "keep-2", "doomed"])
    manager = RealManager(root)

    report = apply_snapshot_cleanup(
        manager, _plan_for(["keep-1", "keep-2", "doomed"]), archive_dir=tmp_path / "archive"
    )

    assert report["deleted"] == 1
    assert report["archived"] == 1
    assert report["failed"] == []
    assert manager.deleted == ["doomed"]

    archive_root = Path(report["archive_dir"])
    assert (archive_root / "doomed" / "manifest.json").is_file()
    assert (archive_root / "doomed" / "files" / "note.txt").read_text(encoding="utf-8") == (
        "content of doomed"
    )

    index = json.loads((archive_root / "index.json").read_text(encoding="utf-8"))
    assert len(index["entries"]) == 1
    assert index["entries"][0]["id"] == "doomed"
    assert index["entries"][0]["checksum"]
    assert index["entries"][0]["reason"]


def test_a_tampered_archive_stops_the_deletion(tmp_path: Path, monkeypatch) -> None:
    """A checksum is what catches wrong bytes; a file count only catches a truncated write."""
    root = _make_snapshot_root(tmp_path, ["keep-1", "keep-2", "doomed"])
    manager = RealManager(root)

    import app.ai.agent.retention as retention

    real_verify = retention.verify_snapshot_archive
    calls = {"n": 0}

    def tampering_verify(archive_root: Path):
        calls["n"] += 1
        result = real_verify(archive_root)
        return {
            "ok": False,
            "verified": 0,
            "mismatched": ["doomed: 校验值不一致"],
            "index": result["index"],
        }

    monkeypatch.setattr(retention, "verify_snapshot_archive", tampering_verify)
    report = apply_snapshot_cleanup(
        manager, _plan_for(["keep-1", "keep-2", "doomed"]), archive_dir=tmp_path / "archive"
    )

    assert calls["n"] == 1, "the archive was never verified"
    assert report["deleted"] == 0
    assert manager.deleted == [], "something was deleted despite the archive failing verification"
    assert report["failed"]


def test_a_failed_archive_stops_the_deletion(tmp_path: Path) -> None:
    """An archive that cannot be written must stop the deletion, not precede it."""
    root = _make_snapshot_root(tmp_path, ["keep-1", "keep-2", "doomed"])
    manager = RealManager(root)

    report = apply_snapshot_cleanup(
        manager, _plan_for(["keep-1", "keep-2", "doomed"]), archive_dir=tmp_path / "nul" / "\0bad"
    )

    assert report["deleted"] == 0
    assert manager.deleted == [], "something was deleted despite the archive failing"
    assert report["failed"], "the failure was not reported"


def test_an_empty_plan_reports_and_changes_nothing(tmp_path: Path) -> None:
    root = _make_snapshot_root(tmp_path, ["keep-1", "keep-2"])
    manager = RealManager(root)
    before = _tree_snapshot(root)

    plan = plan_snapshot_cleanup(
        [_snapshot("keep-1", days_old=1), _snapshot("keep-2", days_old=2)], _policy(), now=NOW
    )
    report = apply_snapshot_cleanup(manager, plan, archive_dir=tmp_path / "archive")

    assert report["deleted"] == 0
    assert report["notes"] == ["没有候选，未做任何改动"]
    assert manager.deleted == []
    assert _tree_snapshot(root) == before


# --------------------------------------------------------------------------- #
# Restore
# --------------------------------------------------------------------------- #
def test_verify_reports_a_matching_archive(tmp_path: Path) -> None:
    root = _make_snapshot_root(tmp_path, ["keep-1", "keep-2", "doomed"])
    manager = RealManager(root)
    report = apply_snapshot_cleanup(
        manager, _plan_for(["keep-1", "keep-2", "doomed"]), archive_dir=tmp_path / "archive"
    )

    result = verify_snapshot_archive(Path(report["archive_dir"]))
    assert result["ok"] is True
    assert result["verified"] == 1


def test_verify_reports_a_tampered_archive(tmp_path: Path) -> None:
    root = _make_snapshot_root(tmp_path, ["keep-1", "keep-2", "doomed"])
    manager = RealManager(root)
    report = apply_snapshot_cleanup(
        manager, _plan_for(["keep-1", "keep-2", "doomed"]), archive_dir=tmp_path / "archive"
    )
    archive_root = Path(report["archive_dir"])

    (archive_root / "doomed" / "files" / "note.txt").write_text("tampered", encoding="utf-8")

    result = verify_snapshot_archive(archive_root)
    assert result["ok"] is False
    assert result["mismatched"]


def test_restore_brings_the_content_back_byte_for_byte(tmp_path: Path) -> None:
    """The point of the archive: after a delete, the snapshot comes back intact."""
    root = _make_snapshot_root(tmp_path, ["keep-1", "keep-2", "doomed"])
    original = _tree_snapshot(root / "doomed")
    manager = RealManager(root)
    report = apply_snapshot_cleanup(
        manager, _plan_for(["keep-1", "keep-2", "doomed"]), archive_dir=tmp_path / "archive"
    )
    assert not (root / "doomed").exists(), "the snapshot was not actually deleted"

    restored = restore_snapshot_archive(Path(report["archive_dir"]), snapshot_root=root)

    assert restored["failed"] == []
    assert restored["restored"] == 1
    assert restored["restored_ids"] == ["doomed"]
    assert _tree_snapshot(root / "doomed") == original, (
        "the restored content differs from the original"
    )


def test_restore_refuses_a_tampered_archive(tmp_path: Path) -> None:
    """A restore from an archive that no longer matches its index must not run."""
    root = _make_snapshot_root(tmp_path, ["keep-1", "keep-2", "doomed"])
    manager = RealManager(root)
    report = apply_snapshot_cleanup(
        manager, _plan_for(["keep-1", "keep-2", "doomed"]), archive_dir=tmp_path / "archive"
    )
    archive_root = Path(report["archive_dir"])
    (archive_root / "doomed" / "files" / "note.txt").write_text("tampered", encoding="utf-8")

    restored = restore_snapshot_archive(archive_root, snapshot_root=root)

    assert restored["restored"] == 0
    assert restored["archive_ok"] is False
    assert restored["failed"]
    assert not (root / "doomed").exists(), "a tampered archive was restored anyway"


def test_restore_does_not_clobber_a_snapshot_that_is_still_there(tmp_path: Path) -> None:
    """Restoring over a live snapshot would destroy the version the user is relying on."""
    root = _make_snapshot_root(tmp_path, ["keep-1", "keep-2", "doomed"])
    manager = RealManager(root)
    report = apply_snapshot_cleanup(
        manager, _plan_for(["keep-1", "keep-2", "doomed"]), archive_dir=tmp_path / "archive"
    )

    first = restore_snapshot_archive(Path(report["archive_dir"]), snapshot_root=root)
    assert first["restored"] == 1

    second = restore_snapshot_archive(Path(report["archive_dir"]), snapshot_root=root)
    assert second["restored"] == 0
    assert second["skipped"], "the existing snapshot was not reported as skipped"

    forced = restore_snapshot_archive(
        Path(report["archive_dir"]), snapshot_root=root, overwrite=True
    )
    assert forced["restored"] == 1


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
@dataclass
class FakeAuditRow:
    id: str
    created_at: datetime
    ai_run_id: str | None = None


def test_audit_rows_of_a_live_run_are_protected() -> None:
    rows = [
        FakeAuditRow(id="old-live", created_at=NOW - timedelta(days=400), ai_run_id="run-live"),
        FakeAuditRow(id="old-dead", created_at=NOW - timedelta(days=400), ai_run_id="run-dead"),
    ]
    policy = _policy(audit_max_count=0, audit_max_age_days=1)
    plan = plan_audit_cleanup(rows, policy, protected_run_ids={"run-live"}, now=NOW)

    candidates = [item.id for item in plan.candidates]
    assert "old-live" not in candidates
    assert "old-dead" in candidates
    protected = next(item for item in plan.protected if item.id == "old-live")
    assert "运行仍在进行" in protected.reason


@pytest.mark.asyncio
async def test_apply_audit_cleanup_reports_archive_failure_without_deleting(tmp_path: Path) -> None:
    """The audit path has the same archive-first guarantee as the snapshot path.

    The rows are read first (that is unavoidable — they are what gets archived), so the
    session is used. What must not happen is the *delete*: it runs only after the archive
    has been written and read back, and here the archive path cannot be created.
    """
    from datetime import datetime as _dt

    class _Row:
        id = "x"
        ai_run_id = None
        step_index = 0
        tool_name = "t"
        category = "general"
        decision = "allow"
        reason = ""
        risk_level = "low"
        permission_level = 1
        target = ""
        status = "ok"
        exit_code = 0
        created_at = _dt(2020, 1, 1, tzinfo=UTC)

    class FakeResult:
        def scalars(self):
            return [_Row()]

    class FakeSession:
        def __init__(self) -> None:
            self.deleted = False

        async def execute(self, *args, **kwargs):
            return FakeResult()

        async def commit(self) -> None:  # pragma: no cover - must not be reached
            self.deleted = True

        async def rollback(self) -> None:  # pragma: no cover
            self.deleted = True

    plan = CleanupPlan(
        kind="audit",
        total=5,
        candidates=[CleanupItem(id="x", created_at="2020-01-01T00:00:00", reason="too old")],
    )
    session = FakeSession()
    # A path that cannot exist: `nul` is a reserved device name on Windows and the NUL
    # byte is invalid in a path anywhere.
    report = await apply_audit_cleanup(session, plan, archive_dir=tmp_path / "nul" / "\0bad")
    assert report["deleted"] == 0
    assert report["failed"], "the archive failure was not reported"
    assert session.deleted is False, "a delete was attempted despite the archive failing"

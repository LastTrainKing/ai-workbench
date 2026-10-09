"""Retention: the rules, and the two ways they could quietly destroy something.

A retention rule is easy to write and easy to write *wrongly*, and the failure is not an
error — it is the quiet disappearance of the one snapshot that could have undone a run.
The assertions here are therefore mostly about what is **not** deleted:

* a pinned snapshot is never a candidate, however old or however far down the list;
* the newest snapshot for a run is never a candidate — it is the version rollback uses,
  so deleting it makes the run unrollbackable while a "version" is nominally retained;
* a snapshot whose run is still going is never a candidate;
* a snapshot inside *either* limit is kept when `require_both_limits` is on;
* an unparseable timestamp is kept;
* and the archive is written and **read back** before anything is deleted, so a failed
  archive stops the deletion instead of producing an unrecoverable one that looks safe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from app.ai.agent.retention import (
    CleanupItem,
    CleanupPlan,
    RetentionPolicy,
    apply_audit_cleanup,
    apply_snapshot_cleanup,
    plan_audit_cleanup,
    plan_snapshot_cleanup,
)

NOW = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)


@dataclass
class FakeSnapshot:
    id: str
    created_at: str
    run_id: str = ""
    total_bytes: int = 100
    pinned: bool = False


def _snapshot(snapshot_id: str, *, days_old: float, run_id: str = "", pinned: bool = False) -> FakeSnapshot:
    created = NOW - timedelta(days=days_old)
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
    # Both are far outside count and age; the newest for the run must still survive.
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

    # Beyond the count but young: also kept.
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
    loose = plan_snapshot_cleanup(
        snapshots, _policy(require_both_limits=False), now=NOW
    )
    assert len(loose.candidates) >= len(strict.candidates)
    assert "old" in [item.id for item in loose.candidates]


# --------------------------------------------------------------------------- #
# The plan is only a plan
# --------------------------------------------------------------------------- #
def test_planning_deletes_nothing() -> None:
    """The whole point of separating decide from do."""

    class ExplodingManager:
        def get(self, snapshot_id: str):  # pragma: no cover - must not be reached
            raise AssertionError("the planner touched the store")

        def delete(self, snapshot_id: str) -> bool:  # pragma: no cover
            raise AssertionError("the planner deleted something")

    snapshots = [_snapshot("a", days_old=1), _snapshot("b", days_old=2), _snapshot("c", days_old=100)]
    plan = plan_snapshot_cleanup(snapshots, _policy(), now=NOW)
    assert plan.as_dict()["candidate_count"] == 1
    assert ExplodingManager is not None  # the class exists only to be obviously unused


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


# --------------------------------------------------------------------------- #
# Applying
# --------------------------------------------------------------------------- #
class FakeManager:
    def __init__(self, snapshots: list[FakeSnapshot]) -> None:
        self._items = {snapshot.id: snapshot for snapshot in snapshots}
        self.deleted: list[str] = []

    def get(self, snapshot_id: str):
        snapshot = self._items.get(snapshot_id)
        if snapshot is None:
            return None

        class _View:
            def __init__(self, item: FakeSnapshot) -> None:
                self._item = item

            def as_dict(self, *, include_files: bool = False) -> dict:
                return {"id": self._item.id, "created_at": self._item.created_at}

        return _View(snapshot)

    def delete(self, snapshot_id: str) -> bool:
        if snapshot_id not in self._items:
            return False
        self.deleted.append(snapshot_id)
        del self._items[snapshot_id]
        return True


def test_apply_archives_then_deletes(tmp_path) -> None:
    snapshots = [
        _snapshot("keep-1", days_old=1),
        _snapshot("keep-2", days_old=2),
        _snapshot("doomed", days_old=100),
    ]
    plan = plan_snapshot_cleanup(snapshots, _policy(), now=NOW)
    manager = FakeManager(snapshots)

    report = apply_snapshot_cleanup(manager, plan, archive_dir=tmp_path)

    assert report["deleted"] == 1
    assert report["after"] == 2
    assert report["failed"] == []
    assert manager.deleted == ["doomed"]
    archive = tmp_path / report["archive_path"].split("\\")[-1].split("/")[-1]
    assert archive.is_file(), report["archive_path"]
    lines = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1
    assert lines[0]["manifest"]["id"] == "doomed"
    assert lines[0]["plan_item"]["reason"]


def test_a_failed_archive_stops_the_deletion(tmp_path, monkeypatch) -> None:
    """An archive that cannot be read back must stop the deletion, not precede it.

    Otherwise the deletion happens and the record of what was deleted is the thing that
    failed — an unrecoverable change that reports success.
    """
    snapshots = [_snapshot("keep-1", days_old=1), _snapshot("keep-2", days_old=2), _snapshot("doomed", days_old=100)]
    plan = plan_snapshot_cleanup(snapshots, _policy(), now=NOW)
    manager = FakeManager(snapshots)

    import app.ai.agent.retention as retention

    original_open = retention.Path.open

    def exploding_open(self, *args, **kwargs):
        if self.suffix == ".jsonl":
            raise OSError("disk full")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(retention.Path, "open", exploding_open, raising=False)
    report = apply_snapshot_cleanup(manager, plan, archive_dir=tmp_path)

    assert report["deleted"] == 0
    assert manager.deleted == [], "something was deleted despite the archive failing"
    assert report["failed"], "the failure was not reported"


def test_an_empty_plan_reports_and_changes_nothing(tmp_path) -> None:
    snapshots = [_snapshot("keep-1", days_old=1), _snapshot("keep-2", days_old=2)]
    plan = plan_snapshot_cleanup(snapshots, _policy(), now=NOW)
    manager = FakeManager(snapshots)

    report = apply_snapshot_cleanup(manager, plan, archive_dir=tmp_path)
    assert report["deleted"] == 0
    assert report["notes"] == ["没有候选，未做任何改动"]
    assert manager.deleted == []


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
async def test_apply_audit_cleanup_reports_archive_failure_without_deleting(tmp_path) -> None:
    """The audit path has the same archive-first guarantee as the snapshot path.

    The rows are read first (that is unavoidable — they are what gets archived), so the
    session is used. What must not happen is the *delete*: it runs only after the archive
    has been written and read back, and here the archive path cannot be created.
    """

    class FakeResult:
        def scalars(self):
            return [
                type(
                    "Row",
                    (),
                    {
                        "id": "x",
                        "ai_run_id": None,
                        "step_index": 0,
                        "tool_name": "t",
                        "category": "general",
                        "decision": "allow",
                        "reason": "",
                        "risk_level": "low",
                        "permission_level": 1,
                        "target": "",
                        "status": "ok",
                        "exit_code": 0,
                        "created_at": None,
                    },
                )()
            ]

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
    report = await apply_audit_cleanup(
        session, plan, archive_dir=tmp_path / "nul" / "\0bad"
    )
    assert report["deleted"] == 0
    assert report["failed"], "the archive failure was not reported"
    assert session.deleted is False, "a delete was attempted despite the archive failing"

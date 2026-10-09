"""Retention: what may be reclaimed, and the proof that it is safe to do so.

Snapshots and audit rows only ever grew. 1,269 snapshots and 3,021 audit rows had
accumulated, and nothing in the product could say which of them still mattered. A
retention rule is easy to write and easy to write *wrongly*: the failure mode is not an
error, it is the quiet deletion of the one snapshot that could have undone a run.

So this module separates deciding from doing.

* ``plan_snapshot_cleanup`` and ``plan_audit_cleanup`` **only decide**. They return a plan
  that names every candidate, every protected item, and the rule that produced each
  answer. Nothing is deleted.
* ``apply_snapshot_cleanup`` and ``apply_audit_cleanup`` execute a plan, and they archive
  before they delete — writing the manifests and rows to a file, reading it back to count
  what was written, and refusing to delete if the archive does not read back.

## What is never a candidate

* **Pinned.** The user said keep it. There is no age or count that overrides that.
* **Its run is still going.** A live run's snapshot is the only way to undo it.
* **The newest snapshot for its run.** Rolling back uses the most recent snapshot for the
  run, so the newest one is the recoverable version. Deleting it would leave the run
  unrollbackable even though a "version" of it was nominally retained.
* **Inside either limit.** Age and count are both respected, and by default a snapshot has
  to exceed **both** to become a candidate — the conservative reading of "supports count
  and time", because a snapshot that is old but recent enough to be within the count is
  one somebody may still be looking at.

## Default is off

``RetentionPolicy.enabled`` is ``False``. Nothing here runs at startup, and nothing here
deletes anything unless a caller builds a plan and applies it explicitly. The first
release is a preview.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import runtime_root
from app.core.logging import get_logger

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RetentionPolicy:
    """The rules. Every value is a decision someone can point at and change."""

    #: Off by default. A retention policy that starts deleting on first run is not
    #: conservative, whatever its thresholds say.
    enabled: bool = False

    #: Keep at most this many snapshots (across all runs).
    snapshot_max_count: int = 200
    #: Keep snapshots younger than this.
    snapshot_max_age_days: int = 30

    #: Keep at most this many audit rows.
    audit_max_count: int = 50_000
    #: Keep audit rows younger than this.
    audit_max_age_days: int = 180

    #: A candidate must exceed **both** the count and the age limit. Setting this to False
    #: makes either limit sufficient, which reclaims more and risks more.
    require_both_limits: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #
@dataclass
class CleanupItem:
    """One thing the plan has an opinion about."""

    id: str
    created_at: str
    reason: str
    size_bytes: int = 0
    run_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CleanupPlan:
    """What would happen, and why, without anything having happened."""

    kind: str
    total: int
    candidates: list[CleanupItem] = field(default_factory=list)
    protected: list[CleanupItem] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)
    generated_at: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(item.size_bytes for item in self.candidates)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "total": self.total,
            "candidate_count": len(self.candidates),
            "protected_count": len(self.protected),
            "reclaimable_bytes": self.reclaimable_bytes,
            "policy": self.policy,
            "generated_at": self.generated_at,
            "notes": self.notes,
            "candidates": [item.as_dict() for item in self.candidates],
            "protected": [item.as_dict() for item in self.protected],
        }


def _parse_timestamp(value: str) -> datetime | None:
    """Snapshots store ``%Y-%m-%d %H:%M:%S``; accept ISO too."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _age_days(created_at: str, now: datetime) -> float | None:
    moment = _parse_timestamp(created_at)
    if moment is None:
        return None
    return (now - moment).total_seconds() / 86400.0


# --------------------------------------------------------------------------- #
# Snapshots
# --------------------------------------------------------------------------- #
def plan_snapshot_cleanup(
    snapshots: list[Any],
    policy: RetentionPolicy,
    *,
    protected_run_ids: set[str] | None = None,
    now: datetime | None = None,
) -> CleanupPlan:
    """Decide which snapshots may be reclaimed. Deletes nothing.

    ``protected_run_ids`` is every run that is currently live or still marked ``RUNNING``
    in the database — the caller supplies it rather than this function guessing, because
    "is this run still going" is a question about the registry and the table, not about a
    directory listing.

    A snapshot whose timestamp cannot be parsed is **protected**, not deleted. An
    unreadable date is a reason to leave something alone.
    """
    now = now or datetime.now(UTC)
    protected_runs = protected_run_ids or set()

    ordered = sorted(snapshots, key=lambda item: getattr(item, "created_at", "") or "", reverse=True)

    newest_by_run: dict[str, str] = {}
    for snapshot in ordered:
        run_id = getattr(snapshot, "run_id", "") or ""
        if run_id and run_id not in newest_by_run:
            newest_by_run[run_id] = snapshot.id

    plan = CleanupPlan(
        kind="snapshot",
        total=len(ordered),
        policy=policy.as_dict(),
        generated_at=now.isoformat(),
        notes=[
            f"数量上限 {policy.snapshot_max_count}，时间上限 {policy.snapshot_max_age_days} 天",
            "同时超出两者才会成为候选" if policy.require_both_limits else "超出任一即可成为候选",
        ],
    )

    for index, snapshot in enumerate(ordered):
        snapshot_id = getattr(snapshot, "id", "")
        created_at = getattr(snapshot, "created_at", "") or ""
        run_id = getattr(snapshot, "run_id", "") or ""
        size = int(getattr(snapshot, "total_bytes", 0) or 0)
        item = CleanupItem(id=snapshot_id, created_at=created_at, reason="", size_bytes=size, run_id=run_id)

        reasons: list[str] = []
        if getattr(snapshot, "pinned", False):
            reasons.append("用户明确保留（pinned）")
        if run_id and run_id in protected_runs:
            reasons.append(f"所属运行仍在进行（{run_id}）")
        if run_id and newest_by_run.get(run_id) == snapshot_id:
            reasons.append("该运行最新的快照，是唯一可回滚的版本")

        age = _age_days(created_at, now)
        within_age = age is not None and age <= policy.snapshot_max_age_days
        within_count = index < policy.snapshot_max_count
        if age is None:
            reasons.append("创建时间无法解析，按保守处理保留")
        elif within_age:
            reasons.append(f"在时间范围内（{age:.1f} 天 ≤ {policy.snapshot_max_age_days} 天）")
        if within_count:
            reasons.append(f"在数量范围内（第 {index + 1} 个 ≤ {policy.snapshot_max_count}）")

        if reasons:
            item.reason = "；".join(reasons)
            plan.protected.append(item)
            continue

        exceeds_count = not within_count
        exceeds_age = age is not None and not within_age
        deletable = (exceeds_count and exceeds_age) if policy.require_both_limits else (exceeds_count or exceeds_age)
        if not deletable:
            item.reason = "只超出其中一项上限，按保守处理保留"
            plan.protected.append(item)
            continue

        item.reason = (
            f"超出数量上限（第 {index + 1} 个 > {policy.snapshot_max_count}）"
            f"且超过 {policy.snapshot_max_age_days} 天（{age:.1f} 天），"
            "不是该运行的最新快照，未被用户保留"
        )
        plan.candidates.append(item)

    return plan


def apply_snapshot_cleanup(
    manager: Any,
    plan: CleanupPlan,
    *,
    archive_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute a snapshot plan: archive first, then delete.

    The archive is written and **read back** before the first deletion. If it does not
    read back, nothing is deleted and the report says so — an archive that silently failed
    is worse than no archive, because it makes the deletion look recoverable.
    """
    archive_dir = archive_dir or (runtime_root() / "retention-archive")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    archive_path = archive_dir / f"snapshots-{stamp}.jsonl"

    report: dict[str, Any] = {
        "kind": "snapshot",
        "planned": len(plan.candidates),
        "deleted": 0,
        "failed": [],
        "archive_path": str(archive_path),
        "before": plan.total,
        "after": plan.total,
        "age_range": _age_range(plan.candidates),
        "reasons": _reason_counts(plan.candidates),
    }

    if not plan.candidates:
        report["notes"] = ["没有候选，未做任何改动"]
        return report

    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        with archive_path.open("w", encoding="utf-8") as handle:
            for item in plan.candidates:
                snapshot = manager.get(item.id)
                if snapshot is None:
                    continue
                handle.write(
                    json.dumps(
                        {"plan_item": item.as_dict(), "manifest": snapshot.as_dict(include_files=True)},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                written += 1
        # Read it back. A file that exists is not a file that can be restored from.
        with archive_path.open("r", encoding="utf-8") as handle:
            recovered = sum(1 for line in handle if line.strip())
    except (OSError, ValueError) as exc:
        # `ValueError` and not just `OSError`: `mkdir` on a path with a NUL byte raises
        # `ValueError`, and an archive failure that escapes as a traceback instead of a
        # report is a deletion whose record nobody sees.
        report["failed"].append(f"归档失败，未删除任何快照：{type(exc).__name__}: {exc}")
        logger.error("snapshot_archive_failed", extra={"error": str(exc)})
        return report

    if recovered != written or recovered != len(plan.candidates):
        report["failed"].append(
            f"归档校验失败：写入 {written} 条、读回 {recovered} 条、计划 {len(plan.candidates)} 条；未删除任何快照"
        )
        logger.error("snapshot_archive_mismatch", extra={"written": written, "recovered": recovered})
        return report

    report["archived"] = recovered

    for item in plan.candidates:
        try:
            if manager.delete(item.id):
                report["deleted"] += 1
            else:
                report["failed"].append(f"{item.id}: 删除返回 False（目录不存在？）")
        except (OSError, ValueError) as exc:
            report["failed"].append(f"{item.id}: {type(exc).__name__}: {exc}")
            logger.error("snapshot_delete_failed", extra={"snapshot_id": item.id, "error": str(exc)})

    report["after"] = plan.total - report["deleted"]
    logger.info(
        "snapshot_cleanup_applied",
        extra={"deleted": report["deleted"], "before": report["before"], "after": report["after"]},
    )
    return report


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
def plan_audit_cleanup(
    rows: list[Any],
    policy: RetentionPolicy,
    *,
    protected_run_ids: set[str] | None = None,
    now: datetime | None = None,
) -> CleanupPlan:
    """Decide which audit rows may be archived and reclaimed. Deletes nothing.

    ``rows`` are objects with ``id``, ``created_at`` and optionally ``ai_run_id``. A row
    belonging to a protected run is kept: the audit trail for a run that is still going,
    or whose result is still in question, is exactly the record that gets asked for later.
    """
    now = now or datetime.now(UTC)
    protected_runs = protected_run_ids or set()

    ordered = sorted(rows, key=lambda row: getattr(row, "created_at", None) or now, reverse=True)

    plan = CleanupPlan(
        kind="audit",
        total=len(ordered),
        policy=policy.as_dict(),
        generated_at=now.isoformat(),
        notes=[
            f"数量上限 {policy.audit_max_count}，时间上限 {policy.audit_max_age_days} 天",
            "归档后可读才删除",
        ],
    )

    for index, row in enumerate(ordered):
        row_id = str(getattr(row, "id", ""))
        created = getattr(row, "created_at", None)
        created_text = created.isoformat() if isinstance(created, datetime) else str(created or "")
        run_id = str(getattr(row, "ai_run_id", "") or "")
        item = CleanupItem(id=row_id, created_at=created_text, reason="", run_id=run_id)

        reasons: list[str] = []
        if run_id and run_id in protected_runs:
            reasons.append("所属运行仍在进行或结果待定")

        age = (now - created).total_seconds() / 86400.0 if isinstance(created, datetime) else None
        within_age = age is not None and age <= policy.audit_max_age_days
        within_count = index < policy.audit_max_count
        if age is None:
            reasons.append("时间无法解析，按保守处理保留")
        elif within_age:
            reasons.append(f"在时间范围内（{age:.1f} 天 ≤ {policy.audit_max_age_days} 天）")
        if within_count:
            reasons.append(f"在数量范围内（第 {index + 1} 条 ≤ {policy.audit_max_count}）")

        if reasons:
            item.reason = "；".join(reasons)
            plan.protected.append(item)
            continue

        exceeds_count = not within_count
        exceeds_age = age is not None and not within_age
        deletable = (exceeds_count and exceeds_age) if policy.require_both_limits else (exceeds_count or exceeds_age)
        if not deletable:
            item.reason = "只超出其中一项上限，按保守处理保留"
            plan.protected.append(item)
            continue

        item.reason = (
            f"超出数量上限（第 {index + 1} 条 > {policy.audit_max_count}）"
            f"且超过 {policy.audit_max_age_days} 天（{age:.1f} 天）"
        )
        plan.candidates.append(item)

    return plan


async def apply_audit_cleanup(
    session: Any,
    plan: CleanupPlan,
    *,
    archive_dir: Path | None = None,
) -> dict[str, Any]:
    """Archive the named audit rows, verify the archive, then delete exactly those rows."""
    from sqlalchemy import delete, select

    from app.models.ai import ToolAuditLog

    archive_dir = archive_dir or (runtime_root() / "retention-archive")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    archive_path = archive_dir / f"audit-{stamp}.jsonl"

    report: dict[str, Any] = {
        "kind": "audit",
        "planned": len(plan.candidates),
        "deleted": 0,
        "failed": [],
        "archive_path": str(archive_path),
        "before": plan.total,
        "after": plan.total,
        "age_range": _age_range(plan.candidates),
        "reasons": _reason_counts(plan.candidates),
    }
    if not plan.candidates:
        report["notes"] = ["没有候选，未做任何改动"]
        return report

    ids = [item.id for item in plan.candidates]
    try:
        rows = list(
            (
                await session.execute(select(ToolAuditLog).where(ToolAuditLog.id.in_(ids)))
            ).scalars()
        )
        archive_dir.mkdir(parents=True, exist_ok=True)
        with archive_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(
                    json.dumps(
                        {
                            "id": str(row.id),
                            "ai_run_id": str(row.ai_run_id) if row.ai_run_id else None,
                            "step_index": row.step_index,
                            "tool_name": row.tool_name,
                            "category": row.category,
                            "decision": row.decision,
                            "reason": row.reason,
                            "risk_level": row.risk_level,
                            "permission_level": row.permission_level,
                            "target": row.target,
                            "status": row.status,
                            "exit_code": row.exit_code,
                            "created_at": row.created_at.isoformat() if row.created_at else None,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        with archive_path.open("r", encoding="utf-8") as handle:
            recovered = sum(1 for line in handle if line.strip())
    except (OSError, ValueError) as exc:
        report["failed"].append(f"归档失败，未删除任何记录：{type(exc).__name__}: {exc}")
        logger.error("audit_archive_failed", extra={"error": str(exc)})
        return report

    if recovered != len(rows) or recovered != len(ids):
        report["failed"].append(
            f"归档校验失败：写入 {recovered} 条、计划 {len(ids)} 条；未删除任何记录"
        )
        logger.error("audit_archive_mismatch", extra={"recovered": recovered, "planned": len(ids)})
        return report

    report["archived"] = recovered

    try:
        result = await session.execute(delete(ToolAuditLog).where(ToolAuditLog.id.in_(ids)))
        await session.commit()
        report["deleted"] = int(result.rowcount or 0)
    except Exception as exc:
        await session.rollback()
        report["failed"].append(f"删除失败：{type(exc).__name__}: {exc}")
        logger.error("audit_delete_failed", extra={"error": str(exc)})
        return report

    report["after"] = plan.total - report["deleted"]
    logger.info(
        "audit_cleanup_applied",
        extra={"deleted": report["deleted"], "before": report["before"], "after": report["after"]},
    )
    return report


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _age_range(items: list[CleanupItem]) -> dict[str, str]:
    stamps = sorted(item.created_at for item in items if item.created_at)
    if not stamps:
        return {"oldest": "", "newest": ""}
    return {"oldest": stamps[0], "newest": stamps[-1]}


def _reason_counts(items: list[CleanupItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.reason] = counts.get(item.reason, 0) + 1
    return counts


def default_archive_dir() -> Path:
    return runtime_root() / "retention-archive"


def policy_from_settings() -> RetentionPolicy:
    """The policy as configured, so the rules live in one place the user can edit."""
    from app.core.config import settings

    return RetentionPolicy(
        enabled=settings.retention_enabled,
        snapshot_max_count=settings.retention_snapshot_max_count,
        snapshot_max_age_days=settings.retention_snapshot_max_age_days,
        audit_max_count=settings.retention_audit_max_count,
        audit_max_age_days=settings.retention_audit_max_age_days,
        require_both_limits=settings.retention_require_both_limits,
    )


__all__ = [
    "CleanupItem",
    "CleanupPlan",
    "RetentionPolicy",
    "apply_audit_cleanup",
    "apply_snapshot_cleanup",
    "default_archive_dir",
    "plan_audit_cleanup",
    "plan_snapshot_cleanup",
    "policy_from_settings",
]

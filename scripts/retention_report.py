"""What would retention reclaim, and why. Prints a plan; deletes nothing unless told to.

This is step one of the retention work, and the only step enabled by default: a report
that names every candidate, every protected item, and the rule that produced each answer.

    .venv/Scripts/python.exe scripts/retention_report.py            # preview, both kinds
    .venv/Scripts/python.exe scripts/retention_report.py --snapshots
    .venv/Scripts/python.exe scripts/retention_report.py --audit

Applying is a separate, explicit act:

    .venv/Scripts/python.exe scripts/retention_report.py --apply --confirm

`--apply` without `--confirm` prints the plan and stops. Even with both, everything is
archived to `.runtime/retention-archive/` and the archive is read back before the first
deletion — see `app.ai.agent.retention`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("TEST_NO_LLM", "1")

#: A run in any of these states may still need its snapshot to undo it, so its snapshots
#: and its audit rows are protected.
_ACTIVE_STATUSES = ("RUNNING",)


def _live_run_ids() -> set[str]:
    """Runs the registry is currently driving."""
    from app.services.autonomous_service import get_run_registry

    return {controller.run_id for controller in get_run_registry().all()}


async def _running_run_ids() -> set[str]:
    """Runs the database still says are running.

    Supplied by the caller rather than inferred: "is this run still going" is a question
    about the registry and the table together, and a snapshot directory cannot answer it.
    """
    from sqlalchemy import select

    from app.domain.enums import RunStatus, RunType
    from app.infrastructure.database import dispose_engine, session_scope
    from app.models.ai import AIRun

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(AIRun.run_id).where(
                    AIRun.run_type == RunType.AGENT.value,
                    AIRun.status.in_(_ACTIVE_STATUSES),
                )
            )
        ).scalars()
        ids = {str(value) for value in rows}
    await dispose_engine()
    return ids


async def _audit_rows() -> list:
    from sqlalchemy import select

    from app.infrastructure.database import dispose_engine, session_scope
    from app.models.ai import ToolAuditLog

    async with session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(
                        ToolAuditLog.id, ToolAuditLog.created_at, ToolAuditLog.ai_run_id
                    ).order_by(ToolAuditLog.created_at.desc())
                )
            ).all()
        )
    await dispose_engine()
    return rows


def _print_plan(plan, *, limit: int = 10) -> None:
    payload = plan.as_dict()
    print(f"\n--- {plan.kind} ---")
    print(f"  总数            : {payload['total']}")
    print(f"  候选            : {payload['candidate_count']}")
    print(f"  受保护          : {payload['protected_count']}")
    if plan.kind == "snapshot":
        print(f"  可回收          : {payload['reclaimable_bytes'] / 1024 / 1024:.2f} MB")
    for note in payload["notes"]:
        print(f"  规则            : {note}")

    if plan.candidates:
        print("  候选明细（前 %d 条）:" % limit)
        for item in plan.candidates[:limit]:
            print(f"    {item.id}  {item.created_at}  {item.reason}")
        if len(plan.candidates) > limit:
            print(f"    … 另有 {len(plan.candidates) - limit} 条")

    protected_by_rule: dict[str, int] = {}
    for item in plan.protected:
        key = item.reason.split("（")[0]
        protected_by_rule[key] = protected_by_rule.get(key, 0) + 1
    if protected_by_rule:
        print("  受保护的原因分布:")
        for reason, count in sorted(protected_by_rule.items(), key=lambda pair: -pair[1]):
            print(f"    {count:>5}  {reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot and audit retention preview")
    parser.add_argument("--snapshots", action="store_true", help="only snapshots")
    parser.add_argument("--audit", action="store_true", help="only audit rows")
    parser.add_argument("--apply", action="store_true", help="apply the plan (needs --confirm)")
    parser.add_argument("--confirm", action="store_true", help="confirm applying the plan")
    parser.add_argument("--json", action="store_true", help="print the plan as JSON")
    parser.add_argument(
        "--verify-archive",
        metavar="DIR",
        help="re-hash an archive directory and report mismatches",
    )
    parser.add_argument(
        "--restore-archive",
        metavar="DIR",
        help="restore snapshots from an archive directory (refuses a tampered archive)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="with --restore-archive, replace a snapshot that is still present",
    )
    args = parser.parse_args()

    # Recovery is the half of retention nobody exercises until they need it, so it gets a
    # command of its own rather than being reachable only from Python.
    if args.verify_archive:
        from app.ai.agent.retention import verify_snapshot_archive

        result = verify_snapshot_archive(Path(args.verify_archive))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    if args.restore_archive:
        from app.ai.agent.retention import restore_snapshot_archive

        result = restore_snapshot_archive(
            Path(args.restore_archive), overwrite=args.overwrite
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not result["failed"] else 1

    both = not (args.snapshots or args.audit)
    policy = __import__("app.ai.agent.retention", fromlist=["policy_from_settings"]).policy_from_settings()

    print("=== retention policy ===")
    print(json.dumps(policy.as_dict(), ensure_ascii=False, indent=2))

    protected = _live_run_ids()
    protected |= asyncio.run(_running_run_ids())
    print(f"受保护的运行（进行中）: {len(protected)}")

    from app.ai.agent.retention import (
        apply_audit_cleanup,
        apply_snapshot_cleanup,
        plan_audit_cleanup,
        plan_snapshot_cleanup,
    )
    from app.ai.agent.snapshot import get_snapshot_manager

    plans = []

    if both or args.snapshots:
        snapshots = get_snapshot_manager().list()
        plan = plan_snapshot_cleanup(snapshots, policy, protected_run_ids=protected)
        plans.append(("snapshot", plan))
        if args.json:
            print(json.dumps(plan.as_dict(), ensure_ascii=False, indent=2))
        else:
            _print_plan(plan)

    if both or args.audit:
        rows = asyncio.run(_audit_rows())
        plan = plan_audit_cleanup(rows, policy, protected_run_ids=protected)
        plans.append(("audit", plan))
        if args.json:
            print(json.dumps(plan.as_dict(), ensure_ascii=False, indent=2))
        else:
            _print_plan(plan)

    if not args.apply:
        print("\n只生成预览，未做任何改动。要执行请加 --apply --confirm。")
        return 0

    if not args.confirm:
        print("\n--apply 需要同时给出 --confirm；未做任何改动。")
        return 1

    if not policy.enabled:
        print(
            "\n策略未启用（retention_enabled=false）。预览是默认行为；"
            "确认规则后把 RETENTION_ENABLED 设为 true 再执行。"
        )
        return 1

    print("\n=== applying ===")
    manager = get_snapshot_manager()
    for kind, plan in plans:
        if kind == "snapshot":
            report = apply_snapshot_cleanup(manager, plan)
        else:
            from app.infrastructure.database import session_scope

            async def _run(plan=plan):
                async with session_scope() as session:
                    return await apply_audit_cleanup(session, plan)

            report = asyncio.run(_run())
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if report["failed"]:
            print(f"!! {kind} 有失败项，已在上面的 failed 中列出")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

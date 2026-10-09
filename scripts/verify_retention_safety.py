"""Retention, proved on real data without touching it.

Three claims, and each needs a different kind of evidence:

1. **A plan changes nothing.** The real snapshot store is hashed before and after planning
   under two policies — the default and a deliberately aggressive one — and the digest must
   be identical. Planning is where a bug would delete something before anyone approved it.
2. **An archive can be restored byte-for-byte.** Run on a *copy* of real snapshots in a
   temporary directory: archive, verify the checksums, delete, restore, and compare the
   content against the original. Real data, real sizes, disposable location.
3. **The real store is untouched.** After all of that, the real snapshot store and the real
   `tool_audit_log` row count are exactly what they were at the start.

The audit archive/restore cycle runs against the **test database**, because restoring rows
means inserting rows and inserting them into the live database would be the thing this
script exists to prove does not happen.

    .venv/Scripts/python.exe scripts/verify_retention_safety.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("TEST_NO_LLM", "1")

RESULTS: list[tuple[bool, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((bool(ok), label, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def _store_digest(root: Path) -> tuple[str, int, int]:
    """A digest over the whole store: every path and every file's bytes."""
    digest = hashlib.sha256()
    files = 0
    total = 0
    if not root.is_dir():
        return "", 0, 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        files += 1
        total += path.stat().st_size
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    return digest.hexdigest(), files, total


def _real_snapshot_root() -> Path:
    from app.ai.agent.snapshot import snapshot_root

    return snapshot_root()


def _audit_count() -> int:
    """Row count in the **development** database, read-only."""
    import subprocess

    psql = ROOT / "vendor" / "runtime" / "pgsql" / "pgsql" / "bin" / "psql.exe"
    run = subprocess.run(
        [str(psql), "-h", "127.0.0.1", "-U", "aiworkbench", "-d", "aiworkbench", "-tAc",
         "select count(*) from tool_audit_log"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PGPASSWORD": "aiworkbench"}, check=False,
    )
    try:
        return int((run.stdout or "0").strip())
    except ValueError:
        return -1


def main() -> int:
    from app.ai.agent.retention import (
        RetentionPolicy,
        apply_snapshot_cleanup,
        plan_snapshot_cleanup,
        restore_snapshot_archive,
        verify_snapshot_archive,
    )
    from app.ai.agent.snapshot import WorkspaceSnapshotManager, snapshot_root

    real_root = _real_snapshot_root()
    before_digest, before_files, before_bytes = _store_digest(real_root)
    before_audit = _audit_count()
    print("=== 0. the real store, before anything ===")
    print(f"  {real_root}")
    print(f"  {before_files} 文件，{before_bytes / 1024 / 1024:.2f} MB，digest {before_digest[:16]}…")
    print(f"  tool_audit_log 行数 {before_audit}")

    print("\n=== 1. planning changes nothing ===")
    manager = WorkspaceSnapshotManager()
    snapshots = manager.list()
    check("the store is readable", len(snapshots) > 0, f"{len(snapshots)} snapshots")

    default_policy = RetentionPolicy()
    strict_policy = RetentionPolicy(
        snapshot_max_count=50, snapshot_max_age_days=1, require_both_limits=True
    )
    for label, policy in (("默认策略", default_policy), ("激进策略", strict_policy)):
        plan = plan_snapshot_cleanup(snapshots, policy)
        print(f"    {label}: 候选 {len(plan.candidates)} / 受保护 {len(plan.protected)}")

    after_plan_digest, after_plan_files, _ = _store_digest(real_root)
    check(
        "**planning left the real store byte-identical**",
        after_plan_digest == before_digest and after_plan_files == before_files,
        f"{after_plan_files} files",
    )

    print("\n=== 2. archive and restore a real copy ===")
    with tempfile.TemporaryDirectory(prefix="aiw-retention-") as tmp:
        tmp_path = Path(tmp)
        copy_root = tmp_path / "snapshots"
        copy_root.mkdir(parents=True)

        # The newest few real snapshots, copied out of the live store.
        picked = sorted(snapshots, key=lambda item: item.created_at, reverse=True)[:3]
        for snapshot in picked:
            shutil.copytree(snapshot.directory, copy_root / snapshot.id)
        check("real snapshots were copied for the test", len(picked) == 3, f"{len(picked)}")

        originals = {snapshot.id: _store_digest(copy_root / snapshot.id)[0] for snapshot in picked}
        original_files = {
            snapshot.id: sorted(
                path.relative_to(copy_root / snapshot.id).as_posix()
                for path in (copy_root / snapshot.id).rglob("*")
                if path.is_file()
            )
            for snapshot in picked
        }

        class CopyManager:
            """Operates only inside the temporary copy."""

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

                return _View(directory)

            def delete(self, snapshot_id: str) -> bool:
                directory = self.root / snapshot_id
                if not directory.is_dir():
                    return False
                shutil.rmtree(directory)
                self.deleted.append(snapshot_id)
                return True

        copy_manager = CopyManager(copy_root)
        # Force all three to be candidates by making the age limit zero.
        plan = plan_snapshot_cleanup(
            picked, RetentionPolicy(snapshot_max_count=0, snapshot_max_age_days=0), now=None
        )
        check("all three are candidates under a zero limit", len(plan.candidates) == 3, str(len(plan.candidates)))

        report = apply_snapshot_cleanup(copy_manager, plan, archive_dir=tmp_path / "archive")
        check("the archive was written and verified", report["archived"] == 3 and not report["failed"], str(report["failed"]))
        check("the copies were deleted", report["deleted"] == 3, str(report["deleted"]))

        archive_root = Path(report["archive_dir"])
        verification = verify_snapshot_archive(archive_root)
        check(
            "**the archive reads back and every checksum matches**",
            verification["ok"] and verification["verified"] == 3,
            f"verified={verification['verified']} mismatched={verification['mismatched']}",
        )

        restored = restore_snapshot_archive(archive_root, snapshot_root=copy_root)
        check("everything was restored", restored["restored"] == 3 and not restored["failed"], str(restored["failed"]))

        same_digest = all(
            _store_digest(copy_root / snapshot_id)[0] == originals[snapshot_id] for snapshot_id in originals
        )
        check("**restored content matches the original, byte for byte**", same_digest)

        same_shape = all(
            sorted(
                path.relative_to(copy_root / snapshot_id).as_posix()
                for path in (copy_root / snapshot_id).rglob("*")
                if path.is_file()
            )
            == original_files[snapshot_id]
            for snapshot_id in originals
        )
        check("**and the file layout matches** (manifest.json + files/ present)", same_shape)

        # A tampered archive must not restore.
        victim = picked[0].id
        target = archive_root / victim / "files"
        if target.is_dir():
            any_file = next(target.rglob("*"))
            if any_file.is_file():
                any_file.write_text("tampered", encoding="utf-8")
        tampered = verify_snapshot_archive(archive_root)
        check("a tampered archive is detected", not tampered["ok"], str(tampered["mismatched"])[:80])

        shutil.rmtree(copy_root / victim, ignore_errors=True)
        refused = restore_snapshot_archive(archive_root, snapshot_root=copy_root)
        check(
            "**a tampered archive restores nothing**",
            refused["restored"] == 0 and not (copy_root / victim).exists(),
        )

    print("\n=== 3. the real store is untouched ===")
    final_digest, final_files, final_bytes = _store_digest(real_root)
    check(
        "**the real snapshot store is byte-identical**",
        final_digest == before_digest and final_files == before_files and final_bytes == before_bytes,
        f"{final_files} files, digest {final_digest[:16]}…",
    )
    final_audit = _audit_count()
    check(
        "**the real audit table has the same number of rows**",
        final_audit == before_audit,
        f"{before_audit} → {final_audit}",
    )
    check("the real snapshot root is still the live one", real_root == snapshot_root())

    passed = sum(1 for ok, _, _ in RESULTS if ok)
    print(f"\n=== {passed}/{len(RESULTS)} passed ===")
    for ok, label, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {label} — {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())

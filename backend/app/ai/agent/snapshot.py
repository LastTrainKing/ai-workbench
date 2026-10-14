"""Workspace snapshots, diffs and rollback.

Before an agent touches a workspace, this takes a copy. Afterwards it can answer
three questions the user actually asks: *what changed*, *can I see it*, and *put
it back*.

The hard part is not copying files, it is rollback safety. The sequence that
breaks naive implementations:

    1. snapshot A
    2. agent edits   -> B
    3. user edits    -> C
    4. rollback      -> would silently destroy the user's edit at step 3

So a snapshot records two manifests: the state at capture, and the state the run
left behind. Rollback compares the workspace against *the second* one. If a file
no longer matches what the run produced, someone else has touched it since, and
the restore stops and reports a conflict instead of overwriting. Forcing is
possible, but it has to be asked for.

Snapshots live in ``.runtime/snapshots/<id>/`` — git-ignored, never in the
delivery archive, and deleted with the rest of the runtime state.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import runtime_root
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Directories never snapshotted: build output and dependency trees are large,
#: reproducible, and not something a rollback should ever restore.
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
    ".ruff_cache", ".mypy_cache", "target", "dist", "build", ".next", ".turbo",
    ".runtime", "vendor", "coverage", ".cache", "out", "bin", "obj",
}

#: Ceilings so a snapshot of a huge tree cannot fill the disk.
MAX_FILES = 5000
MAX_FILE_BYTES = 5_000_000
MAX_TOTAL_BYTES = 300_000_000

TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".md", ".txt",
    ".toml", ".ini", ".cfg", ".yaml", ".yml", ".xml", ".html", ".css", ".scss",
    ".sql", ".sh", ".bat", ".cmd", ".ps1", ".java", ".kt", ".go", ".rs", ".c",
    ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".vue", ".svelte", ".env",
    ".example", ".gitignore", ".lock", ".properties", ".gradle",
}


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _snapshot_root() -> Path:

    return runtime_root() / "snapshots"


def snapshot_root() -> Path:
    """Where snapshots live. Public so retention can restore into the same place.

    A restore that guessed its own path would put snapshots somewhere the manager does not
    look, and the result would be "the archive restored successfully" followed by a list
    that does not contain them.
    """
    return _snapshot_root()


@dataclass
class FileEntry:
    path: str  # relative, POSIX separators
    sha256: str
    size: int
    stored: bool = False  # whether the content was copied (false for oversize files)

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size, "stored": self.stored}


@dataclass
class WorkspaceSnapshot:
    id: str
    root: str
    label: str = ""
    run_id: str = ""
    created_at: str = ""
    files: dict[str, FileEntry] = field(default_factory=dict)
    #: The state the run left behind, used for conflict detection on rollback.
    post_files: dict[str, FileEntry] = field(default_factory=dict)
    total_bytes: int = 0
    truncated: bool = False
    #: Set when the user has said this snapshot matters and must not be reclaimed.
    #: Retention treats it as an absolute: no age or count overrides it.
    pinned: bool = False

    @property
    def directory(self) -> Path:
        return _snapshot_root() / self.id

    def as_dict(self, *, include_files: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "root": self.root,
            "label": self.label,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "file_count": len(self.files),
            "total_bytes": self.total_bytes,
            "truncated": self.truncated,
            "pinned": self.pinned,
            "has_post_state": bool(self.post_files),
        }
        if include_files:
            payload["files"] = [f.as_dict() for f in self.files.values()]
        return payload


@dataclass
class DiffEntry:
    path: str
    change: str  # added | removed | modified
    lines_added: int = 0
    lines_removed: int = 0
    before_size: int = 0
    after_size: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "change": self.change,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "before_size": self.before_size,
            "after_size": self.after_size,
        }


@dataclass
class Conflict:
    path: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "reason": self.reason}


class WorkspaceSnapshotManager:
    """Capture, compare and restore a directory tree."""

    def __init__(self) -> None:
        #: Set while diffing, so `_line_delta` can find the stored "before" copies
        #: without threading the snapshot through every helper.
        self._current_snapshot_id: str = ""

    # ------------------------------------------------------------------ capture --
    def _walk(self, root: Path) -> tuple[dict[str, FileEntry], int, bool]:
        entries: dict[str, FileEntry] = {}
        total = 0
        truncated = False

        for path in sorted(root.rglob("*")):
            if len(entries) >= MAX_FILES:
                truncated = True
                break
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            relative = path.relative_to(root).as_posix()
            try:
                digest = _digest_file(path)
            except OSError:
                continue
            stored = size <= MAX_FILE_BYTES and (total + size) <= MAX_TOTAL_BYTES
            if stored:
                total += size
            else:
                truncated = True
            entries[relative] = FileEntry(path=relative, sha256=digest, size=size, stored=stored)

        return entries, total, truncated

    def create(self, root: str | Path, *, label: str = "", run_id: str = "") -> WorkspaceSnapshot:
        """Capture the current state of a directory."""
        base = Path(root).resolve()
        if not base.is_dir():
            raise ValueError(f"Not a directory: {base}")

        snapshot_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        entries, total, truncated = self._walk(base)

        snapshot = WorkspaceSnapshot(
            id=snapshot_id,
            root=str(base),
            label=label,
            run_id=run_id,
            created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            files=entries,
            total_bytes=total,
            truncated=truncated,
        )

        directory = snapshot.directory
        (directory / "files").mkdir(parents=True, exist_ok=True)
        for entry in entries.values():
            if not entry.stored:
                continue
            source = base / entry.path
            target = directory / "files" / entry.path
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, target)
            except OSError as exc:
                logger.warning("snapshot_copy_failed", extra={"path": entry.path, "error": str(exc)})
                entry.stored = False

        self._write(snapshot)
        logger.info(
            "snapshot_created",
            extra={"snapshot": snapshot_id, "files": len(entries), "root": str(base)},
        )
        return snapshot

    def mark_post_state(self, snapshot: WorkspaceSnapshot) -> WorkspaceSnapshot:
        """Record what the run left behind, enabling conflict-safe rollback."""
        base = Path(snapshot.root)
        entries, _, _ = self._walk(base) if base.is_dir() else ({}, 0, False)
        snapshot.post_files = entries
        self._write(snapshot)
        return snapshot

    # ------------------------------------------------------------------ storage --
    def _write(self, snapshot: WorkspaceSnapshot) -> None:
        payload = {
            "id": snapshot.id,
            "root": snapshot.root,
            "label": snapshot.label,
            "run_id": snapshot.run_id,
            "created_at": snapshot.created_at,
            "total_bytes": snapshot.total_bytes,
            "truncated": snapshot.truncated,
            "pinned": snapshot.pinned,
            "files": [f.as_dict() for f in snapshot.files.values()],
            "post_files": [f.as_dict() for f in snapshot.post_files.values()],
        }
        snapshot.directory.mkdir(parents=True, exist_ok=True)
        (snapshot.directory / "manifest.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def list(self) -> list[WorkspaceSnapshot]:
        root = _snapshot_root()
        if not root.is_dir():
            return []
        snapshots: list[WorkspaceSnapshot] = []
        for directory in sorted(root.iterdir(), reverse=True):
            if not directory.is_dir():
                continue
            manifest = directory / "manifest.json"
            if not manifest.is_file():
                continue
            try:
                snapshots.append(self._read(manifest))
            except (OSError, json.JSONDecodeError, KeyError):
                continue
        return snapshots

    def get(self, snapshot_id: str) -> WorkspaceSnapshot | None:
        manifest = _snapshot_root() / snapshot_id / "manifest.json"
        if not manifest.is_file():
            return None
        try:
            return self._read(manifest)
        except (OSError, json.JSONDecodeError, KeyError):
            return None

    @staticmethod
    def _read(manifest: Path) -> WorkspaceSnapshot:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        snapshot = WorkspaceSnapshot(
            id=payload["id"],
            root=payload["root"],
            label=payload.get("label", ""),
            run_id=payload.get("run_id", ""),
            created_at=payload.get("created_at", ""),
            total_bytes=payload.get("total_bytes", 0),
            truncated=payload.get("truncated", False),
            pinned=payload.get("pinned", False),
        )
        for item in payload.get("files", []):
            snapshot.files[item["path"]] = FileEntry(**item)
        for item in payload.get("post_files", []):
            snapshot.post_files[item["path"]] = FileEntry(**item)
        return snapshot

    def set_pinned(self, snapshot_id: str, pinned: bool = True) -> WorkspaceSnapshot | None:
        """Mark a snapshot as one the user wants kept.

        Retention reads this and stops considering the snapshot, whatever its age or how
        far down the list it is. Without a way to say "this one matters" the only choices
        are "keep everything" and "lose the important one eventually".
        """
        snapshot = self.get(snapshot_id)
        if snapshot is None:
            return None
        snapshot.pinned = pinned
        self._write(snapshot)
        return snapshot

    def delete(self, snapshot_id: str) -> bool:
        directory = _snapshot_root() / snapshot_id
        if not directory.is_dir():
            return False
        shutil.rmtree(directory, ignore_errors=True)
        return True

    # -------------------------------------------------------------------- diff --
    def diff(self, snapshot: WorkspaceSnapshot, *, unified: bool = False) -> dict[str, Any]:
        """Compare a snapshot against the workspace as it is now."""
        base = Path(snapshot.root)
        if not base.is_dir():
            raise ValueError(f"The snapshot's directory no longer exists: {base}")

        self._current_snapshot_id = snapshot.id
        current, _, _ = self._walk(base)
        before = snapshot.files

        entries: list[DiffEntry] = []
        for path, entry in sorted(before.items()):
            now = current.get(path)
            if now is None:
                entries.append(DiffEntry(path=path, change="removed", before_size=entry.size))
            elif now.sha256 != entry.sha256:
                added, removed = self._line_delta(base, path, entry)
                entries.append(
                    DiffEntry(
                        path=path,
                        change="modified",
                        lines_added=added,
                        lines_removed=removed,
                        before_size=entry.size,
                        after_size=now.size,
                    )
                )
        for path in sorted(current):
            if path not in before:
                # `current[path]`, not the `entry` left over from the loop above. Using
                # the stale variable reported a newly added file with the size of
                # whichever file happened to be visited last, which is a wrong number
                # rather than a missing one — the harder kind to notice.
                entries.append(
                    DiffEntry(path=path, change="added", after_size=current[path].size)
                )

        summary = {
            "added": sum(1 for e in entries if e.change == "added"),
            "removed": sum(1 for e in entries if e.change == "removed"),
            "modified": sum(1 for e in entries if e.change == "modified"),
            "lines_added": sum(e.lines_added for e in entries),
            "lines_removed": sum(e.lines_removed for e in entries),
        }

        payload: dict[str, Any] = {
            "snapshot_id": snapshot.id,
            "root": snapshot.root,
            "files": [e.as_dict() for e in entries],
            "summary": summary,
            "changed": len(entries),
        }
        if unified:
            payload["unified"] = self._unified(base, snapshot, entries)
        return payload

    def _line_delta(self, base: Path, path: str, before: FileEntry) -> tuple[int, int]:
        """Line counts for a modified text file.

        Uses the stored copy as the "before" side. Returns (0, 0) for binaries
        and for files whose content was too large to store — reporting a made-up
        line count would be worse than reporting none.
        """
        suffix = Path(path).suffix.lower()
        if suffix and suffix not in TEXT_SUFFIXES and Path(path).name not in TEXT_SUFFIXES:
            return (0, 0)
        if not before.stored:
            return (0, 0)

        stored = _snapshot_root() / self._current_snapshot_id / "files" / path
        current = base / path
        if not stored.is_file() or not current.is_file():
            return (0, 0)

        try:
            old = stored.read_text(encoding="utf-8", errors="replace").splitlines()
            new = current.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return (0, 0)

        added = removed = 0
        for line in difflib.unified_diff(old, new, n=0):
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        return added, removed

    def _unified(self, base: Path, snapshot: WorkspaceSnapshot, entries: list[DiffEntry]) -> str:
        """A unified diff of the changed files.

        Only text files are diffed; a binary change is reported by name and size,
        because a diff of a PNG is noise.
        """
        chunks: list[str] = []
        for entry in entries[:50]:
            path = entry.path
            suffix = Path(path).suffix.lower()
            if suffix and suffix not in TEXT_SUFFIXES and Path(path).name not in TEXT_SUFFIXES:
                chunks.append(f"--- {path}\n+++ {path}\n[binary file: {entry.change}]\n")
                continue

            before_lines: list[str] = []
            stored = snapshot.directory / "files" / path
            if entry.change != "added" and stored.is_file():
                before_lines = stored.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)

            after_lines: list[str] = []
            current = base / path
            if entry.change != "removed" and current.is_file():
                after_lines = current.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)

            diff = difflib.unified_diff(
                before_lines, after_lines, fromfile=f"a/{path}", tofile=f"b/{path}", n=3
            )
            chunks.append("".join(diff))
        return "\n".join(chunks)[:200_000]

    # ---------------------------------------------------------------- rollback --
    def check_conflicts(self, snapshot: WorkspaceSnapshot) -> list[Conflict]:
        """Files that changed after the run, i.e. that a rollback would destroy.

        Compared against the *post-run* manifest. Without one, every file that
        differs from the pre-run state counts as potentially the user's — the safe
        reading, since we cannot tell their edit from ours.
        """
        base = Path(snapshot.root)
        if not base.is_dir():
            return [Conflict(path="", reason=f"The workspace is gone: {base}")]

        current, _, _ = self._walk(base)
        reference = snapshot.post_files or {}

        conflicts: list[Conflict] = []
        for path, entry in current.items():
            expected = reference.get(path)
            if expected is None:
                if path in snapshot.files and snapshot.files[path].sha256 != entry.sha256:
                    conflicts.append(
                        Conflict(path=path, reason="This file changed after the snapshot and the run")
                    )
                elif path not in snapshot.files:
                    conflicts.append(
                        Conflict(path=path, reason="This file was created after the snapshot")
                    )
                continue
            if entry.sha256 != expected.sha256:
                conflicts.append(
                    Conflict(path=path, reason="This file was modified after the agent finished")
                )

        return conflicts

    def restore(
        self, snapshot: WorkspaceSnapshot, *, force: bool = False
    ) -> dict[str, Any]:
        """Put the workspace back to the snapshot state."""
        base = Path(snapshot.root)
        if not base.is_dir():
            raise ValueError(f"The snapshot's directory no longer exists: {base}")

        conflicts = self.check_conflicts(snapshot)
        if conflicts and not force:
            return {
                "restored": False,
                "conflict": True,
                "conflicts": [c.as_dict() for c in conflicts],
                "message": (
                    f"{len(conflicts)} file(s) changed after the snapshot. "
                    "Restoring would discard those changes. Review them, then restore "
                    "with force if that is really what you want."
                ),
            }

        restored: list[str] = []
        removed: list[str] = []

        current, _, _ = self._walk(base)
        # Delete files that did not exist at capture time.
        for path in current:
            if path not in snapshot.files:
                try:
                    (base / path).unlink()
                    removed.append(path)
                except OSError as exc:
                    logger.warning("rollback_delete_failed", extra={"path": path, "error": str(exc)})

        # Restore or rewrite the rest.
        for path in snapshot.files:
            target = base / path
            stored = snapshot.directory / "files" / path
            if not stored.is_file():
                continue  # oversize file: not stored, so cannot be restored
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(stored, target)
                restored.append(path)
            except OSError as exc:
                logger.warning("rollback_restore_failed", extra={"path": path, "error": str(exc)})

        return {
            "restored": True,
            "conflict": False,
            "files_restored": len(restored),
            "files_removed": len(removed),
            "forced": bool(conflicts and force),
            "conflicts_overridden": [c.path for c in conflicts] if force else [],
            "message": (
                f"Restored {len(restored)} file(s), removed {len(removed)} that did not exist at capture."
            ),
        }


_manager: WorkspaceSnapshotManager | None = None


def get_snapshot_manager() -> WorkspaceSnapshotManager:
    global _manager
    if _manager is None:
        _manager = WorkspaceSnapshotManager()
    return _manager


__all__ = [
    "Conflict",
    "DiffEntry",
    "FileEntry",
    "WorkspaceSnapshot",
    "WorkspaceSnapshotManager",
    "get_snapshot_manager",
]

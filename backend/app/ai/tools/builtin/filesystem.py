"""Filesystem tools.

Every path in this module goes through two checks before it is touched:

1. ``ctx.resolve()`` turns a relative path into an absolute one against the run's
   working directory, so a tool can never be steered by whatever directory the
   backend process happens to be in.
2. ``ctx.require_permission(path, write=...)`` asks the policy. The policy denies
   protected locations first (credentials, system directories), then requires the
   path to be inside a granted root.

Doing this per path — rather than once per tool — matters for the tools that take
two paths: ``file_copy`` and ``file_move`` must be checked at both ends, or the
agent could read a secret and write it somewhere readable.

Reads and writes are bounded: results are capped by the executor, and the file
tools refuse to load an unbounded amount into memory in the first place.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Any

from app.ai.agent.tools import Tool, ToolContext
from app.ai.tools.builtin.documents import DocumentReadTool, DocumentWriteTool
from app.ai.types import ToolResult, ToolSpec
from app.core.errors import ValidationError

#: Refuse to read more than this in one call. Large files are a real thing, but
#: pulling one into the model's context is never the right answer.
MAX_READ_BYTES = 512_000
#: A directory listing larger than this is useless to a model anyway.
MAX_LIST_ENTRIES = 1_000
#: A recursive search stops here.
MAX_SEARCH_RESULTS = 200
#: How deep a recursive walk may go before it is considered a mistake.
MAX_WALK_DEPTH = 12

TEXT_SUFFIXES = {
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".jsonc",
    ".md", ".txt", ".toml", ".ini", ".cfg", ".yaml", ".yml", ".xml", ".html",
    ".htm", ".css", ".scss", ".sql", ".sh", ".bash", ".bat", ".cmd", ".ps1",
    ".psm1", ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cs",
    ".rb", ".php", ".swift", ".vue", ".svelte", ".dockerfile", ".gitignore",
    ".env", ".example", ".conf", ".properties", ".gradle", ".lua", ".r", ".m",
    ".csv", ".tsv", ".log", ".lock",
}


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _looks_textual(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES or path.name.lower() in TEXT_SUFFIXES:
        return True
    # No extension: sniff the first block for NUL bytes rather than guessing.
    try:
        with path.open("rb") as handle:
            sample = handle.read(2048)
    except OSError:
        return False
    return b"\x00" not in sample


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# file_read
# --------------------------------------------------------------------------- #
class FileReadTool(Tool):
    spec = ToolSpec(
        name="file_read",
        description=(
            "Read a text file. Returns its contents, size, line count and a "
            "SHA-256 hash. Use line_start/line_end to read a slice of a large file."
        ),
        category="filesystem",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute, or relative to the working directory"},
                "line_start": {"type": "integer", "description": "1-based first line (optional)"},
                "line_end": {"type": "integer", "description": "1-based last line, inclusive (optional)"},
                "max_bytes": {"type": "integer", "description": f"Ceiling, default {MAX_READ_BYTES}"},
            },
            "required": ["path"],
        },
        permission_level=0,
        risk_level="low",
        timeout_seconds=15,
        mutates_state=False,
        output_schema={"type": "object", "properties": {"content": {"type": "string"}}},
    )

    async def execute(
        self,
        ctx: ToolContext,
        path: str,
        line_start: int | None = None,
        line_end: int | None = None,
        max_bytes: int = MAX_READ_BYTES,
    ) -> ToolResult:
        target = Path(ctx.resolve(path))
        ctx.require_permission(target, write=False)

        if not target.exists():
            return ToolResult(ok=False, error=f"No such file: {target}", summary="not found")
        if target.is_dir():
            return ToolResult(
                ok=False,
                error=f"{target} is a directory - use directory_list",
                summary="is a directory",
            )

        size = target.stat().st_size
        ceiling = max(1024, min(int(max_bytes or MAX_READ_BYTES), MAX_READ_BYTES))
        if not _looks_textual(target):
            return ToolResult(
                ok=False,
                error=f"{target.name} looks like a binary file ({_human(size)})",
                summary="binary file",
            )

        raw = target.read_bytes()[:ceiling]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")

        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        total_lines = text.count("\n") + 1

        sliced = False
        if line_start or line_end:
            lines = text.splitlines()
            start = max(1, int(line_start or 1))
            end = min(len(lines), int(line_end or len(lines)))
            text = "\n".join(lines[start - 1 : end])
            sliced = True

        return ToolResult(
            ok=True,
            data={
                "path": str(target),
                "content": text,
                "size_bytes": size,
                "size_human": _human(size),
                "line_count": total_lines,
                "sha256": digest,
                "truncated": size > ceiling,
                "sliced": sliced,
            },
            summary=f"read {target.name} ({_human(size)})",
        )


# --------------------------------------------------------------------------- #
# file_write / file_append
# --------------------------------------------------------------------------- #
class FileWriteTool(Tool):
    spec = ToolSpec(
        name="file_write",
        description=(
            "Write a text file, creating parent directories as needed. Overwrites "
            "an existing file. Returns the previous and new SHA-256 so the change "
            "can be diffed and rolled back."
        ),
        category="filesystem",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "create_dirs": {"type": "boolean", "description": "Create missing parents (default true)"},
            },
            "required": ["path", "content"],
        },
        permission_level=1,
        risk_level="low",
        timeout_seconds=20,
        mutates_state=True,
    )

    async def execute(
        self, ctx: ToolContext, path: str, content: str, create_dirs: bool = True
    ) -> ToolResult:
        target = Path(ctx.resolve(path))
        ctx.require_permission(target, write=True)

        if target.exists() and target.is_dir():
            return ToolResult(ok=False, error=f"{target} is a directory", summary="is a directory")

        if create_dirs and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)

        previous_hash = ""
        if target.exists():
            previous_hash = hashlib.sha256(target.read_bytes()).hexdigest()

        data = content.encode("utf-8")
        target.write_bytes(data)
        ctx.consume_write()
        ctx.register_artifact("files_written", str(target))

        return ToolResult(
            ok=True,
            data={
                "path": str(target),
                "bytes_written": len(data),
                "original_hash": previous_hash,
                "modified_hash": hashlib.sha256(data).hexdigest(),
                "created": not previous_hash,
            },
            summary=("created " if not previous_hash else "updated ") + target.name,
        )


class FileAppendTool(Tool):
    spec = ToolSpec(
        name="file_append",
        description="Append text to a file, creating it if absent.",
        category="filesystem",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        permission_level=1,
        risk_level="low",
        timeout_seconds=20,
        mutates_state=True,
    )

    async def execute(self, ctx: ToolContext, path: str, content: str) -> ToolResult:
        target = Path(ctx.resolve(path))
        ctx.require_permission(target, write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        before = target.stat().st_size if target.exists() else 0
        with target.open("a", encoding="utf-8") as handle:
            handle.write(content)
        ctx.consume_write()
        ctx.register_artifact("files_written", str(target))
        return ToolResult(
            ok=True,
            data={"path": str(target), "appended_bytes": len(content.encode("utf-8")), "size_before": before},
            summary=f"appended to {target.name}",
        )


# --------------------------------------------------------------------------- #
# file_delete / file_move / file_copy
# --------------------------------------------------------------------------- #
class FileDeleteTool(Tool):
    spec = ToolSpec(
        name="file_delete",
        description="Delete a single file. Deleting a directory requires directory_delete.",
        category="filesystem",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        permission_level=1,
        risk_level="medium",
        timeout_seconds=20,
        mutates_state=True,
    )

    async def execute(self, ctx: ToolContext, path: str) -> ToolResult:
        target = Path(ctx.resolve(path))
        ctx.require_permission(target, write=True)
        if not target.exists():
            return ToolResult(ok=False, error=f"No such file: {target}", summary="not found")
        if target.is_dir():
            return ToolResult(
                ok=False,
                error="Refusing to delete a directory with file_delete - use directory_delete",
                summary="is a directory",
            )
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        target.unlink()
        ctx.consume_write()
        ctx.register_artifact("files_deleted", str(target))
        return ToolResult(
            ok=True,
            data={"path": str(target), "deleted_hash": digest},
            summary=f"deleted {target.name}",
        )


class FileMoveTool(Tool):
    spec = ToolSpec(
        name="file_move",
        description="Move or rename a file. Both paths must be inside the permitted area.",
        category="filesystem",
        parameters={
            "type": "object",
            "properties": {"source": {"type": "string"}, "destination": {"type": "string"}, "overwrite": {"type": "boolean"}},
            "required": ["source", "destination"],
        },
        permission_level=1,
        risk_level="medium",
        timeout_seconds=20,
        mutates_state=True,
    )

    async def execute(
        self, ctx: ToolContext, source: str, destination: str, overwrite: bool = False
    ) -> ToolResult:
        src = Path(ctx.resolve(source))
        dst = Path(ctx.resolve(destination))
        # Both ends are checked: a permitted source must not be moved to a
        # location the agent has no business writing to.
        ctx.require_permission(src, write=True)
        ctx.require_permission(dst, write=True)

        if not src.exists():
            return ToolResult(ok=False, error=f"No such file: {src}", summary="not found")
        if dst.exists() and not overwrite:
            return ToolResult(ok=False, error=f"{dst} already exists", summary="destination exists")

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        ctx.consume_write()
        ctx.register_artifact("files_moved", f"{src} -> {dst}")
        return ToolResult(ok=True, data={"source": str(src), "destination": str(dst)}, summary=f"moved to {dst.name}")


class FileCopyTool(Tool):
    spec = ToolSpec(
        name="file_copy",
        description="Copy a file or directory. Both paths must be inside the permitted area.",
        category="filesystem",
        parameters={
            "type": "object",
            "properties": {"source": {"type": "string"}, "destination": {"type": "string"}, "overwrite": {"type": "boolean"}},
            "required": ["source", "destination"],
        },
        permission_level=1,
        risk_level="medium",
        timeout_seconds=60,
        mutates_state=True,
    )

    async def execute(
        self, ctx: ToolContext, source: str, destination: str, overwrite: bool = False
    ) -> ToolResult:
        src = Path(ctx.resolve(source))
        dst = Path(ctx.resolve(destination))
        ctx.require_permission(src, write=False)
        ctx.require_permission(dst, write=True)

        if not src.exists():
            return ToolResult(ok=False, error=f"No such path: {src}", summary="not found")
        if dst.exists():
            if not overwrite:
                return ToolResult(ok=False, error=f"{dst} already exists", summary="destination exists")
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()

        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        ctx.consume_write()
        ctx.register_artifact("files_copied", f"{src} -> {dst}")
        return ToolResult(ok=True, data={"source": str(src), "destination": str(dst)}, summary=f"copied to {dst.name}")


# --------------------------------------------------------------------------- #
# file_exists / file_metadata
# --------------------------------------------------------------------------- #
class FileExistsTool(Tool):
    spec = ToolSpec(
        name="file_exists",
        description="Check whether a path exists and what kind of thing it is.",
        category="filesystem",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        permission_level=0,
        risk_level="low",
        timeout_seconds=10,
        mutates_state=False,
    )

    async def execute(self, ctx: ToolContext, path: str) -> ToolResult:
        target = Path(ctx.resolve(path))
        ctx.require_permission(target, write=False)
        exists = target.exists()
        return ToolResult(
            ok=True,
            data={
                "path": str(target),
                "exists": exists,
                "is_file": target.is_file() if exists else False,
                "is_dir": target.is_dir() if exists else False,
            },
            summary=f"{target.name}: {'exists' if exists else 'missing'}",
        )


class FileMetadataTool(Tool):
    spec = ToolSpec(
        name="file_metadata",
        description="Size, timestamps, permissions and hash for a path.",
        category="filesystem",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        permission_level=0,
        risk_level="low",
        timeout_seconds=15,
        mutates_state=False,
    )

    async def execute(self, ctx: ToolContext, path: str) -> ToolResult:
        target = Path(ctx.resolve(path))
        ctx.require_permission(target, write=False)
        if not target.exists():
            return ToolResult(ok=False, error=f"No such path: {target}", summary="not found")

        stat = target.stat()
        data: dict[str, Any] = {
            "path": str(target),
            "name": target.name,
            "is_file": target.is_file(),
            "is_dir": target.is_dir(),
            "size_bytes": stat.st_size,
            "size_human": _human(stat.st_size),
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_ctime)),
            "readable": os.access(target, os.R_OK),
            "writable": os.access(target, os.W_OK),
        }
        if target.is_file() and stat.st_size <= 32 * 1024 * 1024:
            data["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        return ToolResult(ok=True, data=data, summary=f"{target.name} ({data['size_human']})")


# --------------------------------------------------------------------------- #
# directory_list / directory_create / directory_delete
# --------------------------------------------------------------------------- #
class DirectoryListTool(Tool):
    spec = ToolSpec(
        name="directory_list",
        description="List a directory: names, kinds, sizes. Set recursive for a bounded tree walk.",
        category="filesystem",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "recursive": {"type": "boolean"},
                "max_depth": {"type": "integer"},
                "pattern": {"type": "string", "description": "Glob filter, e.g. *.py"},
                "include_hidden": {"type": "boolean"},
            },
            "required": ["path"],
        },
        permission_level=0,
        risk_level="low",
        timeout_seconds=30,
        mutates_state=False,
    )

    async def execute(
        self,
        ctx: ToolContext,
        path: str,
        recursive: bool = False,
        max_depth: int = 3,
        pattern: str = "",
        include_hidden: bool = False,
    ) -> ToolResult:
        root = Path(ctx.resolve(path))
        ctx.require_permission(root, write=False)
        if not root.exists():
            return ToolResult(ok=False, error=f"No such directory: {root}", summary="not found")
        if not root.is_dir():
            return ToolResult(ok=False, error=f"{root} is not a directory", summary="not a directory")

        depth_limit = max(1, min(int(max_depth or 3), MAX_WALK_DEPTH))
        entries: list[dict[str, Any]] = []
        skipped = 0

        def walk(current: Path, depth: int) -> None:
            nonlocal skipped
            if len(entries) >= MAX_LIST_ENTRIES:
                return
            try:
                children = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            except PermissionError:
                skipped += 1
                return
            for child in children:
                if len(entries) >= MAX_LIST_ENTRIES:
                    return
                if not include_hidden and child.name.startswith("."):
                    continue
                if pattern and not fnmatch.fnmatch(child.name, pattern) and not (
                    child.is_dir() and recursive
                ):
                    continue
                try:
                    size = child.stat().st_size if child.is_file() else 0
                except OSError:
                    size = 0
                entries.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "type": "dir" if child.is_dir() else "file",
                        "size_bytes": size,
                        "size_human": _human(size) if child.is_file() else "",
                        "depth": depth,
                    }
                )
                if recursive and child.is_dir() and depth < depth_limit:
                    walk(child, depth + 1)

        walk(root, 1)
        return ToolResult(
            ok=True,
            data={
                "path": str(root),
                "entries": entries,
                "count": len(entries),
                "truncated": len(entries) >= MAX_LIST_ENTRIES,
                "skipped_unreadable": skipped,
            },
            summary=f"listed {len(entries)} entries in {root.name}",
        )


class DirectoryCreateTool(Tool):
    spec = ToolSpec(
        name="directory_create",
        description="Create a directory, including missing parents.",
        category="filesystem",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        permission_level=1,
        risk_level="low",
        timeout_seconds=15,
        mutates_state=True,
    )

    async def execute(self, ctx: ToolContext, path: str) -> ToolResult:
        target = Path(ctx.resolve(path))
        ctx.require_permission(target, write=True)
        existed = target.exists()
        target.mkdir(parents=True, exist_ok=True)
        if not existed:
            ctx.consume_write()
        return ToolResult(
            ok=True,
            data={"path": str(target), "created": not existed},
            summary=("created " if not existed else "already existed: ") + target.name,
        )


class DirectoryDeleteTool(Tool):
    spec = ToolSpec(
        name="directory_delete",
        description=(
            "Delete a directory. Non-empty directories are refused unless "
            "recursive is true, which always asks for confirmation first."
        ),
        category="filesystem",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "recursive": {"type": "boolean", "description": "Required to delete a non-empty directory"},
            },
            "required": ["path"],
        },
        permission_level=1,
        risk_level="high",
        timeout_seconds=60,
        mutates_state=True,
        requires_confirmation=True,
    )

    async def execute(self, ctx: ToolContext, path: str, recursive: bool = False) -> ToolResult:
        target = Path(ctx.resolve(path))
        ctx.require_permission(target, write=True)

        if not target.exists():
            return ToolResult(ok=False, error=f"No such directory: {target}", summary="not found")
        if not target.is_dir():
            return ToolResult(ok=False, error=f"{target} is not a directory", summary="not a directory")

        # Refuse the obviously catastrophic targets even if a broad path was
        # granted: these are never a legitimate agent action.
        if target == target.anchor or len(target.parts) <= 2:
            return ToolResult(
                ok=False,
                error=f"Refusing to delete {target}: it is too close to a filesystem root",
                summary="refused: root-level path",
            )

        children = list(target.iterdir())
        if children and not recursive:
            return ToolResult(
                ok=False,
                error=f"{target} is not empty ({len(children)} entries) - pass recursive=true to delete it",
                summary="directory not empty",
            )

        removed = sum(1 for _ in target.rglob("*")) if recursive else len(children)
        shutil.rmtree(target)
        ctx.consume_write()
        ctx.register_artifact("directories_deleted", str(target))
        return ToolResult(
            ok=True,
            data={"path": str(target), "entries_removed": removed},
            summary=f"deleted {target.name} ({removed} entries)",
        )


# --------------------------------------------------------------------------- #
# file_search
# --------------------------------------------------------------------------- #
class FileSearchTool(Tool):
    spec = ToolSpec(
        name="file_search",
        description=(
            "Find files by name pattern, or search inside files by content. "
            "Returns matching paths (and matching lines for a content search)."
        ),
        category="filesystem",
        parameters={
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Directory to search (default: working directory)"},
                "name_pattern": {"type": "string", "description": "Glob, e.g. *.py or test_*.ts"},
                "content": {"type": "string", "description": "Substring or regex to find inside files"},
                "regex": {"type": "boolean"},
                "case_sensitive": {"type": "boolean"},
                "max_results": {"type": "integer"},
                "max_depth": {"type": "integer"},
            },
            "required": [],
        },
        permission_level=0,
        risk_level="low",
        timeout_seconds=45,
        mutates_state=False,
    )

    async def execute(
        self,
        ctx: ToolContext,
        root: str = "",
        name_pattern: str = "",
        content: str = "",
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 100,
        max_depth: int = 8,
    ) -> ToolResult:
        if not name_pattern and not content:
            raise ValidationError("Provide either name_pattern or content")

        base = Path(ctx.resolve(root)) if root else Path(ctx.resolve("."))
        ctx.require_permission(base, write=False)
        if not base.is_dir():
            return ToolResult(ok=False, error=f"No such directory: {base}", summary="not found")

        limit = max(1, min(int(max_results or 100), MAX_SEARCH_RESULTS))
        depth_limit = max(1, min(int(max_depth or 8), MAX_WALK_DEPTH))

        matcher = None
        if content:
            import re as _re

            flags = 0 if case_sensitive else _re.IGNORECASE
            try:
                matcher = _re.compile(content if regex else _re.escape(content), flags)
            except _re.error as exc:
                raise ValidationError(f"Invalid regex: {exc}") from exc

        needle = content if case_sensitive else content.lower()
        matches: list[dict[str, Any]] = []
        scanned = 0
        skipped_denied = 0

        for dirpath, dirnames, filenames in os.walk(base):
            current = Path(dirpath)
            if len(current.relative_to(base).parts) >= depth_limit:
                dirnames[:] = []
            # Prune noise before descending.
            dirnames[:] = [
                d
                for d in dirnames
                if d not in {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".next"}
            ]

            for filename in filenames:
                if len(matches) >= limit:
                    break
                if name_pattern and not fnmatch.fnmatch(filename, name_pattern):
                    continue

                candidate = current / filename
                # A search must not become a way to read protected files.
                verdict = ctx.permissions.check_path(candidate, write=False) if ctx.permissions else None
                if verdict is not None and not verdict.allowed:
                    skipped_denied += 1
                    continue

                if matcher is None:
                    matches.append({"path": str(candidate), "kind": "name"})
                    continue

                if not _looks_textual(candidate):
                    continue
                try:
                    if candidate.stat().st_size > 2_000_000:
                        continue
                    text = candidate.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                scanned += 1
                for number, line in enumerate(text.splitlines(), start=1):
                    haystack = line if case_sensitive else line.lower()
                    hit = matcher.search(line) if regex else (needle in haystack)
                    if hit:
                        matches.append(
                            {
                                "path": str(candidate),
                                "kind": "content",
                                "line": number,
                                "text": line.strip()[:300],
                            }
                        )
                        break  # one hit per file keeps the result readable
                if len(matches) >= limit:
                    break

        return ToolResult(
            ok=True,
            data={
                "root": str(base),
                "matches": matches,
                "count": len(matches),
                "files_scanned": scanned,
                "skipped_denied": skipped_denied,
                "truncated": len(matches) >= limit,
            },
            summary=f"found {len(matches)} match(es)",
        )


def build_filesystem_tools() -> list[Tool]:
    return [
        FileReadTool(),
        FileWriteTool(),
        FileAppendTool(),
        FileDeleteTool(),
        FileMoveTool(),
        FileCopyTool(),
        FileExistsTool(),
        FileMetadataTool(),
        DirectoryListTool(),
        DirectoryCreateTool(),
        DirectoryDeleteTool(),
        FileSearchTool(),
        # Document generation belongs with the file tools: same permission
        # checks, same workspace sandbox, same audit trail.
        DocumentWriteTool(),
        DocumentReadTool(),
    ]


__all__ = ["build_filesystem_tools"]

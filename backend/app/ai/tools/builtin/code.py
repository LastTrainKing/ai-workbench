"""Code tools — search, replace, patch and analyse source files.

Editing code is the operation an agent gets wrong most often, so these tools are
built around making the change *checkable* rather than merely possible:

* ``code_replace`` requires the old text to appear exactly once unless
  ``allow_multiple`` is set. Ambiguous edits are the single most common way an
  agent corrupts a file, and a tool that silently replaces three occurrences when
  the model meant one is worse than a tool that refuses.
* Every write returns the hash before and after, so the change can be diffed and
  rolled back by the run's snapshot.
* ``code_patch`` applies a unified diff and reports how many hunks landed, rather
  than reporting success for a patch that only partly applied.
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path
from typing import Any

from app.ai.agent.tools import Tool, ToolContext
from app.ai.types import ToolResult, ToolSpec
from app.core.errors import ValidationError

MAX_SCAN_BYTES = 2_000_000


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load(ctx: ToolContext, path: str, *, write: bool) -> tuple[Path, str]:
    target = Path(ctx.resolve(path))
    ctx.require_permission(target, write=write)
    if not target.is_file():
        raise ValidationError(f"No such file: {target}")
    if target.stat().st_size > MAX_SCAN_BYTES:
        raise ValidationError(f"{target} is larger than the {MAX_SCAN_BYTES}-byte editing limit")
    try:
        return target, target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{target} is not a UTF-8 text file") from exc


# --------------------------------------------------------------------------- #
# code_search
# --------------------------------------------------------------------------- #
class CodeSearchTool(Tool):
    spec = ToolSpec(
        name="code_search",
        description=(
            "Search source files for a pattern and return file, line number and "
            "the matching line. Supports regular expressions."
        ),
        category="code",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "File or directory (default: working directory)"},
                "file_pattern": {"type": "string", "description": "Glob, e.g. *.py"},
                "regex": {"type": "boolean"},
                "case_sensitive": {"type": "boolean"},
                "max_results": {"type": "integer"},
            },
            "required": ["pattern"],
        },
        permission_level=0,
        risk_level="low",
        timeout_seconds=60,
        mutates_state=False,
    )

    async def execute(
        self,
        ctx: ToolContext,
        pattern: str,
        path: str = "",
        file_pattern: str = "",
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> ToolResult:
        base = Path(ctx.resolve(path)) if path else Path(ctx.resolve("."))
        ctx.require_permission(base, write=False)

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            matcher = re.compile(pattern if regex else re.escape(pattern), flags)
        except re.error as exc:
            raise ValidationError(f"Invalid regular expression: {exc}") from exc

        limit = max(1, min(int(max_results or 100), 500))
        skip_dirs = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".next"}
        targets: list[Path] = []

        if base.is_file():
            targets = [base]
        else:
            for child in base.rglob(file_pattern or "*"):
                if any(part in skip_dirs for part in child.parts):
                    continue
                if child.is_file():
                    targets.append(child)
                if len(targets) > 5000:
                    break

        hits: list[dict[str, Any]] = []
        scanned = 0
        for candidate in targets:
            if len(hits) >= limit:
                break
            if ctx.permissions is not None and not ctx.permissions.check_path(candidate).allowed:
                continue
            try:
                if candidate.stat().st_size > MAX_SCAN_BYTES:
                    continue
                text = candidate.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            scanned += 1
            for number, line in enumerate(text.splitlines(), start=1):
                if matcher.search(line):
                    hits.append(
                        {
                            "path": str(candidate),
                            "line": number,
                            "text": line.strip()[:240],
                        }
                    )
                    if len(hits) >= limit:
                        break

        return ToolResult(
            ok=True,
            data={"matches": hits, "count": len(hits), "files_scanned": scanned, "truncated": len(hits) >= limit},
            summary=f"{len(hits)} match(es) in {scanned} file(s)",
        )


# --------------------------------------------------------------------------- #
# code_replace
# --------------------------------------------------------------------------- #
class CodeReplaceTool(Tool):
    spec = ToolSpec(
        name="code_replace",
        description=(
            "Replace an exact block of text in a file. The old text must appear "
            "exactly once unless allow_multiple is set — an ambiguous edit is "
            "refused rather than guessed at. Returns the hash before and after."
        ),
        category="code",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string", "description": "Must match exactly, including indentation"},
                "new_text": {"type": "string"},
                "allow_multiple": {"type": "boolean", "description": "Replace every occurrence"},
            },
            "required": ["path", "old_text", "new_text"],
        },
        permission_level=1,
        risk_level="low",
        timeout_seconds=30,
        mutates_state=True,
    )
    is_write = True

    async def execute(
        self,
        ctx: ToolContext,
        path: str,
        old_text: str,
        new_text: str,
        allow_multiple: bool = False,
    ) -> ToolResult:
        target, original = _load(ctx, path, write=True)
        if not old_text:
            raise ValidationError("old_text must not be empty")

        occurrences = original.count(old_text)
        if occurrences == 0:
            return ToolResult(
                ok=False,
                error="The text to replace was not found. Read the file again — the content may differ in whitespace.",
                summary="no match",
            )
        if occurrences > 1 and not allow_multiple:
            return ToolResult(
                ok=False,
                error=(
                    f"The text appears {occurrences} times. Include more surrounding context "
                    "to make the edit unambiguous, or set allow_multiple to replace all."
                ),
                summary=f"ambiguous: {occurrences} matches",
            )

        updated = original.replace(old_text, new_text)
        target.write_text(updated, encoding="utf-8")
        ctx.consume_write()
        ctx.register_artifact("files_modified", str(target))

        return ToolResult(
            ok=True,
            data={
                "path": str(target),
                "replacements": occurrences,
                "original_hash": _digest(original),
                "modified_hash": _digest(updated),
                "original_lines": original.count("\n") + 1,
                "modified_lines": updated.count("\n") + 1,
            },
            summary=f"replaced {occurrences} occurrence(s) in {target.name}",
        )


# --------------------------------------------------------------------------- #
# code_patch
# --------------------------------------------------------------------------- #
class CodePatchTool(Tool):
    spec = ToolSpec(
        name="code_patch",
        description=(
            "Apply a unified diff to one file. Reports how many hunks were applied "
            "and fails when a hunk does not match, rather than reporting success "
            "for a partial patch."
        ),
        category="code",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "patch": {"type": "string", "description": "Unified diff content"},
            },
            "required": ["path", "patch"],
        },
        permission_level=1,
        risk_level="low",
        timeout_seconds=30,
        mutates_state=True,
    )
    is_write = True

    async def execute(self, ctx: ToolContext, path: str, patch: str) -> ToolResult:
        target, original = _load(ctx, path, write=True)

        hunks = _parse_hunks(patch)
        if not hunks:
            raise ValidationError("The patch contains no hunks (no lines starting with @@)")

        lines = original.splitlines(keepends=True)
        offset = 0
        applied = 0
        for start, old_lines, new_lines in hunks:
            index = start - 1 + offset
            window = lines[index : index + len(old_lines)]
            if [line.rstrip("\r\n") for line in window] != [line.rstrip("\r\n") for line in old_lines]:
                return ToolResult(
                    ok=False,
                    error=(
                        f"Hunk at line {start} did not match the file. "
                        f"Applied {applied} of {len(hunks)} hunks; the file was left unchanged."
                    ),
                    summary=f"hunk mismatch at line {start}",
                )
            lines[index : index + len(old_lines)] = new_lines
            offset += len(new_lines) - len(old_lines)
            applied += 1

        updated = "".join(lines)
        target.write_text(updated, encoding="utf-8")
        ctx.consume_write()
        ctx.register_artifact("files_modified", str(target))

        return ToolResult(
            ok=True,
            data={
                "path": str(target),
                "hunks_applied": applied,
                "hunks_total": len(hunks),
                "original_hash": _digest(original),
                "modified_hash": _digest(updated),
            },
            summary=f"applied {applied}/{len(hunks)} hunks to {target.name}",
        )


def _parse_hunks(patch: str) -> list[tuple[int, list[str], list[str]]]:
    """Extract (start_line, old_lines, new_lines) from a unified diff."""
    hunks: list[tuple[int, list[str], list[str]]] = []
    current: dict[str, Any] | None = None
    header = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")

    for raw in patch.splitlines():
        match = header.match(raw)
        if match:
            if current:
                hunks.append((current["start"], current["old"], current["new"]))
            current = {"start": int(match.group(1)), "old": [], "new": []}
            continue
        if current is None:
            continue  # file headers (--- / +++) and preamble
        if raw.startswith("---") or raw.startswith("+++"):
            continue
        if raw.startswith("-"):
            current["old"].append(raw[1:])
        elif raw.startswith("+"):
            current["new"].append(raw[1:])
        elif raw.startswith(" "):
            current["old"].append(raw[1:])
            current["new"].append(raw[1:])
        elif raw.startswith("\\"):  # "\ No newline at end of file"
            continue

    if current:
        hunks.append((current["start"], current["old"], current["new"]))
    return hunks


# --------------------------------------------------------------------------- #
# code_analyze
# --------------------------------------------------------------------------- #
class CodeAnalyzeTool(Tool):
    spec = ToolSpec(
        name="code_analyze",
        description=(
            "Structural summary of a source file: classes, functions, imports and "
            "line count. For Python it parses the AST; for other languages it uses "
            "declaration patterns."
        ),
        category="code",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        permission_level=0,
        risk_level="low",
        timeout_seconds=30,
        mutates_state=False,
    )

    async def execute(self, ctx: ToolContext, path: str) -> ToolResult:
        target, text = _load(ctx, path, write=False)
        suffix = target.suffix.lower()
        lines = text.splitlines()

        analysis: dict[str, Any] = {
            "path": str(target),
            "language": suffix.lstrip(".") or "text",
            "line_count": len(lines),
            "sha256": _digest(text),
            "classes": [],
            "functions": [],
            "imports": [],
            "parse_method": "pattern",
        }

        if suffix == ".py":
            try:
                tree = ast.parse(text)
                analysis["parse_method"] = "ast"
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        analysis["classes"].append(
                            {
                                "name": node.name,
                                "line": node.lineno,
                                "bases": [
                                    getattr(b, "id", getattr(b, "attr", "?")) for b in node.bases
                                ],
                                "methods": [
                                    child.name
                                    for child in node.body
                                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                                ],
                            }
                        )
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        analysis["functions"].append(
                            {
                                "name": node.name,
                                "line": node.lineno,
                                "args": [a.arg for a in node.args.args],
                                "async": isinstance(node, ast.AsyncFunctionDef),
                                "docstring": bool(ast.get_docstring(node)),
                            }
                        )
                    elif isinstance(node, ast.Import):
                        analysis["imports"] += [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        analysis["imports"].append(node.module or "")
            except SyntaxError as exc:
                analysis["parse_method"] = "pattern (syntax error)"
                analysis["syntax_error"] = f"line {exc.lineno}: {exc.msg}"
        else:
            for number, line in enumerate(lines, start=1):
                stripped = line.strip()
                if re.match(r"^(export\s+)?(default\s+)?class\s+\w+", stripped):
                    analysis["classes"].append({"name": re.findall(r"class\s+(\w+)", stripped)[0], "line": number})
                elif re.match(r"^(export\s+)?(async\s+)?function\s+\w+", stripped):
                    analysis["functions"].append({"name": re.findall(r"function\s+(\w+)", stripped)[0], "line": number})
                elif re.match(r"^(export\s+)?const\s+\w+\s*=\s*(async\s*)?\(", stripped):
                    analysis["functions"].append(
                        {"name": re.findall(r"const\s+(\w+)", stripped)[0], "line": number}
                    )
                elif re.match(r"^(import|from|require\()", stripped):
                    analysis["imports"].append(stripped[:120])

        return ToolResult(
            ok=True,
            data=analysis,
            summary=(
                f"{target.name}: {len(analysis['classes'])} class(es), "
                f"{len(analysis['functions'])} function(s), {len(lines)} lines"
            ),
        )


def build_code_tools() -> list[Tool]:
    return [CodeSearchTool(), CodeReplaceTool(), CodePatchTool(), CodeAnalyzeTool()]


__all__ = ["build_code_tools"]

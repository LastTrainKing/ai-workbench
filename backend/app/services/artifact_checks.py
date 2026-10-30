"""Reading a produced file back, by the rules that file's type actually has.

`file_exists` is not verification. A zero-byte `report.md`, a `.docx` that is
really a renamed text file, and a deck with no slides all pass "the file is
there" and all fail the user. This module is the answer to "is this file what it
claims to be", asked once per kind.

**Three outcomes, not two.** A check can pass, fail, or not have been run at
all — and the third is not the second. When `python-docx` is missing the honest
answer is "I could not open it", not "it is broken". Collapsing those two would
either libel a good file or bless an unread one, so `make_verification` treats
`unverified` as neither pass nor failure, and `passed` is false either way.

That is also why a check never silently disappears: a kind with no reader gets
an explicit `unverified` row naming the reason, so the count on screen is the
count of questions actually asked.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

#: Plain text the product writes and can decode itself.
TEXT_KINDS = {"md", "txt", "report"}

#: Office containers: a zip whose part names are the format's own contract.
CONTAINER_PARTS = {
    "docx": "word/document.xml",
    "xlsx": "xl/workbook.xml",
    "pptx": "ppt/presentation.xml",
}

#: Extensions the readback readers know how to open.
READABLE_KINDS = {"pptx", "docx", "xlsx", "pdf", "csv"}

#: What the agent writes when the task is source code. Deliberately narrow: a file
#: is only "code" if the producing run said so.
CODE_KINDS = {"code"}


def _check(name: str, outcome: str, detail: str) -> dict[str, Any]:
    """One question and its answer.

    `ok` is kept as a derived convenience for the callers that only want a boolean;
    `outcome` is the field that carries the real three-way answer.
    """
    return {"name": name, "outcome": outcome, "ok": outcome == "pass", "detail": detail}


def _human_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} 字节"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _read_text_strict(target: Path) -> tuple[str | None, str]:
    """Decode as UTF-8 without the forgiving error handler.

    `errors="replace"` is right for *reading a file to show someone* and wrong for
    *judging it*: it turns a mis-encoded file into a string full of U+FFFD and
    reports success. Verification uses the strict decoder precisely so that a
    mojibake file fails here instead of at the user's eyes.
    """
    try:
        return target.read_text(encoding="utf-8-sig"), ""
    except UnicodeDecodeError as exc:
        return None, f"不是有效的 UTF-8：{exc.reason}（位置 {exc.start}）"


def _container_checks(kind: str, target: Path) -> list[dict[str, Any]]:
    """A zip container, then the parts that make it that format."""
    checks: list[dict[str, Any]] = []
    if not zipfile.is_zipfile(target):
        checks.append(_check("container", "fail", f"{kind} 是一个 zip 容器，这个文件不是。"))
        return checks
    checks.append(_check("container", "pass", "zip 容器可读"))

    required = CONTAINER_PARTS[kind]
    try:
        with zipfile.ZipFile(target) as archive:
            names = set(archive.namelist())
    except Exception as exc:  # a truncated or password-locked archive
        checks.append(
            _check("parts", "fail", f"打不开压缩包内容：{type(exc).__name__}: {exc}"[:200])
        )
        return checks

    if required in names:
        checks.append(_check("parts", "pass", f"包含 {required}"))
    else:
        checks.append(_check("parts", "fail", f"缺少必要部件 {required}"))
    return checks


def _readback_check(kind: str, target: Path) -> dict[str, Any]:
    """Re-open the file with the library that writes that format.

    A missing library yields `unverified`, carrying the dependency's own message.
    """
    try:
        from app.ai.tools.builtin.documents import (
            _read_csv,
            _read_docx,
            _read_pdf,
            _read_pptx,
            _read_xlsx,
        )

        readers = {
            "pptx": _read_pptx,
            "docx": _read_docx,
            "xlsx": _read_xlsx,
            "pdf": _read_pdf,
            "csv": _read_csv,
        }
        payload = readers[kind](target)
    except Exception as exc:
        # ImportError is wrapped by the tool layer into ValidationError with an
        # actionable message; anything else is a genuinely broken file. Neither is
        # a pass, and both are reported with the reason attached.
        return _check("readback", "unverified", f"{type(exc).__name__}: {exc}"[:200])

    if kind == "pptx":
        count = int(payload.get("slide_count") or 0)
        return _check(
            "readback",
            "pass" if count > 0 else "fail",
            f"{count} 页幻灯片" if count else "演示文稿里没有幻灯片",
        )
    if kind == "docx":
        paragraphs = int(payload.get("paragraphs") or 0)
        return _check(
            "readback",
            "pass" if paragraphs > 0 else "fail",
            f"{paragraphs} 个段落" if paragraphs else "文档里没有段落",
        )
    if kind == "xlsx":
        sheets = int(payload.get("sheet_count") or 0)
        return _check(
            "readback",
            "pass" if sheets > 0 else "fail",
            f"{sheets} 个工作表，{payload.get('charts')} 个图表"
            if sheets
            else "工作簿里没有工作表",
        )
    if kind == "pdf":
        pages = int(payload.get("page_count") or 0)
        detail = f"{pages} 页"
        # A scanned PDF has no text layer. That is worth saying out loud rather
        # than passing off as a readable document — and it is not a failure.
        if payload.get("note"):
            detail += f"；{payload['note']}"
        return _check("readback", "pass" if pages > 0 else "fail", detail)
    if kind == "csv":
        rows = int(payload.get("row_count") or 0)
        return _check(
            "readback",
            "pass" if rows > 0 else "fail",
            f"{rows} 行，{payload.get('columns')} 列" if rows else "CSV 里没有数据行",
        )
    return _check("readback", "unverified", f"{kind} 没有可用的读取器")


def checks_for(kind: str, target: Path) -> list[dict[str, Any]]:
    """Every question that applies to this kind of file, in order.

    The first check is always existence, and a file that is not there stops the
    list — asking whether a missing file has slides is not a question.
    """
    kind = (kind or "").lower()

    if not target.is_file():
        return [_check("file_exists", "fail", "文件不存在")]

    checks: list[dict[str, Any]] = [
        _check("file_exists", "pass", _human_bytes(target.stat().st_size))
    ]

    if kind in CONTAINER_PARTS:
        checks.extend(_container_checks(kind, target))
        # A file that failed its container check is not worth opening; the reader
        # would raise the same thing with less context.
        if any(check["outcome"] == "fail" for check in checks):
            return checks
        checks.append(_readback_check(kind, target))
        return checks

    if kind in READABLE_KINDS:
        checks.append(_readback_check(kind, target))
        return checks

    if kind in TEXT_KINDS:
        text, problem = _read_text_strict(target)
        if text is None:
            checks.append(_check("encoding", "fail", problem))
            return checks
        checks.append(_check("encoding", "pass", "UTF-8"))
        stripped = text.strip()
        checks.append(
            _check(
                "content",
                "pass" if stripped else "fail",
                f"{len(text)} 个字符" if stripped else "文件是空的",
            )
        )
        return checks

    if kind in CODE_KINDS:
        text, problem = _read_text_strict(target)
        checks.append(
            _check("encoding", "pass", "UTF-8")
            if text is not None
            else _check("encoding", "fail", problem)
        )
        # §10: "没有运行就不能声称已编译或通过测试". This stage does not run a
        # build, so it says so instead of implying one happened.
        checks.append(
            _check(
                "runtime_check", "unverified", "本次没有运行任何编译或测试，不对代码正确性下结论"
            )
        )
        return checks

    # Unknown kind. If it decodes as text, say so; otherwise say plainly that
    # nothing was checked, rather than reporting a pass nobody performed.
    text, _ = _read_text_strict(target)
    if text is not None and text.strip():
        checks.append(_check("readback", "pass", f"可读文本，{len(text)} 个字符"))
    else:
        checks.append(_check("readback", "unverified", f"没有针对 {kind or '未知类型'} 的检查规则"))
    return checks


__all__ = ["CONTAINER_PARTS", "READABLE_KINDS", "checks_for"]

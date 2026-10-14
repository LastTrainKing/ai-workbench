"""Per-type verification: the questions each format actually has.

§10 of the release task book. The failures this file is written against are all of the
same shape — a check that reports something it did not establish:

* a zero-byte `report.md` passing "the file exists"
* a text file renamed to `.docx` passing because a file was found at that path
* a kind with no reader being reported as verified, or as broken
* a code deliverable being reported as "compiled" when nothing was run

The last two are why `unverified` exists as a third outcome. A check that was not
performed is neither a pass nor a failure, and `make_verification` must not let either
of them absorb it.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from app.models.delivery import make_verification
from app.services.artifact_checks import checks_for


def _outcomes(checks: list[dict]) -> list[str]:
    return [check["outcome"] for check in checks]


def _named(checks: list[dict], name: str) -> dict:
    for check in checks:
        if check["name"] == name:
            return check
    raise AssertionError(f"no check named {name!r} in {[c['name'] for c in checks]}")


# --------------------------------------------------------------------------- #
# The file is not there
# --------------------------------------------------------------------------- #
def test_a_missing_file_is_one_failed_check_and_no_questions_after_it(tmp_path: Path):
    """Asking whether a file that does not exist has slides is not a question."""
    checks = checks_for("pptx", tmp_path / "gone.pptx")

    assert _outcomes(checks) == ["fail"]
    assert checks[0]["name"] == "file_exists"
    assert not make_verification(checks)["passed"]


# --------------------------------------------------------------------------- #
# Text
# --------------------------------------------------------------------------- #
def test_an_empty_markdown_file_does_not_pass(tmp_path: Path):
    """The exact case "the file exists" cannot tell apart from a real report."""
    target = tmp_path / "report.md"
    target.write_text("", encoding="utf-8")

    checks = checks_for("md", target)

    assert _outcomes(checks) == ["pass", "pass", "fail"]
    assert not make_verification(checks)["passed"]


def test_a_real_markdown_file_passes_and_reports_its_size(tmp_path: Path):
    target = tmp_path / "report.md"
    target.write_text("# 目录\n\n第一章\n", encoding="utf-8")

    checks = checks_for("md", target)
    result = make_verification(checks)

    assert _outcomes(checks) == ["pass", "pass", "pass"]
    assert result["passed"]
    assert _named(checks, "content")["detail"] == "10 个字符"


def test_mis_encoded_text_fails_on_the_encoding_not_on_being_empty(tmp_path: Path):
    """`errors="replace"` would have turned this into a string of U+FFFD and passed it.

    The distinction matters to the person reading the result: "it is empty" sends them
    to rewrite the file, "it is not UTF-8" sends them to fix the encoder.
    """
    target = tmp_path / "notes.txt"
    target.write_bytes("中文".encode("gbk"))

    checks = checks_for("txt", target)

    assert _outcomes(checks) == ["pass", "fail"]
    assert "UTF-8" in _named(checks, "encoding")["detail"]


# --------------------------------------------------------------------------- #
# Containers
# --------------------------------------------------------------------------- #
def test_a_renamed_text_file_is_not_a_docx(tmp_path: Path):
    """The container check is what catches a `.docx` that is really a `.txt`."""
    target = tmp_path / "report.docx"
    target.write_text("这不是一个 Word 文档", encoding="utf-8")

    checks = checks_for("docx", target)

    assert _outcomes(checks) == ["pass", "fail"]
    assert _named(checks, "container")["outcome"] == "fail"
    # The reader is not asked: opening a non-container would raise the same thing
    # with less context, and a second failure says nothing new.
    assert "readback" not in [check["name"] for check in checks]


def test_a_zip_without_the_format_s_parts_fails_on_parts(tmp_path: Path):
    """A zip is not a `.docx`. The part name is the format's own contract."""
    target = tmp_path / "report.docx"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("hello.txt", "not a document")

    checks = checks_for("docx", target)

    assert _outcomes(checks) == ["pass", "pass", "fail"]
    assert "word/document.xml" in _named(checks, "parts")["detail"]


def test_a_real_docx_is_read_back(tmp_path: Path):
    import docx

    target = tmp_path / "real.docx"
    document = docx.Document()
    document.add_paragraph("第一章")
    document.save(str(target))

    checks = checks_for("docx", target)

    assert _outcomes(checks) == ["pass", "pass", "pass", "pass"]
    assert make_verification(checks)["passed"]


# --------------------------------------------------------------------------- #
# The third outcome
# --------------------------------------------------------------------------- #
def test_a_kind_with_no_reader_is_unverified_not_verified(tmp_path: Path):
    """A binary of an unknown kind is not "verified" and is not "broken"."""
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\x00\x01\x02\xff\xfe")

    checks = checks_for("binary", target)

    assert _outcomes(checks) == ["pass", "unverified"]
    assert not make_verification(checks)["passed"]


def test_code_is_never_reported_as_tested(tmp_path: Path):
    """§10: 没有运行就不能声称已编译或通过测试."""
    target = tmp_path / "main.py"
    target.write_text("print('hi')\n", encoding="utf-8")

    checks = checks_for("code", target)

    assert _outcomes(checks) == ["pass", "pass", "unverified"]
    assert _named(checks, "runtime_check")["outcome"] == "unverified"


# --------------------------------------------------------------------------- #
# `make_verification` must not let an unverified check masquerade as either answer
# --------------------------------------------------------------------------- #
def test_unverified_is_counted_apart_from_failed():
    result = make_verification(
        [
            {"name": "a", "outcome": "pass"},
            {"name": "b", "outcome": "fail"},
            {"name": "c", "outcome": "unverified"},
        ]
    )

    assert result["passed"] is False
    assert result["failed_count"] == 1
    assert result["unverified_count"] == 1
    assert result["check_count"] == 3


def test_a_boolean_only_check_still_reads(tmp_path: Path):
    """Rows written before the third outcome existed carry `ok` alone."""
    result = make_verification([{"name": "legacy", "ok": True}])

    assert result["checks"][0]["outcome"] == "pass"
    assert result["passed"] is True
    assert result["unverified_count"] == 0


def test_an_unverified_check_alone_does_not_pass():
    """The failure this rule prevents: "we did not check" rendering as a green tick."""
    result = make_verification([{"name": "a", "outcome": "unverified"}])

    assert result["passed"] is False

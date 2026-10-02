"""Unit tests for security helpers, file storage safety and parsers."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.parsers.documents import (
    MarkdownParser,
    TextParser,
    normalise_text,
    parse_file,
)
from app.core.errors import (
    DocumentParseError,
    PayloadTooLargeError,
    StorageError,
    UnsupportedMediaTypeError,
)
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.infrastructure.files import (
    delete_file,
    ensure_within_root,
    sanitize_filename,
    validate_content_type,
    validate_extension,
)


# --------------------------------------------------------------------------- #
# passwords / JWT
# --------------------------------------------------------------------------- #
def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("correct horse battery")
    second = hash_password("correct horse battery")
    assert first != second, "bcrypt must use a random salt per hash"
    assert verify_password("correct horse battery", first)
    assert not verify_password("wrong password", first)


def test_verify_password_tolerates_corrupt_hash() -> None:
    assert verify_password("anything", "not-a-bcrypt-hash") is False
    assert verify_password("anything", "") is False


def test_long_passwords_are_truncated_not_crashing() -> None:
    long_password = "a" * 500
    hashed = hash_password(long_password)
    assert verify_password(long_password, hashed)


def test_jwt_roundtrip() -> None:
    token, expires = create_access_token("user-123", extra={"username": "alice"})
    payload = decode_access_token(token)
    assert payload["sub"] == "user-123"
    assert payload["username"] == "alice"
    assert payload["typ"] == "access"
    assert expires.timestamp() > 0


def test_invalid_jwt_is_rejected() -> None:
    from app.core.errors import AuthenticationError

    with pytest.raises(AuthenticationError):
        decode_access_token("not.a.jwt")


def test_expired_jwt_is_rejected() -> None:
    from app.core.errors import AuthenticationError

    token, _ = create_access_token("user-123", expires_minutes=-1)
    with pytest.raises(AuthenticationError) as exc:
        decode_access_token(token)
    assert exc.value.code == "token_expired"


# --------------------------------------------------------------------------- #
# filename sanitisation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        ("../../../etc/passwd", ".."),
        ("..\\..\\windows\\system32\\cmd.exe", "\\"),
        ("/absolute/path/file.md", "/"),
        ("file\x00name.txt", "\x00"),
        ("C:evil.txt", ":"),
    ],
)
def test_sanitize_filename_blocks_traversal(raw: str, must_not_contain: str) -> None:
    cleaned = sanitize_filename(raw)
    assert must_not_contain not in cleaned
    assert Path(cleaned).name == cleaned


def test_sanitize_filename_keeps_extension() -> None:
    assert sanitize_filename("notes.md").endswith(".md")
    assert sanitize_filename("REPORT.PDF").endswith(".PDF")


def test_sanitize_filename_handles_windows_reserved_names() -> None:
    assert sanitize_filename("CON.txt").startswith("_")
    assert sanitize_filename("nul").startswith("_")


def test_sanitize_filename_handles_empty_input() -> None:
    assert sanitize_filename("") == "untitled"


# --------------------------------------------------------------------------- #
# extension / content-type validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["a.md", "a.markdown", "a.txt", "a.pdf", "A.PDF"])
def test_allowed_extensions_pass(name: str) -> None:
    assert validate_extension(name)


@pytest.mark.parametrize("name", ["a.exe", "a.sh", "a.zip", "a", "a.php"])
def test_disallowed_extensions_are_rejected(name: str) -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        validate_extension(name)


def test_content_type_mismatch_is_rejected() -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        validate_content_type(".pdf", "text/html")


def test_content_type_octet_stream_is_accepted() -> None:
    validate_content_type(".pdf", "application/octet-stream")
    validate_content_type(".md", "text/markdown")


def test_missing_content_type_is_accepted() -> None:
    validate_content_type(".txt", None)


# --------------------------------------------------------------------------- #
# storage root containment
# --------------------------------------------------------------------------- #
def test_ensure_within_root_accepts_inside_path(tmp_path: Path) -> None:
    inside = tmp_path / "a" / "b.txt"
    inside.parent.mkdir(parents=True)
    inside.write_text("x", encoding="utf-8")
    assert ensure_within_root(inside, tmp_path) == inside.resolve()


def test_ensure_within_root_rejects_outside_path(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(StorageError):
        ensure_within_root(outside, root)


def test_delete_file_outside_root_is_refused(tmp_path: Path, monkeypatch) -> None:
    from app.core import config as config_module

    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(config_module.settings, "storage_dir", str(root), raising=False)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")

    assert delete_file(victim) is False
    assert victim.exists(), "a file outside the storage root must never be deleted"


# --------------------------------------------------------------------------- #
# parsers
# --------------------------------------------------------------------------- #
def test_markdown_parser_extracts_title_and_headings(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text("# 标题\n\n## 小节\n\n正文内容。\n", encoding="utf-8")
    parsed = MarkdownParser.parse(path)
    assert parsed.title == "标题"
    assert parsed.metadata["heading_count"] == 2
    assert "正文内容" in parsed.text


def test_text_parser_falls_back_to_gb18030(tmp_path: Path) -> None:
    path = tmp_path / "gbk.txt"
    path.write_bytes("中文内容测试".encode("gb18030"))
    parsed = TextParser.parse(path)
    assert "中文内容" in parsed.text
    assert parsed.metadata["encoding"] == "gb18030"


def test_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("   \n\n  ", encoding="utf-8")
    with pytest.raises(DocumentParseError):
        TextParser.parse(path)


def test_unsupported_extension_raises(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("a,b", encoding="utf-8")
    with pytest.raises(UnsupportedMediaTypeError):
        parse_file(path)


def test_corrupt_pdf_raises_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4 not really a pdf")
    with pytest.raises(DocumentParseError):
        parse_file(path)


def test_pdf_with_no_extractable_text_raises(tmp_path: Path) -> None:
    """A PDF with a valid structure but no text stream must fail loudly."""
    path = tmp_path / "scan.pdf"
    path.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >> endobj\n"
        b"trailer << /Size 4 /Root 1 0 R >>\n%%EOF\n"
    )
    with pytest.raises(DocumentParseError):
        parse_file(path)


def test_real_sample_pdf_parses() -> None:
    sample = Path(__file__).resolve().parents[3] / "samples" / "distributed-systems.pdf"
    if not sample.exists():
        pytest.skip("sample PDF fixture not generated")
    parsed = parse_file(sample)
    assert parsed.page_count == 3
    assert "CAP Theorem" in parsed.text


def test_normalise_text_collapses_whitespace() -> None:
    assert normalise_text("a\r\n\r\n\r\n\r\nb") == "a\n\n\nb"
    assert normalise_text("x   \ny") == "x\ny"
    assert normalise_text("a\u00a0b") == "a b"


def test_language_detection() -> None:
    assert MarkdownParser.parse  # parser available
    from app.ai.parsers.documents import _detect_language

    assert _detect_language("这是一个中文句子，用于测试语言检测。") == "zh"
    assert _detect_language("This is an English sentence.") == "en"
    assert _detect_language("") == "unknown"


# --------------------------------------------------------------------------- #
# oversize guard (streaming)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_streaming_upload_rejects_oversize_and_cleans_up() -> None:
    from app.infrastructure.files import save_upload

    class FakeStream:
        def __init__(self, total: int) -> None:
            self.remaining = total

        async def read(self, size: int) -> bytes:
            if self.remaining <= 0:
                return b""
            chunk = min(size, self.remaining)
            self.remaining -= chunk
            return b"a" * chunk

    from app.core.config import settings

    over = (settings.max_upload_mb + 1) * 1024 * 1024
    with pytest.raises(PayloadTooLargeError):
        await save_upload(FakeStream(over), "big.txt", content_type="text/plain")

    # Nothing must be left behind on disk after a rejected upload.
    leftovers = [p for p in settings.upload_path.glob("*.txt") if p.stat().st_size > 0]
    assert not leftovers, "an oversized upload must not leave a partial file"


@pytest.mark.asyncio
async def test_empty_upload_is_rejected() -> None:
    from app.infrastructure.files import save_upload

    class EmptyStream:
        async def read(self, size: int) -> bytes:
            return b""

    with pytest.raises(StorageError):
        await save_upload(EmptyStream(), "empty.txt", content_type="text/plain")

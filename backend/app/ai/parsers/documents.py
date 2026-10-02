"""Document parsers: Markdown, plain text and PDF.

Every parser returns the same ``ParsedDocument`` shape so the ingestion
pipeline (extract → normalise → chunk → embed) is format agnostic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import DocumentParseError, UnsupportedMediaTypeError
from app.core.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".pdf"}
MAX_PDF_PAGES = 500


@dataclass(slots=True)
class ParsedDocument:
    text: str
    title: str = ""
    page_count: int = 0
    language: str = "unknown"
    metadata: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _detect_language(text: str) -> str:
    if not text:
        return "unknown"
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if cjk > latin * 0.2:
        return "zh" if latin < cjk else "mixed"
    return "en" if latin else "unknown"


def normalise_text(text: str) -> str:
    """Normalise whitespace without destroying Markdown structure."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\ufeff", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    # Collapse runs of spaces that PDF extraction often introduces.
    text = re.sub(r"[ \t]{3,}", "  ", text)
    return text.strip()


class MarkdownParser:
    suffix = ".md"

    @staticmethod
    def parse(path: Path) -> ParsedDocument:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise DocumentParseError(f"Cannot read Markdown file: {exc}") from exc

        title = ""
        match = re.search(r"^#\s+(.+)$", raw, re.MULTILINE)
        if match:
            title = match.group(1).strip()
        headings = re.findall(r"^(#{1,6})\s+(.+)$", raw, re.MULTILINE)
        front_matter = re.match(r"^---\n(.*?)\n---\n", raw, re.DOTALL)
        meta: dict[str, object] = {"heading_count": len(headings)}
        if front_matter:
            meta["front_matter"] = front_matter.group(1)[:1000]
        text = normalise_text(raw)
        if not text:
            raise DocumentParseError("Markdown file is empty after normalisation")
        return ParsedDocument(
            text=text,
            title=title or path.stem,
            language=_detect_language(text),
            metadata=meta,
        )


class TextParser:
    suffix = ".txt"

    @staticmethod
    def parse(path: Path) -> ParsedDocument:
        raw = ""
        used_encoding = ""
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
            try:
                raw = path.read_text(encoding=encoding)
                used_encoding = encoding
                break
            except (UnicodeDecodeError, LookupError):
                continue
            except OSError as exc:
                raise DocumentParseError(f"Cannot read text file: {exc}") from exc
        if not raw:
            raise DocumentParseError("Text file is empty or uses an unsupported encoding")
        text = normalise_text(raw)
        if not text:
            raise DocumentParseError("Text file contains no content after normalisation")
        first_line = text.split("\n", 1)[0].strip()[:120]
        return ParsedDocument(
            text=text,
            title=first_line or path.stem,
            language=_detect_language(text),
            metadata={"encoding": used_encoding, "line_count": text.count("\n") + 1},
        )


class PdfParser:
    suffix = ".pdf"

    @staticmethod
    def parse(path: Path) -> ParsedDocument:
        try:
            from pypdf import PdfReader
            from pypdf.errors import PdfReadError
        except ImportError as exc:  # pragma: no cover
            raise DocumentParseError(
                "PDF support requires the 'pypdf' package (pip install pypdf)"
            ) from exc

        try:
            reader = PdfReader(str(path))
        except (PdfReadError, OSError, ValueError) as exc:
            raise DocumentParseError(
                f"PDF is corrupted or unreadable: {type(exc).__name__}: {exc}"
            ) from exc

        if reader.is_encrypted:
            try:
                # Try the common "empty password" case before giving up.
                if reader.decrypt("") == 0:
                    raise DocumentParseError("PDF is password protected and cannot be parsed")
            except Exception as exc:
                raise DocumentParseError("PDF is password protected and cannot be parsed") from exc

        page_count = len(reader.pages)
        warnings: list[str] = []
        if page_count > MAX_PDF_PAGES:
            warnings.append(
                f"PDF has {page_count} pages; only the first {MAX_PDF_PAGES} were parsed."
            )
            page_count = MAX_PDF_PAGES

        chunks: list[str] = []
        empty_pages = 0
        for index in range(page_count):
            try:
                page_text = reader.pages[index].extract_text() or ""
            except Exception as exc:
                logger.warning(
                    "pdf_page_extract_failed",
                    extra={"page": index + 1, "error": f"{type(exc).__name__}: {exc}"},
                )
                warnings.append(f"Page {index + 1} could not be extracted.")
                continue
            if not page_text.strip():
                empty_pages += 1
                continue
            chunks.append(f"[第 {index + 1} 页]\n{page_text.strip()}")

        text = normalise_text("\n\n".join(chunks))
        if not text:
            raise DocumentParseError(
                "No extractable text found. The PDF is likely a scanned image; "
                "OCR is out of scope for this project."
            )
        if empty_pages:
            warnings.append(f"{empty_pages} page(s) contained no extractable text.")

        meta = reader.metadata or {}
        title = (meta.get("/Title") or "").strip() if isinstance(meta.get("/Title"), str) else ""
        return ParsedDocument(
            text=text,
            title=title or path.stem,
            page_count=page_count,
            language=_detect_language(text),
            metadata={
                "pdf_pages": page_count,
                "pdf_producer": str(meta.get("/Producer") or "")[:200],
                "pdf_author": str(meta.get("/Author") or "")[:200],
            },
            warnings=warnings,
        )


_PARSERS = {
    ".md": MarkdownParser,
    ".markdown": MarkdownParser,
    ".txt": TextParser,
    ".pdf": PdfParser,
}


def get_parser(suffix: str):
    parser = _PARSERS.get(suffix.lower())
    if parser is None:
        raise UnsupportedMediaTypeError(
            f"Unsupported file type '{suffix}'. Supported: {sorted(_PARSERS)}",
            details={"supported": sorted(_PARSERS)},
        )
    return parser


def parse_file(path: Path) -> ParsedDocument:
    parser = get_parser(path.suffix)
    parsed = parser.parse(path)
    logger.info(
        "document_parsed",
        extra={
            "file": path.name,
            "suffix": path.suffix,
            "chars": len(parsed.text),
            "pages": parsed.page_count,
            "warnings": len(parsed.warnings),
        },
    )
    return parsed

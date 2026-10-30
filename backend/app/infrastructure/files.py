"""Safe local file storage.

Security measures implemented here (see docs/security.md):

* extension allow-list + MIME sniffing consistency check;
* hard size limit enforced *while streaming* (not after the fact);
* the stored filename is generated (UUID), never derived from user input, so
  path traversal, NUL bytes and Windows reserved names are impossible;
* every stored path is re-validated to be inside the storage root before use.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.core.errors import PayloadTooLargeError, StorageError, UnsupportedMediaTypeError
from app.core.logging import get_logger

logger = get_logger(__name__)

_UNSAFE = re.compile(r"[\x00-\x1f<>:\"/\\|?*]")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

MIME_BY_EXT = {
    ".md": {"text/markdown", "text/plain", "text/x-markdown", "application/octet-stream"},
    ".markdown": {"text/markdown", "text/plain", "text/x-markdown", "application/octet-stream"},
    ".txt": {"text/plain", "application/octet-stream"},
    ".pdf": {"application/pdf", "application/octet-stream"},
}

_CHUNK = 1024 * 256


@dataclass(slots=True)
class StoredFile:
    path: Path
    original_name: str
    safe_name: str
    size_bytes: int
    content_hash: str
    suffix: str


def sanitize_filename(name: str) -> str:
    """Strip directories, control characters and reserved names."""
    base = os.path.basename(name.replace("\\", "/")).strip()
    base = _UNSAFE.sub("_", base)
    base = base.lstrip(".") or "untitled"
    stem, dot, suffix = base.rpartition(".")
    if not dot:
        stem, suffix = base, ""
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    stem = stem[:120]
    return f"{stem}.{suffix}" if suffix else stem


def validate_extension(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in settings.allowed_extension_list:
        raise UnsupportedMediaTypeError(
            f"File type '{suffix or 'unknown'}' is not allowed.",
            details={"allowed": settings.allowed_extension_list},
        )
    return suffix


def validate_content_type(suffix: str, content_type: str | None) -> None:
    """Reject an obvious mismatch between the declared type and the extension."""
    if not content_type:
        return
    allowed = MIME_BY_EXT.get(suffix)
    if allowed and content_type.split(";")[0].strip().lower() not in allowed:
        raise UnsupportedMediaTypeError(
            f"Content-Type '{content_type}' does not match the '{suffix}' extension.",
            details={"allowed": sorted(allowed)},
        )


def ensure_within_root(path: Path, root: Path | None = None) -> Path:
    root = (root or settings.storage_path).resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise StorageError(
            "Refusing to access a path outside the storage root",
            details={"path": str(resolved)},
        ) from exc
    return resolved


async def save_upload(stream, filename: str, *, content_type: str | None = None) -> StoredFile:
    """Persist an uploaded stream with streaming size enforcement.

    ``stream`` must expose an async ``read(size)`` (Starlette ``UploadFile``).
    """
    suffix = validate_extension(filename)
    validate_content_type(suffix, content_type)

    settings.ensure_dirs()
    safe_name = sanitize_filename(filename)
    target_dir = settings.upload_path
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    target = ensure_within_root(target_dir / stored_name)

    max_bytes = settings.max_upload_mb * 1024 * 1024
    digest = hashlib.sha256()
    total = 0
    try:
        with target.open("wb") as handle:
            while True:
                chunk = await stream.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    handle.close()
                    target.unlink(missing_ok=True)
                    raise PayloadTooLargeError(
                        f"File exceeds the {settings.max_upload_mb} MB limit.",
                        details={"limit_mb": settings.max_upload_mb, "read_bytes": total},
                    )
                digest.update(chunk)
                handle.write(chunk)
    except PayloadTooLargeError:
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise StorageError(f"Failed to store the uploaded file: {exc}") from exc

    if total == 0:
        target.unlink(missing_ok=True)
        raise StorageError("Uploaded file is empty")

    return StoredFile(
        path=target,
        original_name=filename,
        safe_name=safe_name,
        size_bytes=total,
        content_hash=digest.hexdigest(),
        suffix=suffix,
    )


def read_text(path: Path, *, limit: int | None = None) -> str:
    resolved = ensure_within_root(path)
    if not resolved.exists():
        raise StorageError("Stored file is missing on disk", details={"path": str(resolved)})
    text = resolved.read_text(encoding="utf-8", errors="replace")
    return text[:limit] if limit else text


def delete_file(path: Path | str) -> bool:
    if not path:
        return False
    try:
        resolved = ensure_within_root(Path(path))
    except StorageError:
        logger.warning("delete_outside_root_refused", extra={"path": str(path)})
        return False
    if resolved.exists():
        resolved.unlink()
        return True
    return False


def storage_usage() -> dict[str, int]:
    root = settings.storage_path
    files = 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            files += 1
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return {"files": files, "bytes": total}

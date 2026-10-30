"""Structured logging with per-request correlation ids.

Every log line carries ``service`` and, when available, ``request_id`` /
``run_id`` so a single user action can be traced across API, service, AI and
worker layers.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

from app.core.config import settings

# ---------------------------------------------------------------------------
# Correlation context (contextvars are safe across asyncio tasks)
# ---------------------------------------------------------------------------
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
run_id_var: ContextVar[str] = ContextVar("run_id", default="-")

_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
    "asctime",
}

_SENSITIVE_KEYS = ("api_key", "apikey", "authorization", "password", "secret", "token")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:20]}"


def mask_secret(value: str | None) -> str:
    """Return a redacted representation of a secret (never log raw keys)."""
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]} (len={len(value)})"


def _sanitize(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        lowered = key.lower()
        if any(s in lowered for s in _SENSITIVE_KEYS):
            out[key] = "<redacted>"
        elif isinstance(value, str) and len(value) > 500:
            out[key] = value[:500] + f"...<truncated {len(value)} chars>"
        else:
            out[key] = value
    return out


class JsonFormatter(logging.Formatter):
    """Machine-readable single-line JSON logs."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
            + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "run_id": run_id_var.get(),
            "message": record.getMessage(),
        }
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        if extras:
            payload["extra"] = _sanitize(extras)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-friendly coloured-ish console output for local development."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        rid = request_id_var.get()
        run = run_id_var.get()
        corr = ""
        if rid != "-":
            corr += f" req={rid}"
        if run != "-":
            corr += f" run={run}"
        base = (
            f"{ts} {record.levelname:<7} [{self.service}] {record.name}: "
            f"{record.getMessage()}{corr}"
        )
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        if extras:
            base += " | " + " ".join(f"{k}={v}" for k, v in _sanitize(extras).items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


_configured = False


def _safe_stream(stream: object) -> object:
    """A stream that cannot fail on a character.

    **This is where `UnicodeEncodeError` came from.** On Windows `sys.stdout` is encoded
    with the console's ANSI code page, and every log line this application writes is in
    Chinese — so the first record with a non-ASCII character raised out of the handler.
    A logging handler that raises is worse than one that prints badly: the message is lost
    and the traceback names a codec instead of the thing that went wrong.

    A packaged GUI build has no console at all, so the stream is `None` and the record goes
    nowhere; that is the operating system's decision and not something to crash over.
    """
    if stream is None:
        return None
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            # `backslashreplace` rather than `replace`: a character the code page cannot
            # carry is written as an escape, which is readable and reversible, instead of
            # a row of question marks that hides which character it was.
            reconfigure(encoding="utf-8", errors="backslashreplace")
            return stream
        except (ValueError, OSError):
            pass
    return stream


def configure_logging(service: str = "api") -> None:
    """Idempotently configure the root logger."""
    global _configured
    if _configured:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(_safe_stream(sys.stdout))
    handler.setFormatter(JsonFormatter(service) if settings.log_json else ConsoleFormatter(service))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quiet noisy third-party loggers. They are only verbose when the operator
    # explicitly asks for DEBUG level via LOG_LEVEL.
    third_party_level = logging.DEBUG if settings.log_level.upper() == "DEBUG" else logging.WARNING
    for noisy in ("httpx", "httpcore", "urllib3", "qdrant_client", "asyncio", "multipart"):
        logging.getLogger(noisy).setLevel(third_party_level)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# Defensive hardening
# ---------------------------------------------------------------------------
# ``logging`` raises ``KeyError: Attempt to overwrite 'X' in LogRecord`` when a
# caller passes a reserved attribute name through ``extra=``.  Because that
# exception is raised *inside* the logging call, a single careless ``extra``
# key can turn a well-handled 4xx into a 500 — which is exactly what happened
# during development (an exception handler logging ``message=``).
#
# Rather than relying on every call site staying disciplined, we drop colliding
# keys centrally. The event still logs; only the duplicate key is discarded.
_original_make_record = logging.Logger.makeRecord


def _safe_make_record(
    self: logging.Logger,
    name: str,
    level: int,
    fn: str,
    lno: int,
    msg: object,
    args: tuple,
    exc_info,
    func: str | None = None,
    extra: dict[str, Any] | None = None,
    sinfo: str | None = None,
) -> logging.LogRecord:
    if extra:
        conflicts = [k for k in extra if k in _RESERVED]
        if conflicts:
            extra = {k: v for k, v in extra.items() if k not in _RESERVED}
            msg = f"{msg} [dropped reserved log keys: {','.join(sorted(conflicts))}]"
    return _original_make_record(
        self, name, level, fn, lno, msg, args, exc_info, func, extra, sinfo
    )


logging.Logger.makeRecord = _safe_make_record  # type: ignore[method-assign]

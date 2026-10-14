"""Persisting the tool audit trail.

The executor calls an async sink for every invocation. This module provides the
real one, writing a ``tool_audit_log`` row.

Two decisions are deliberate:

* **Refusals are recorded too.** A log that only contains what ran cannot answer
  "what did it try to do, and what did we stop?" — which is the question an audit
  trail exists to answer.
* **Writing never breaks a run.** The sink is wrapped by the executor and again
  here: an audit failure is logged and swallowed. Losing a log line is bad;
  failing a user's task because the log write failed is worse.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.ai import ToolAuditLog

logger = get_logger(__name__)


#: Short run id -> database UUID, for the lifetime of the process.
#:
#: The agent works with the recorder's short run id (`run_...`), which addresses a
#: live controller; the audit row needs the `ai_runs` primary key. Converting the
#: short id with `uuid.UUID()` fails, and the failure is silent: the column is
#: nullable, so every audit row was written with `ai_run_id = NULL`.
#:
#: That mattered downstream — the BuildRun collector looked up audit rows by run id,
#: found none, and reported "no output detected" for a run that had plainly produced
#: a file. Cached because the mapping never changes and a lookup per tool call would
#: be a query per tool call.
_RUN_ID_CACHE: dict[str, uuid.UUID] = {}


async def resolve_run_uuid(session: AsyncSession, run_id: str | None) -> uuid.UUID | None:
    """Turn whatever run id we were handed into the `ai_runs` primary key.

    Accepts a UUID string as-is, and looks up a short run id. Returns None rather
    than raising: an audit row without a run id is still worth keeping.
    """
    if not run_id:
        return None

    text = str(run_id)
    try:
        return uuid.UUID(text)
    except (ValueError, AttributeError, TypeError):
        pass

    cached = _RUN_ID_CACHE.get(text)
    if cached is not None:
        return cached

    try:
        from sqlalchemy import select

        from app.models.ai import AIRun

        row = (
            await session.execute(select(AIRun.id).where(AIRun.run_id == text))
        ).scalar_one_or_none()
        if row is not None:
            _RUN_ID_CACHE[text] = row
        return row
    except Exception as exc:
        logger.warning("audit_run_id_lookup_failed", extra={"run_id": text, "error": str(exc)})
        return None


def make_audit_sink(session: AsyncSession):
    """Build the sink the executor calls after each tool invocation.

    The session is the run's own session, so audit rows commit with the run
    instead of needing a second connection that could deadlock against it.
    """

    async def sink(
        *,
        run_id: str | None,
        step_index: int,
        tool_name: str,
        category: str,
        decision: str,
        reason: str,
        risk_level: str,
        permission_level: int,
        target: str,
        arguments: dict[str, Any],
        status: str,
        exit_code: int | None,
        output_bytes: int,
        truncated: bool,
        error_message: str,
        latency_ms: int,
        confirmed_by_user: bool | None,
    ) -> None:
        # Resolved, not parsed. The agent hands us a short run id, and parsing it
        # as a UUID silently produced NULL on every row.
        run_uuid = await resolve_run_uuid(session, run_id)

        row = ToolAuditLog(
            ai_run_id=run_uuid,
            step_index=step_index,
            tool_name=tool_name[:64],
            category=category[:32],
            decision=decision,
            reason=reason[:2000],
            risk_level=risk_level,
            permission_level=permission_level,
            target=(target or "")[:2000],
            arguments=_safe_arguments(arguments),
            status=status,
            exit_code=exit_code,
            output_bytes=output_bytes,
            truncated=truncated,
            error_message=(error_message or "")[:2000],
            latency_ms=latency_ms,
            confirmed_by_user=confirmed_by_user,
        )
        session.add(row)
        await session.flush()

    return sink


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep the audit row small and free of anything enormous.

    A ``file_write`` carries the whole file body. Storing that verbatim would put
    copies of user source files in the audit table, which is both a privacy
    problem and a size problem. The hash is kept instead — it still answers "did
    the content change between these two calls?".
    """
    import hashlib

    out: dict[str, Any] = {}
    for key, value in (arguments or {}).items():
        if isinstance(value, str) and len(value) > 500:
            digest = hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16]
            out[key] = f"<{len(value)} chars, sha256:{digest}>"
        elif isinstance(value, (dict, list)):
            rendered = str(value)
            if len(rendered) > 500:
                out[key] = f"<{len(rendered)} chars>"
            else:
                out[key] = value
        else:
            out[key] = value
    return out


__all__ = ["make_audit_sink"]

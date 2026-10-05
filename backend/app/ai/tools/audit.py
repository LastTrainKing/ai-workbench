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
        run_uuid: uuid.UUID | None = None
        if run_id:
            try:
                run_uuid = uuid.UUID(str(run_id))
            except (ValueError, AttributeError, TypeError):
                run_uuid = None

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

"""The gate every computer-tool call passes through.

Nothing reaches the operating system without going through :class:`ToolExecutor`.
It is the only place that combines the five things a safe agent needs, and it does
them in a fixed order:

    1. validate the arguments against the tool's declared schema
    2. ask the PermissionManager (deny beats allow; risk decides the prompt)
    3. obtain user confirmation when the verdict requires it
    4. run under a hard timeout, with the working directory enforced
    5. write an audit row — including for the calls that were refused

Steps 2 and 5 are what make this different from a shell wrapper. A model that
invents a path outside the workspace is stopped at step 2, and a model that tries
something it is not allowed to do leaves a record at step 5.

Failure classification lives here too, because the retry policy needs to tell a
transient network blip apart from a permission refusal: the first is worth
retrying, the second never is.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.ai.tools.agent_scope import agent_tool_scope
from app.ai.tools.permissions import (
    AgentPolicy,
    Decision,
    PermissionManager,
    PermissionVerdict,
    RiskLevel,
)
from app.core.errors import AppError, PermissionDeniedError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Failure classification
# --------------------------------------------------------------------------- #
class FailureKind(StrEnum):
    """Why something failed, which decides whether retrying could help."""

    TRANSIENT = "transient"  # network blip, lock contention — retry
    PERMISSION = "permission"  # refused by policy — never retry
    VALIDATION = "validation"  # bad arguments — never retry
    TOOL = "tool"  # the tool itself failed — retry once at most
    NETWORK = "network"  # DNS, TLS, timeout — retry with backoff
    MODEL = "model"  # the model produced something unusable — retry
    TIMEOUT = "timeout"  # exceeded its budget — retry once
    CANCELLED = "cancelled"  # the user stopped it — never retry
    FATAL = "fatal"  # cannot continue — never retry
    CONFIRMATION = "confirmation"  # waiting for a human — never auto-retry

    @property
    def retryable(self) -> bool:
        return self in {
            FailureKind.TRANSIENT,
            FailureKind.TOOL,
            FailureKind.NETWORK,
            FailureKind.MODEL,
            FailureKind.TIMEOUT,
        }


_TRANSIENT_HINTS = (
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "deadlock",
    "lock wait timeout",
    "too many connections",
    "temporarily",
    "try again",
)
_NETWORK_HINTS = (
    "getaddrinfo",
    "name or service not known",
    "nodename nor servname",
    "ssl",
    "certificate",
    "connection aborted",
    "network is unreachable",
    "timed out",
)


def classify_failure(exc: BaseException | str) -> FailureKind:
    """Map an exception or message onto a retry decision."""
    if isinstance(exc, asyncio.CancelledError):
        return FailureKind.CANCELLED
    if isinstance(exc, TimeoutError):
        return FailureKind.TIMEOUT
    if isinstance(exc, ValidationError):
        return FailureKind.VALIDATION
    if isinstance(exc, PermissionError):
        return FailureKind.PERMISSION
    if isinstance(exc, AppError):
        code = getattr(exc, "code", "") or ""
        if code.startswith("ai_"):
            return FailureKind.MODEL
        if code in {"not_found", "conflict"}:
            return FailureKind.FATAL

    text = str(exc).lower()
    if any(hint in text for hint in _NETWORK_HINTS):
        return FailureKind.NETWORK
    if any(hint in text for hint in _TRANSIENT_HINTS):
        return FailureKind.TRANSIENT
    return FailureKind.TOOL


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ToolOutcome:
    """What happened, in the shape both the agent loop and the UI need."""

    ok: bool
    data: Any = None
    error: str = ""
    summary: str = ""
    latency_ms: int = 0
    #: SUCCEEDED | FAILED | DENIED | DECLINED | TIMEOUT | CANCELLED
    status: str = "SUCCEEDED"
    failure: FailureKind | None = None
    verdict: PermissionVerdict | None = None
    exit_code: int | None = None
    truncated: bool = False
    output_bytes: int = 0

    @property
    def retryable(self) -> bool:
        return bool(self.failure and self.failure.retryable)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "summary": self.summary,
            "latency_ms": self.latency_ms,
            "status": self.status,
            "failure": self.failure.value if self.failure else None,
            "exit_code": self.exit_code,
            "truncated": self.truncated,
            "output_bytes": self.output_bytes,
        }


@dataclass(slots=True)
class ConfirmationRequest:
    """What the user is being asked to approve."""

    tool: str
    arguments: dict[str, Any]
    verdict: PermissionVerdict
    run_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "run_id": self.run_id,
            "operation": self.verdict.operation or self.tool,
            "target": self.verdict.target,
            "reason": self.verdict.reason,
            "risk": self.verdict.risk.value,
            "impact": self.verdict.impact,
            "detail": self.verdict.detail,
        }


#: Returns True to proceed. Raising or returning False declines.
ConfirmHandler = Callable[[ConfirmationRequest], Awaitable[bool]]


@dataclass(slots=True)
class ResourceLimits:
    """Ceilings a single run must respect."""

    max_output_bytes: int = 200_000
    max_files_modified: int = 50
    files_modified: int = 0
    tool_calls: int = 0
    max_tool_calls: int = 100
    started_at: float = field(default_factory=time.monotonic)
    max_runtime_seconds: int = 1800

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def exhausted(self) -> str | None:
        if self.tool_calls >= self.max_tool_calls:
            return f"reached the tool-call limit ({self.max_tool_calls})"
        if self.elapsed() >= self.max_runtime_seconds:
            return f"reached the runtime limit ({self.max_runtime_seconds}s)"
        if self.files_modified >= self.max_files_modified:
            return f"reached the file-modification limit ({self.max_files_modified})"
        return None


# --------------------------------------------------------------------------- #
# Executor
# --------------------------------------------------------------------------- #
def _denied_verdict(verdict: PermissionVerdict, exc: BaseException) -> PermissionVerdict:
    """Rewrite the verdict so the audit row reflects the refusal that happened.

    The tool passed the coarse check (its *kind* is permitted) and then refused a
    specific argument. Carrying the original ALLOW verdict into the audit would
    record the opposite of what occurred.
    """
    details = getattr(exc, "details", None) or {}
    return PermissionVerdict(
        decision=Decision.DENY,
        reason=str(exc) or "Refused by policy",
        risk=RiskLevel(details.get("risk", verdict.risk.value))
        if details.get("risk") in {r.value for r in RiskLevel}
        else verdict.risk,
        detail=str(details.get("detail", "")),
        operation=verdict.operation,
        target=str(details.get("path", verdict.target)),
        impact="The policy refuses this specific target.",
    )


def _derive_target(tool: str, arguments: dict[str, Any], verdict_target: str) -> str:
    """Work out what the call actually acted on.

    The permission verdict only carries a target when the *policy* named one. For
    an allowed call the policy often has nothing specific to say, which would
    leave the audit row unable to answer the question it exists for: which file,
    which command, which host?

    So the target is taken from the arguments, preferring the field that names the
    thing acted on. Path-like keys come first because the file tools are the ones
    where "which file?" matters most.
    """
    if verdict_target:
        return verdict_target

    for key in ("path", "source", "destination", "file", "url", "command", "root", "target", "pid", "port"):
        value = arguments.get(key)
        if value not in (None, ""):
            return str(value)

    # No obviously-named argument: fall back to a short rendering of whatever was
    # passed, which is still better than an empty cell.
    if arguments:
        rendered = ", ".join(f"{k}={v}" for k, v in list(arguments.items())[:3])
        return rendered[:500]
    return ""


class ToolExecutor:
    """Runs tools. The agent never calls a tool any other way."""

    def __init__(self, policy: AgentPolicy, *, audit: Any | None = None) -> None:
        self.policy = policy
        self.permissions = PermissionManager(policy)
        self._audit = audit

    # ---------------------------------------------------------------- audit --
    async def _record(
        self,
        *,
        tool: str,
        category: str,
        arguments: dict[str, Any],
        outcome: ToolOutcome,
        run_id: str | None,
        step_index: int,
        permission_level: int,
        confirmed: bool | None,
    ) -> None:
        """Persist one audit row. Never raises: auditing must not break a run."""
        if self._audit is None:
            return
        verdict = outcome.verdict
        try:
            await self._audit(
                run_id=run_id,
                step_index=step_index,
                tool_name=tool,
                category=category,
                decision=verdict.decision.value if verdict else "allow",
                reason=verdict.reason if verdict else "",
                risk_level=(verdict.risk.value if verdict else RiskLevel.LOW.value),
                permission_level=permission_level,
                target=_derive_target(tool, arguments, verdict.target if verdict else ""),
                arguments=arguments,
                status=outcome.status,
                exit_code=outcome.exit_code,
                output_bytes=outcome.output_bytes,
                truncated=outcome.truncated,
                error_message=outcome.error,
                latency_ms=outcome.latency_ms,
                confirmed_by_user=confirmed,
            )
        except Exception as exc:
            logger.warning(
                "tool_audit_write_failed",
                extra={"tool": tool, "error": f"{type(exc).__name__}: {exc}"},
            )

    # -------------------------------------------------------------- execute --
    async def execute(
        self,
        tool: Any,
        ctx: Any,
        arguments: dict[str, Any],
        *,
        run_id: str | None = None,
        step_index: int = 0,
        confirm: ConfirmHandler | None = None,
        limits: ResourceLimits | None = None,
    ) -> ToolOutcome:
        spec = tool.spec
        started = time.perf_counter()

        def finish(outcome: ToolOutcome, confirmed: bool | None = None) -> ToolOutcome:
            outcome.latency_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        # --- 0. resource ceiling -------------------------------------------
        if limits is not None:
            exhausted = limits.exhausted()
            if exhausted:
                outcome = ToolOutcome(
                    ok=False,
                    status="FAILED",
                    error=f"The run has {exhausted}",
                    failure=FailureKind.FATAL,
                    summary=f"stopped: {exhausted}",
                )
                await self._record(
                    tool=spec.name,
                    category=spec.category,
                    arguments=arguments,
                    outcome=outcome,
                    run_id=run_id,
                    step_index=step_index,
                    permission_level=spec.permission_level,
                    confirmed=None,
                )
                return finish(outcome)

        # --- 1. validate ---------------------------------------------------
        try:
            cleaned = tool.validate(arguments)
        except Exception as exc:
            outcome = ToolOutcome(
                ok=False,
                status="FAILED",
                error=str(exc),
                failure=FailureKind.VALIDATION,
                summary=f"invalid arguments for {spec.name}",
            )
            await self._record(
                tool=spec.name,
                category=spec.category,
                arguments=arguments,
                outcome=outcome,
                run_id=run_id,
                step_index=step_index,
                permission_level=spec.permission_level,
                confirmed=None,
            )
            return finish(outcome)

        # --- 2. permission -------------------------------------------------
        verdict = self.permissions.check_tool(spec, cleaned)
        if verdict.decision is Decision.DENY:
            outcome = ToolOutcome(
                ok=False,
                status="DENIED",
                error=verdict.reason,
                failure=FailureKind.PERMISSION,
                verdict=verdict,
                summary=f"denied: {verdict.reason}",
            )
            await self._record(
                tool=spec.name,
                category=spec.category,
                arguments=cleaned,
                outcome=outcome,
                run_id=run_id,
                step_index=step_index,
                permission_level=spec.permission_level,
                confirmed=None,
            )
            return finish(outcome)

        # --- 3. confirmation ------------------------------------------------
        confirmed: bool | None = None
        if verdict.decision is Decision.CONFIRM:
            if confirm is None:
                outcome = ToolOutcome(
                    ok=False,
                    status="DENIED",
                    error=f"'{spec.name}' needs confirmation, which is not available in this context",
                    failure=FailureKind.PERMISSION,
                    verdict=verdict,
                    summary="confirmation unavailable",
                )
                await self._record(
                    tool=spec.name,
                    category=spec.category,
                    arguments=cleaned,
                    outcome=outcome,
                    run_id=run_id,
                    step_index=step_index,
                    permission_level=spec.permission_level,
                    confirmed=None,
                )
                return finish(outcome)

            request = ConfirmationRequest(
                tool=spec.name, arguments=cleaned, verdict=verdict, run_id=run_id or ""
            )
            try:
                approved = await asyncio.wait_for(
                    confirm(request),
                    timeout=self.policy.confirmation_timeout_seconds,
                )
            except TimeoutError:
                outcome = ToolOutcome(
                    ok=False,
                    status="TIMEOUT",
                    error="No response to the confirmation request",
                    failure=FailureKind.CONFIRMATION,
                    verdict=verdict,
                    summary="confirmation timed out",
                )
                await self._record(
                    tool=spec.name,
                    category=spec.category,
                    arguments=cleaned,
                    outcome=outcome,
                    run_id=run_id,
                    step_index=step_index,
                    permission_level=spec.permission_level,
                    confirmed=None,
                )
                return finish(outcome)

            confirmed = bool(approved)
            if not approved:
                outcome = ToolOutcome(
                    ok=False,
                    status="DECLINED",
                    error="The user declined this operation",
                    failure=FailureKind.PERMISSION,
                    verdict=verdict,
                    summary=f"declined: {verdict.operation or spec.name}",
                )
                await self._record(
                    tool=spec.name,
                    category=spec.category,
                    arguments=cleaned,
                    outcome=outcome,
                    run_id=run_id,
                    step_index=step_index,
                    permission_level=spec.permission_level,
                    confirmed=False,
                )
                return finish(outcome)

        # --- 4. run ---------------------------------------------------------
        timeout = float(getattr(spec, "timeout_seconds", 30.0) or 30.0)
        # Tell the context whether a question was put to someone and answered.
        #
        # The two permission checks ask about different things — this one about the tool, the
        # tool's own about the concrete path — and a path verdict of "needs confirmation"
        # used to be raised as a refusal *after* the user had already approved it. At Observe
        # level that meant: asked, approved, nothing happened. `require_permission` honours
        # the verdict only when this flag says the prompt really happened.
        had_flag = hasattr(ctx, "approved")
        previous_approved = getattr(ctx, "approved", False)
        if had_flag:
            ctx.approved = bool(confirmed)
        try:
            # Everything the agent does to the machine happens inside this block, and this
            # is the only place a tool is called — the class says so in its own docstring.
            # The flag it sets is what `policy_store` reads before refusing to let the
            # agent change the policy that governs it. See `agent_scope.py`.
            with agent_tool_scope(spec.name):
                result = await asyncio.wait_for(tool.execute(ctx, **cleaned), timeout=timeout)
        except TimeoutError:
            outcome = ToolOutcome(
                ok=False,
                status="TIMEOUT",
                error=f"'{spec.name}' exceeded its {timeout:g}s budget",
                failure=FailureKind.TIMEOUT,
                verdict=verdict,
                summary=f"timeout after {timeout:g}s",
            )
        except asyncio.CancelledError:
            outcome = ToolOutcome(
                ok=False,
                status="CANCELLED",
                error="Cancelled",
                failure=FailureKind.CANCELLED,
                verdict=verdict,
                summary="cancelled",
            )
            await self._record(
                tool=spec.name,
                category=spec.category,
                arguments=cleaned,
                outcome=outcome,
                run_id=run_id,
                step_index=step_index,
                permission_level=spec.permission_level,
                confirmed=confirmed,
            )
            raise
        except Exception as exc:
            kind = classify_failure(exc)
            # A tool can refuse a *concrete argument* even when the tool itself is
            # permitted: `file_read` is allowed, but the path it was handed may be
            # on the denied list. That refusal must be recorded as a denial, not
            # as a failure of an allowed call — an audit trail that reports
            # "allow" for something it blocked is worse than no trail at all.
            denied_in_tool = isinstance(exc, PermissionDeniedError)
            outcome = ToolOutcome(
                ok=False,
                status="DENIED" if denied_in_tool else "FAILED",
                error=f"{type(exc).__name__}: {exc}",
                failure=FailureKind.PERMISSION if denied_in_tool else kind,
                verdict=_denied_verdict(verdict, exc) if denied_in_tool else verdict,
                summary=f"{spec.name} {'denied' if denied_in_tool else 'failed'}",
            )
        else:
            payload, truncated, size = _cap_payload(
                result.data, self.policy.max_output_bytes
            )
            outcome = ToolOutcome(
                ok=bool(result.ok),
                data=payload,
                error=result.error,
                summary=result.summary or (f"{spec.name} ok" if result.ok else f"{spec.name} failed"),
                status="SUCCEEDED" if result.ok else "FAILED",
                failure=None if result.ok else classify_failure(result.error or "tool failed"),
                verdict=verdict,
                exit_code=getattr(result, "exit_code", None),
                truncated=truncated,
                output_bytes=size,
            )
        finally:
            # Restored on every path, including the one that re-raises a cancellation. The
            # flag describes one call, and a stale `True` would let the *next* call through
            # a prompt it never had.
            if had_flag:
                ctx.approved = previous_approved

        if limits is not None:
            limits.tool_calls += 1

        await self._record(
            tool=spec.name,
            category=spec.category,
            arguments=cleaned,
            outcome=outcome,
            run_id=run_id,
            step_index=step_index,
            permission_level=spec.permission_level,
            confirmed=confirmed,
        )
        return finish(outcome)


class _OutputBudget:
    """How many bytes are left for this tool result.

    The old `_cap_payload` passed the **full** limit to every child, so a dict with fifty
    keys produced fifty strings of up to `limit` bytes each and a payload fifty times the
    ceiling. The number it returned was then wrong too, because it summed the children's
    claimed sizes rather than the bytes actually kept.

    A budget object rather than a running integer because the recursion needs to *spend*,
    not to receive: every leaf draws from the same pot, and when the pot is empty every
    remaining leaf is dropped rather than truncated to nothing.
    """

    __slots__ = ("limit", "truncated", "used")

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0
        self.truncated = False

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def take(self, size: int) -> None:
        self.used += size


def _cap_payload(data: Any, limit: int) -> tuple[Any, bool, int]:
    """Keep a tool result inside the output ceiling — the whole result, not each field.

    A command that prints a gigabyte must not end up in the database, in the prompt, or in
    the UI. The ceiling is a property of the **result**: one budget, spent by every string
    inside it, so `{"a": "...", "b": "..."}` cannot total twice the limit.

    ## Why there is a loop at the end

    The budget counts bytes of content, of keys, and of JSON punctuation — but it is a
    *model* of what `json.dumps` will produce, and a model of a serializer is never exact.
    The first three versions of this function each came in a few bytes over: quotes and
    commas per field, then the container's own brackets, then multi-byte characters
    counted as one each. Each fix narrowed the gap and none of them closed it.

    So the guarantee is not made by the model. The result is serialised, measured, and if
    it is still over, the budget is reduced by the overshoot and the walk runs again. Two
    or three passes converge. **The number this returns is the measured size**, not the
    budget's opinion of it.
    """
    if limit <= 0:
        return data, False, 0

    budget = _OutputBudget(limit)
    capped = _cap_value(data, budget)
    size = _measure(capped)

    for _ in range(4):
        if size <= limit:
            break
        # Take off the overshoot, plus a margin for the marker text the next pass will add.
        budget = _OutputBudget(max(1, limit - (size - limit) - 64))
        capped = _cap_value(data, budget)
        size = _measure(capped)

    return capped, budget.truncated or size > limit, size


def _measure(value: Any) -> int:
    """The bytes this value actually costs once serialised."""
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8", errors="ignore"))
    except (TypeError, ValueError):
        return len(str(value).encode("utf-8", errors="ignore"))


def _cap_value(data: Any, budget: _OutputBudget) -> Any:
    """Walk one value, spending from the shared budget.

    **The marker text is part of the budget too.** The first version of this counted only
    the content it kept, so fifty truncated fields each added an eighty-byte "... omitted
    ..." note on top of the ceiling and the result came out four times over. A limit that
    does not include the words explaining the limit is not a limit.
    """
    if isinstance(data, str):
        size = len(data.encode("utf-8", errors="ignore"))
        if size <= budget.remaining:
            budget.take(size)
            return data

        # Build the marker first, then keep whatever is left after paying for it.
        marker = (
            f"\n\n... [{size} bytes omitted: the whole result is capped at "
            f"{budget.limit} bytes] ...\n\n"
        )
        available = budget.remaining - len(marker.encode("utf-8", errors="ignore"))
        budget.truncated = True
        if available < 64:
            # The budget was spent by earlier fields. Return nothing rather than a marker
            # nobody has room for — an empty string costs two bytes and does not lie.
            budget.take(2)
            return ""

        # **Slice bytes, not characters.** `data[:keep]` counts characters while the
        # budget counts UTF-8 bytes, so five thousand Chinese characters — fifteen
        # thousand bytes — were budgeted as five thousand and came out at 5,853. The
        # string is cut on a byte boundary and decoded with `errors="ignore"` so a
        # multi-byte character split down the middle is dropped rather than mangled.
        raw = data.encode("utf-8", errors="ignore")
        half = available // 2
        head = raw[:half].decode("utf-8", errors="ignore")
        tail = raw[-half:].decode("utf-8", errors="ignore") if len(raw) > half else ""
        body = f"{head}{marker}{tail}"
        budget.take(len(body.encode("utf-8", errors="ignore")))
        return body

    if isinstance(data, dict):
        out: dict[str, Any] = {}
        budget.take(2)  # this container's own braces
        for key, value in data.items():
            # **JSON's own punctuation is part of the size.** `{"a":"b"}` is eight bytes
            # for two bytes of content: two quotes, a colon, a comma and two braces per
            # field. A thousand-element array is three kilobytes of structure alone, and
            # the first version of this budget counted none of it — the result was under
            # budget by its own accounting and over budget on the wire.
            cost = len(str(key).encode("utf-8", errors="ignore")) + 4
            if cost > budget.remaining:
                # Stop adding fields. Emitting an empty string for each remaining key
                # would still cost its quotes and comma — a thousand keys is three
                # kilobytes of punctuation, which is how "capped at 2 KB" produced 5.9 KB.
                budget.truncated = True
                break
            budget.take(cost)
            out[key] = _cap_value(value, budget)
        return out

    if isinstance(data, list):
        out_list: list[Any] = []
        budget.take(2)  # this container's own brackets
        for value in data:
            if budget.remaining < 3:  # the comma, the quotes, and something to put in them
                budget.truncated = True
                break
            budget.take(2)
            out_list.append(_cap_value(value, budget))
        return out_list

    if data is None:
        return None

    size = len(str(data).encode("utf-8", errors="ignore"))
    budget.take(size)
    return data


__all__ = [
    "ConfirmHandler",
    "ConfirmationRequest",
    "FailureKind",
    "ResourceLimits",
    "ToolExecutor",
    "ToolOutcome",
    "classify_failure",
]

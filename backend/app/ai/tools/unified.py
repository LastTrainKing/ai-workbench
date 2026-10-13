"""One catalogue for the agent, two execution paths underneath.

The agent should not have to know that some of its tools query the database and
others run PowerShell. This module presents a single flat tool list and routes
each call to the right machinery:

* **business tools** (knowledge search, tasks, notes, planner) go through the
  existing ``ToolRegistry``, unchanged — Service -> Repository -> Database;
* **computer tools** (files, shell, git, processes, network) go through
  ``ToolExecutor``, which is what enforces the permission policy, the workspace
  sandbox, the timeout, the output cap and the audit row.

The routing is by membership, not by a flag the model could influence, so a call
cannot be steered onto the less-guarded path.

Two consequences worth being explicit about:

* the agent still cannot reach the database directly — the business path is
  untouched, and no computer tool opens a session;
* a computer tool that is disabled by policy is absent from ``specs()``, so the
  model is never shown an action it is not allowed to take.
"""

from __future__ import annotations

from typing import Any

from app.ai.agent.tools import ToolContext, ToolRegistry
from app.ai.tools.executor import ConfirmHandler, ResourceLimits, ToolExecutor, ToolOutcome
from app.ai.tools.registry import ComputerToolRegistry
from app.ai.types import ToolResult
from app.core.logging import get_logger

logger = get_logger(__name__)


def _to_tool_result(outcome: ToolOutcome) -> ToolResult:
    """Adapt a computer-tool outcome to the shape the agent loop already speaks.

    The status is folded into ``error`` rather than dropped: "the user declined
    this" and "the command failed" need different follow-up from the model, and a
    bare ``ok=False`` would lose that distinction.
    """
    if outcome.ok:
        return ToolResult(
            ok=True,
            data=outcome.data,
            summary=outcome.summary,
            latency_ms=outcome.latency_ms,
            exit_code=outcome.exit_code,
        )

    detail = outcome.error or outcome.summary or "the tool failed"
    if outcome.status in {"DENIED", "DECLINED"}:
        detail = f"{detail} (permission: {outcome.status.lower()})"
    elif outcome.status == "TIMEOUT":
        detail = f"{detail} (timed out)"

    return ToolResult(
        ok=False,
        data=outcome.data,
        error=detail,
        summary=outcome.summary,
        latency_ms=outcome.latency_ms,
        exit_code=outcome.exit_code,
    )


class UnifiedToolRegistry:
    """The registry the agent actually sees."""

    def __init__(
        self,
        business: ToolRegistry,
        computer: ComputerToolRegistry | None = None,
        executor: ToolExecutor | None = None,
        *,
        confirm: ConfirmHandler | None = None,
        limits: ResourceLimits | None = None,
        allowed: frozenset[str] | None = None,
    ) -> None:
        self.business = business
        self.computer = computer
        self.executor = executor
        self._confirm = confirm
        self.limits = limits
        #: What the model is offered, when an employee's contract names a subset.
        #:
        #: This is *not* the security boundary — `PermissionManager.check_tool` is, and it
        #: runs before every execution regardless of what was offered. This is the other
        #: half: an employee whose contract says "19 tools" should not be looking at 55
        #: and spending context on the 36 it may not use.
        self._allowed = allowed

    # -------------------------------------------------------------- catalogue --
    @property
    def has_computer_tools(self) -> bool:
        return bool(self.computer and self.computer.names())

    def names(self) -> list[str]:
        names = list(self.business.names()) if hasattr(self.business, "names") else []
        if self.computer:
            names += self.computer.names()
        return sorted(self._filter(set(names)))

    def _filter(self, names: set[str]) -> set[str]:
        if self._allowed is None:
            return names
        return {name for name in names if name in self._allowed}

    def get_spec(self, name: str) -> Any | None:
        """The spec for `name`, across both halves of the union.

        Resolved through `specs()` rather than through each half's own lookup, because
        `specs()` is where the business-wins deduplication is decided. Asking the halves
        separately would let this return the *computer* tool for a name that the model
        was actually offered as a *business* tool — the same name resolving to two
        different specs depending on who asked.
        """
        for spec in self.specs():
            if spec.name == name:
                return spec
        return None

    def specs(self) -> list[Any]:
        """Business first, then computer tools, deduplicated by name.

        Order matters slightly: the business tools are the ones the original
        prompts were written around, so keeping them at the front means an
        existing prompt keeps selecting them the way it did before.

        Deduplication is not defensive padding — it is load-bearing. The provider
        rejects a tool list containing two entries with the same name, with HTTP
        400 "Tool names must be unique", and the agent then cannot run at all. That
        happened: a business tool and a computer tool were both called
        `document_read`. The failure surfaced as a provider error on every call,
        which reads like a network problem rather than a naming collision in our
        own registry, so the collision is logged loudly here as well as prevented.
        """
        seen: set[str] = set()
        specs: list[Any] = []
        duplicates: list[str] = []

        for spec in self.business.specs():
            if spec.name in seen:
                duplicates.append(spec.name)
                continue
            seen.add(spec.name)
            specs.append(spec)

        if self.computer:
            for spec in self.computer.specs():
                if spec.name in seen:
                    # The business tool wins, matching the ordering rationale above.
                    duplicates.append(spec.name)
                    continue
                seen.add(spec.name)
                specs.append(spec)

        if duplicates:
            logger.warning(
                "duplicate_tool_names",
                extra={"names": sorted(set(duplicates))},
            )
        if self._allowed is not None:
            specs = [spec for spec in specs if spec.name in self._allowed]
        return specs

    def catalog(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for spec in self.business.specs():
            rows.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "category": spec.category,
                    "source": "business",
                    "permission_level": spec.permission_level,
                    "risk_level": spec.risk_level,
                    "mutates_state": spec.mutates_state,
                    "requires_confirmation": spec.requires_confirmation,
                }
            )
        if self.computer:
            for row in self.computer.catalog():
                rows.append({**row, "source": "computer"})
        return rows

    def by_source(self) -> dict[str, list[str]]:
        result = {"business": [], "computer": []}
        if hasattr(self.business, "names"):
            result["business"] = sorted(self.business.names())
        if self.computer:
            result["computer"] = self.computer.names()
        return result

    # ---------------------------------------------------------------- execute --
    async def execute(self, ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolResult:
        if self.computer:
            tool = self.computer.get(name)
            if tool is not None:
                if self.executor is None:
                    return ToolResult(
                        ok=False,
                        error=f"tool '{name}' is registered but no executor is configured",
                        summary="executor missing",
                    )
                outcome = await self.executor.execute(
                    tool,
                    ctx,
                    arguments,
                    run_id=ctx.run_id or None,
                    confirm=self._confirm,
                    limits=self.limits,
                )
                return _to_tool_result(outcome)

        # Not a computer tool: the original path, untouched.
        return await self.business.execute(ctx, name, arguments)


__all__ = ["UnifiedToolRegistry"]

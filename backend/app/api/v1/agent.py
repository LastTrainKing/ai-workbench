"""Agent permission and computer-tool endpoints.

Backs Settings -> Agent Permissions and the tool catalogue. Like the AI provider
endpoints, this is a real control surface: raising the permission level changes
what the next agent run is allowed to do, and switching a capability off removes
those tools from the model's catalogue entirely.

What this API deliberately cannot do
------------------------------------
The protected-location rules (``.ssh``, credential stores, browser profiles,
Windows, Program Files) and the project's own ``.env`` are merged in from the
built-in policy on every load. A request here can *add* denied paths and can
*lower* the permission level, but it cannot remove a protection or grant a path
the built-in policy refuses. That asymmetry is the point: a settings endpoint
should not be a way to escalate past the sandbox.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.ai.tools import policy_store
from app.ai.tools.confirmation import get_broker
from app.ai.tools.permissions import (
    AgentMode,
    PermissionLevel,
    RiskLevel,
    classify_command,
)
from app.ai.tools.registry import CAPABILITIES, build_computer_registry
from app.api.deps import CurrentUser
from app.core.errors import ValidationError
from app.core.response import ok

router = APIRouter(prefix="/agent", tags=["agent"])


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class PermissionUpdate(BaseModel):
    """Partial update. Omitted fields are left untouched; ``null`` clears one."""

    mode: str | None = Field(default=None, description="assist | supervised | autonomous")
    level: int | None = Field(default=None, ge=0, le=3, description="0 observe .. 3 full control")
    allowed_paths: list[str] | None = Field(default=None, description="Replaces the configured list")
    extra_denied_paths: list[str] | None = Field(default=None, description="Additive only")
    allowed_domains: list[str] | None = None
    blocked_domains: list[str] | None = None
    max_tool_calls: int | None = Field(default=None, ge=1, le=2000)
    max_runtime_seconds: int | None = Field(default=None, ge=30, le=86400)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    max_output_bytes: int | None = Field(default=None, ge=4096, le=10_000_000)
    max_files_modified: int | None = Field(default=None, ge=1, le=1000)
    max_download_bytes: int | None = Field(default=None, ge=1024, le=5_000_000_000)
    confirmation_timeout_seconds: int | None = Field(default=None, ge=10, le=3600)


class CapabilityUpdate(BaseModel):
    """Switch a whole capability area on or off."""

    capabilities: dict[str, bool]


class ConfirmationDecision(BaseModel):
    approved: bool


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #
@router.get("/permissions", summary="Effective permission policy")
async def get_permissions(user: CurrentUser) -> dict[str, Any]:
    """The policy as Settings renders it, with the source of each value."""
    view = policy_store.describe(user.id)
    view["modes"] = [
        {
            "value": AgentMode.ASSIST.value,
            "label": "Assist",
            "description": "分析并给出建议，不执行任何操作",
        },
        {
            "value": AgentMode.SUPERVISED.value,
            "label": "Supervised",
            "description": "可以执行；高风险操作需要确认",
        },
        {
            "value": AgentMode.AUTONOMOUS.value,
            "label": "Autonomous",
            "description": "在授权范围内自主完成，关键操作仍会确认",
        },
    ]
    view["levels"] = [
        {"value": int(level), "label": PermissionLevel.label(int(level))}
        for level in PermissionLevel
    ]
    return ok(view)


@router.patch("/permissions", summary="Change the permission policy")
async def update_permissions(payload: PermissionUpdate, user: CurrentUser) -> dict[str, Any]:
    patch = payload.model_dump(exclude_unset=True)

    if patch.get("mode") is not None:
        mode = str(patch["mode"]).lower()
        if mode not in {m.value for m in AgentMode}:
            raise ValidationError(f"Unknown agent mode '{patch['mode']}'")
        patch["mode"] = mode

    if patch.get("level") is not None:
        level = int(patch["level"])
        if level not in {int(x) for x in PermissionLevel}:
            raise ValidationError(f"Unknown permission level {level}")
        patch["level"] = level

    policy_store.save_overrides(patch, user.id)
    return ok(policy_store.describe(user.id), message="Permission policy updated")


@router.delete("/permissions", summary="Reset the policy to the configured defaults")
async def reset_permissions(user: CurrentUser) -> dict[str, Any]:
    policy_store.reset_overrides(user.id)
    return ok(policy_store.describe(user.id), message="Permission policy reset")


# --------------------------------------------------------------------------- #
# "What would you do about this?"
# --------------------------------------------------------------------------- #
class PermissionProbe(BaseModel):
    """One thing to ask the policy about. Any subset may be supplied."""

    path: str | None = Field(default=None, description="A file or directory to test")
    write: bool = Field(default=False, description="Test writing rather than reading")
    domain: str | None = Field(default=None, description="A URL or host to test")
    command: str | None = Field(default=None, description="A shell command to classify")
    tool: str | None = Field(default=None, description="A tool name to test against the policy")
    arguments: dict[str, Any] | None = Field(default=None, description="That tool's arguments")


@router.post("/permissions/check", summary="Ask the real permission service about one thing")
async def check_permission(payload: PermissionProbe, user: CurrentUser) -> dict[str, Any]:
    """What the policy would decide, and why.

    This runs `PermissionManager` itself — the same object `file_write` and `terminal_exec`
    consult before they do anything. That is the whole point: a settings screen that
    re-implemented the rules would be a second source of truth, and the second one is always
    the one that drifts. The answer here is the answer the next tool call gets.

    A `deny` is a real refusal and a `confirm` is a real prompt; neither is a UI state. The
    protected-location rules (`.ssh`, credential stores, browser profiles, Windows, Program
    Files) and the project's own `.env` cannot be lifted from the settings endpoint at all,
    so a path inside them reports `deny` no matter what the level is set to — which is what
    the screen needs to be able to show.
    """
    from app.ai.tools.permissions import PermissionManager

    policy = policy_store.build_policy(user.id)
    manager = PermissionManager(policy)

    results: dict[str, Any] = {}
    if payload.path:
        results["path"] = manager.check_path(payload.path, write=bool(payload.write)).as_dict()
    if payload.domain:
        results["domain"] = manager.check_domain(payload.domain, writes=bool(payload.write)).as_dict()
    if payload.command:
        verdict = manager.check_command(payload.command)
        results["command"] = verdict.as_dict()
    if payload.tool:
        from app.ai.tools.registry import build_computer_registry

        registry = build_computer_registry(policy, enabled=policy_store.capability_flags(user.id))
        tool = registry.get(payload.tool)
        if tool is None:
            raise ValidationError(f"Unknown computer tool '{payload.tool}'")
        results["tool"] = manager.check_tool(tool.spec, payload.arguments or {}).as_dict()

    if not results:
        raise ValidationError("Supply at least one of: path, domain, command, tool")

    return ok(
        {
            "results": results,
            "policy": {
                "mode": policy.mode.value,
                "level": int(policy.level),
                "level_label": PermissionLevel.label(int(policy.level)),
            },
            "workspace": str(policy.workspace),
        }
    )


# --------------------------------------------------------------------------- #
# Capabilities and tools
# --------------------------------------------------------------------------- #
@router.get("/capabilities", summary="Capability areas and whether each is available")
async def list_capabilities(user: CurrentUser) -> dict[str, Any]:
    policy = policy_store.build_policy(user.id)
    registry = build_computer_registry(policy, enabled=policy_store.capability_flags(user.id))
    enabled_flags = policy_store.capability_flags(user.id)

    areas = []
    for area in registry.capabilities():
        areas.append(
            {
                **area,
                "user_enabled": enabled_flags.get(area["key"], True),
            }
        )
    return ok(
        {
            "capabilities": areas,
            "mode": policy.mode.value,
            "level": int(policy.level),
            "level_label": PermissionLevel.label(int(policy.level)),
            "tool_count": len(registry.names()),
        }
    )


@router.patch("/capabilities", summary="Enable or disable capability areas")
async def update_capabilities(payload: CapabilityUpdate, user: CurrentUser) -> dict[str, Any]:
    known = {group.key for group in CAPABILITIES}
    unknown = sorted(set(payload.capabilities) - known)
    if unknown:
        raise ValidationError(f"Unknown capability area(s): {', '.join(unknown)}")

    policy_store.save_capability_flags(payload.capabilities, user.id)
    policy = policy_store.build_policy(user.id)
    registry = build_computer_registry(policy, enabled=policy_store.capability_flags(user.id))
    return ok(
        {
            "capabilities": policy_store.capability_flags(user.id),
            "tool_count": len(registry.names()),
        },
        message="Capabilities updated",
    )


@router.get("/tools", summary="Every tool the agent can use, with its governance")
async def list_tools(
    user: CurrentUser,
    category: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """The catalogue, including business tools, so the UI can explain everything.

    ``available`` reflects the current policy: a tool the policy would refuse is
    reported as unavailable rather than hidden, because the user is looking at
    this screen precisely to find out what the agent can and cannot do.
    """
    from app.ai.agent.tools import get_tool_registry

    policy = policy_store.build_policy(user.id)
    registry = build_computer_registry(policy, enabled=policy_store.capability_flags(user.id))
    available_names = set(registry.names())

    rows: list[dict[str, Any]] = []

    for spec in get_tool_registry().specs():
        rows.append(
            {
                "name": spec.name,
                "description": spec.description,
                "category": spec.category,
                "source": "business",
                "permission_level": spec.permission_level,
                "permission_label": PermissionLevel.label(spec.permission_level),
                "risk_level": spec.risk_level,
                "mutates_state": spec.mutates_state,
                "requires_confirmation": spec.requires_confirmation,
                "timeout_seconds": spec.timeout_seconds,
                "available": True,  # business tools are not gated by the computer policy
                "parameters": spec.parameters,
            }
        )

    for row in registry.catalog():
        rows.append({**row, "source": "computer", "available": row["name"] in available_names})

    # Report the computer tools the policy currently excludes, so the screen can
    # show "3 tools need a higher permission level" instead of silently omitting
    # them.
    excluded: list[dict[str, Any]] = []
    from app.ai.tools.permissions import AgentPolicy

    permissive = AgentPolicy.from_settings(mode=AgentMode.SUPERVISED, level=PermissionLevel.FULL_CONTROL)
    all_names = set(build_computer_registry(permissive).names())
    for row in build_computer_registry(permissive).catalog():
        if row["name"] not in available_names and row["name"] in all_names:
            excluded.append(
                {
                    "name": row["name"],
                    "category": row["category"],
                    "permission_level": row["permission_level"],
                    "permission_label": row["permission_label"],
                    "reason": f"needs {row['permission_label']}",
                }
            )

    if category:
        rows = [r for r in rows if r["category"] == category]
    if source:
        rows = [r for r in rows if r["source"] == source]

    return ok(
        {
            "tools": rows,
            "count": len(rows),
            "available": sum(1 for r in rows if r["available"]),
            "excluded": excluded,
            "policy": {
                "mode": policy.mode.value,
                "level": int(policy.level),
                "level_label": PermissionLevel.label(int(policy.level)),
            },
        }
    )


@router.get("/tools/{tool_name}", summary="One tool in full detail")
async def get_tool(tool_name: str, user: CurrentUser) -> dict[str, Any]:
    policy = policy_store.build_policy(user.id)
    registry = build_computer_registry(policy, enabled=policy_store.capability_flags(user.id))
    tool = registry.get(tool_name)
    if tool is None:
        from app.ai.agent.tools import get_tool_registry

        business = get_tool_registry().get(tool_name)
        spec = business.spec
        return ok(
            {
                "name": spec.name,
                "description": spec.description,
                "category": spec.category,
                "source": "business",
                "parameters": spec.parameters,
                "governance": spec.governance(),
            }
        )

    spec = tool.spec
    return ok(
        {
            "name": spec.name,
            "description": spec.description,
            "category": spec.category,
            "source": "computer",
            "parameters": spec.parameters,
            "governance": spec.governance(),
            "available": True,
        }
    )


@router.post("/tools/classify-command", summary="Explain the risk of a command without running it")
async def classify(command: str, user: CurrentUser) -> dict[str, Any]:
    """Used by the confirmation dialog to show the user *why* something is flagged."""
    risk, reason = classify_command(command)
    return ok(
        {
            "command": command,
            "risk": risk.value,
            "reason": reason,
            "requires_confirmation": RiskLevel(risk).order >= RiskLevel.HIGH.order,
        }
    )


# --------------------------------------------------------------------------- #
# Confirmations
# --------------------------------------------------------------------------- #
@router.get("/confirmations", summary="High-risk operations waiting for a decision")
async def list_confirmations(user: CurrentUser, run_id: str | None = None) -> dict[str, Any]:
    broker = get_broker()
    items = broker.for_run(run_id) if run_id else broker.all_pending()
    return ok({"confirmations": items, "count": len(items)})


@router.post("/confirmations/{confirmation_id}", summary="Approve or decline a pending operation")
async def decide_confirmation(
    confirmation_id: str, payload: ConfirmationDecision, user: CurrentUser
) -> dict[str, Any]:
    resolved = await get_broker().resolve(confirmation_id, bool(payload.approved))
    if not resolved:
        raise ValidationError(
            "That confirmation is no longer waiting — it was already answered or timed out."
        )
    return ok(
        {"id": confirmation_id, "approved": payload.approved},
        message="Approved" if payload.approved else "Declined",
    )

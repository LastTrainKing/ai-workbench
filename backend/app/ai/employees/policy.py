"""Turning a user's permission policy into an employee's.

**The one rule: an employee narrows, never widens.** §10 of the brief requires that the
employee cannot self-authorise, and that UI state is not a security control. The mechanism
here is that the employee's ``permission_profile`` is applied as a *reduction* of the
user's effective policy. There is no branch that produces a more permissive result than
the user already had, so an employee created by an import, a template, or a hand-edited
JSON file cannot reach past its owner.

Concretely, every field moves in exactly one direction:

===========================  ====================================================
field                        narrowing
===========================  ====================================================
``level``                    ``min`` — lower number, less of the machine
``mode``                     toward ``ASSIST`` — the most conservative wins
``allowed_paths``            **intersection** — an employee may not add an area
``denied_paths``             union — an employee may add a denial, never remove one
``allowed_domains``          intersection
``blocked_domains``          union
``max_*`` ceilings           ``min`` — an employee may ask for less, not more
===========================  ====================================================

**The ceilings are the part that is easy to get wrong.** ``max_tool_calls = 10_000`` in an
employee profile looks harmless and is not: it is a way for a stored row to lift the
user's own budget. Taking the minimum makes the field mean "this employee gets at most
this much", which is the only reading that cannot be abused.

**Path intersection is textual, and that is deliberate.** ``Path`` objects are compared
after ``resolve()``-free normalisation, because the real containment check — the one that
handles ``..``, junctions and symlinks — happens in the permission manager at execution
time, against the filesystem. Duplicating it here would create a second answer to the same
question, and the weaker of the two would be the one that mattered.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.ai.tools.permissions import AgentMode, AgentPolicy, PermissionLevel

#: Most conservative first. Narrowing means moving toward the front of this tuple.
_MODE_ORDER: tuple[AgentMode, ...] = (AgentMode.ASSIST, AgentMode.SUPERVISED, AgentMode.AUTONOMOUS)

#: Ceilings on the policy. An employee profile may lower any of these and cannot raise one.
_CEILING_FIELDS: tuple[str, ...] = (
    "max_tool_calls",
    "max_runtime_seconds",
    "max_retries",
    "max_output_bytes",
    "max_files_modified",
    "max_download_bytes",
    "confirmation_timeout_seconds",
)


def _normalise_path(raw: str) -> str:
    """A comparable form of a path, without touching the filesystem."""
    return os.path.normcase(os.path.normpath(os.path.expandvars(str(raw).strip())))


def _narrow_mode(base: AgentMode, requested: Any) -> AgentMode:
    if requested is None:
        return base
    try:
        want = AgentMode(str(requested).lower())
    except ValueError:
        return base
    # The more conservative of the two wins. If the employee asks for more autonomy than
    # the user granted, the user's answer stands.
    return min(base, want, key=_MODE_ORDER.index)


def _narrow_level(base: PermissionLevel, requested: Any) -> PermissionLevel:
    if requested is None:
        return base
    try:
        want = PermissionLevel(int(requested))
    except (TypeError, ValueError):
        return base
    return want if want < base else base


def _narrow_tools(base: frozenset[str] | None, profile: dict[str, Any]) -> frozenset[str] | None:
    """The employee's tool whitelist, intersected with the user's if the user has one.

    Three cases, and the middle one is the one that matters:

    * no ``skills`` key — inherit the base unchanged (``None`` means "all")
    * ``skills`` present — the intersection. An employee may only *remove* tools
    * ``skills`` is an empty list — the empty set, which denies everything. "I listed no
      tools" is a statement, not an omission.
    """
    if "skills" not in profile:
        return base
    skills = profile.get("skills")
    if not isinstance(skills, list):
        return base
    wanted = frozenset(str(s) for s in skills)
    return wanted if base is None else wanted & base


def narrow_policy(base: AgentPolicy, profile: dict[str, Any] | None) -> AgentPolicy:
    """Apply an employee's profile to the user's policy. Returns a new policy.

    ``base`` is not mutated — it is shared, and a run that narrowed it in place would
    leave the user's own settings narrower than they were.
    """
    if not profile:
        return base

    narrowed = AgentPolicy(
        mode=_narrow_mode(base.mode, profile.get("mode")),
        level=_narrow_level(base.level, profile.get("level")),
        workspace=base.workspace,
        allowed_paths=list(base.allowed_paths),
        denied_paths=list(base.denied_paths),
        allowed_domains=list(base.allowed_domains),
        blocked_domains=list(base.blocked_domains),
        allowed_tools=_narrow_tools(base.allowed_tools, profile),
    )
    for field in _CEILING_FIELDS:
        setattr(narrowed, field, getattr(base, field))

    # --- paths: allowed intersects, denied unions ---
    requested_allowed = profile.get("allowed_paths")
    if isinstance(requested_allowed, list):
        allowed_keys = {_normalise_path(p) for p in requested_allowed if str(p).strip()}
        narrowed.allowed_paths = [
            p for p in base.allowed_paths if _normalise_path(p) in allowed_keys
        ]
        # An empty request means "none of them", which is the safe reading. A profile that
        # forgets the key entirely leaves the base list alone — absent is not the same as
        # empty, and conflating them would silently lock every employee out of the
        # workspace.
    extra_denied = profile.get("extra_denied_paths")
    if isinstance(extra_denied, list):
        narrowed.denied_paths = [
            *base.denied_paths,
            *[Path(p) for p in extra_denied if str(p).strip()],
        ]

    # --- domains: allowed intersects, blocked unions ---
    requested_domains = profile.get("allowed_domains")
    if isinstance(requested_domains, list):
        wanted = {str(d).strip().lower() for d in requested_domains if str(d).strip()}
        narrowed.allowed_domains = [d for d in base.allowed_domains if d.lower() in wanted]
    extra_blocked = profile.get("blocked_domains")
    if isinstance(extra_blocked, list):
        narrowed.blocked_domains = [
            *base.blocked_domains,
            *[str(d).strip().lower() for d in extra_blocked if str(d).strip()],
        ]

    # --- ceilings: min ---
    for field in _CEILING_FIELDS:
        value = profile.get(field)
        if isinstance(value, int) and value > 0:
            current = getattr(narrowed, field)
            setattr(narrowed, field, min(current, value))

    return narrowed


def describe_narrowing(base: AgentPolicy, narrowed: AgentPolicy) -> dict[str, Any]:
    """What changed, for the employee detail page. Empty dict = identical to the user's.

    **Both the label and the number.** The label is what an API response should carry —
    `"Observe only"` reads well in a JSON dump and in the audit log. The number is what a
    window needs, because the window has to say it in its own language and translating an
    English label back into an integer is a lookup table nobody maintains. The first
    version sent only the label, and the employee page rendered
    「权限降到「Observe only」」 on a Chinese screen.
    """
    changes: dict[str, Any] = {}
    if narrowed.level != base.level:
        changes["level"] = {
            "from": PermissionLevel.label(int(base.level)),
            "from_value": int(base.level),
            "to": PermissionLevel.label(int(narrowed.level)),
            "to_value": int(narrowed.level),
        }
    if narrowed.mode != base.mode:
        changes["mode"] = {"from": base.mode.value, "to": narrowed.mode.value}
    if len(narrowed.allowed_paths) != len(base.allowed_paths):
        changes["allowed_paths"] = {
            "from": len(base.allowed_paths),
            "to": len(narrowed.allowed_paths),
        }
    if len(narrowed.denied_paths) != len(base.denied_paths):
        changes["denied_paths"] = {
            "from": len(base.denied_paths),
            "to": len(narrowed.denied_paths),
        }
    for field in _CEILING_FIELDS:
        if getattr(narrowed, field) != getattr(base, field):
            changes[field] = {"from": getattr(base, field), "to": getattr(narrowed, field)}
    return changes


def tool_allowed(profile: dict[str, Any] | None, tool_name: str) -> bool:
    """Whether the employee's whitelist admits this tool.

    An employee with no ``skills`` key at all gets the base policy's answer — the key is
    optional. An employee with an **empty** list gets nothing, because "I listed no tools"
    is a statement, not an omission.
    """
    if not profile or "skills" not in profile:
        return True
    skills = profile.get("skills")
    if not isinstance(skills, list):
        return True
    return tool_name in {str(s) for s in skills}

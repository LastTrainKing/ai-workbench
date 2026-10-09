"""The computer tool registry.

One place that knows which machine capabilities exist, what each costs, and which
ones the current policy has switched off. The agent asks the registry for the
tools it may use and is given only those, so a capability that is disabled is not
merely refused at call time — it is never offered to the model in the first place.
That matters: a model that cannot see a tool cannot waste a turn trying to use it.

The registry is rebuilt from the policy rather than mutated, so a permission
change takes effect on the next run with no stale state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.agent.tools import Tool
from app.ai.tools.builtin import (
    build_code_tools,
    build_filesystem_tools,
    build_git_tools,
    build_network_tools,
    build_observation_tools,
    build_process_tools,
    build_project_tools,
    build_terminal_tools,
)
from app.ai.tools.permissions import AgentMode, AgentPolicy, PermissionLevel, RiskLevel
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Tools whose successful call means a file now exists at a known path.
#:
#: Defined here, next to the registry that knows which capabilities exist, because
#: two callers need the same answer and they must not be allowed to drift: the
#: build executor, deciding which files a run produced, and the agent's final
#: verifier, telling the model where those files are so it does not have to guess.
#:
#: That second caller is the reason this moved. The verifier used to be handed no
#: information about the run's own output, guessed a path, missed the
#: `deliverables/` prefix, and the resulting "file does not exist" failed an
#: otherwise perfect run.
ARTIFACT_TOOLS: frozenset[str] = frozenset({"document_write", "file_write"})


@dataclass(slots=True)
class CapabilityGroup:
    """A named family of tools, so the Settings screen can switch a whole area."""

    key: str
    label: str
    description: str
    required_level: PermissionLevel
    builder: Any


#: The capability areas, in the order the Settings screen shows them.
CAPABILITIES: tuple[CapabilityGroup, ...] = (
    CapabilityGroup(
        "filesystem", "Filesystem", "Read, write, move and delete files and directories",
        PermissionLevel.OBSERVE, build_filesystem_tools,
    ),
    CapabilityGroup(
        "code", "Code", "Search, replace, patch and analyse source files",
        PermissionLevel.OBSERVE, build_code_tools,
    ),
    CapabilityGroup(
        "terminal", "Terminal", "Run PowerShell, cmd, Python and Node commands",
        PermissionLevel.SYSTEM, build_terminal_tools,
    ),
    CapabilityGroup(
        "git", "Git", "Inspect and change repository state",
        PermissionLevel.OBSERVE, build_git_tools,
    ),
    CapabilityGroup(
        "project", "Build and test", "Run the project's tests, linter, type checker and build",
        PermissionLevel.WORKSPACE, build_project_tools,
    ),
    CapabilityGroup(
        "process", "Processes", "List, start and stop processes",
        PermissionLevel.OBSERVE, build_process_tools,
    ),
    CapabilityGroup(
        "network", "Network", "Fetch URLs and download files from allowed hosts",
        PermissionLevel.SYSTEM, build_network_tools,
    ),
    CapabilityGroup(
        "observation", "Computer observation", "Capture the screen and read display state",
        PermissionLevel.OBSERVE, build_observation_tools,
    ),
)


class ComputerToolRegistry:
    """Holds the enabled computer tools for one policy."""

    def __init__(self, policy: AgentPolicy, *, enabled: dict[str, bool] | None = None) -> None:
        self.policy = policy
        self._tools: dict[str, Tool] = {}
        self._enabled = dict(enabled or {})
        self._build()

    # ------------------------------------------------------------------ build --
    def _group_enabled(self, group: CapabilityGroup) -> bool:
        """A group is available when it is not switched off and the level allows it."""
        if not self._enabled.get(group.key, True):
            return False
        if self.policy.mode is AgentMode.ASSIST:
            # Assist mode is analysis only: it may look, not touch.
            return group.required_level <= PermissionLevel.OBSERVE
        return self.policy.level >= group.required_level

    def _build(self) -> None:
        for group in CAPABILITIES:
            if not self._group_enabled(group):
                logger.debug("capability_disabled", extra={"group": group.key})
                continue
            for tool in group.builder():
                spec = tool.spec
                # Assist mode is analysis only: it may read and propose, never
                # change. Filtering here rather than at call time means the model
                # is never offered an action it cannot take.
                if self.policy.mode is AgentMode.ASSIST and spec.mutates_state:
                    continue
                # A mutating tool above the user's level is not offered either:
                # the executor would refuse it anyway, and listing an impossible
                # action only wastes a turn.
                if (
                    spec.mutates_state
                    and spec.permission_level > int(self.policy.level)
                    and self.policy.mode is not AgentMode.AUTONOMOUS
                ):
                    continue
                if spec.name in self._tools:
                    raise ValueError(f"Duplicate computer tool name: {spec.name}")
                self._tools[spec.name] = tool

    # ------------------------------------------------------------------ access --
    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_spec(self, name: str) -> Any | None:
        """The spec for `name`, or None.

        The executor needs this to ask one question before a call runs: *can repeating
        this be harmful?* `specs()` returns a list, and scanning it per call would work
        but hides the intent; the lookup is the point.
        """
        tool = self._tools.get(name)
        return tool.spec if tool is not None else None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def tools(self) -> list[Tool]:
        return [self._tools[name] for name in self.names()]

    def specs(self) -> list[Any]:
        return [tool.spec for tool in self.tools()]

    def by_category(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for tool in self.tools():
            grouped.setdefault(tool.spec.category, []).append(tool.spec.name)
        return {key: sorted(value) for key, value in sorted(grouped.items())}

    def catalog(self) -> list[dict[str, Any]]:
        """Everything the UI needs to explain the tool system."""
        rows = []
        for tool in self.tools():
            spec = tool.spec
            rows.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "category": spec.category,
                    "permission_level": spec.permission_level,
                    "permission_label": PermissionLevel.label(spec.permission_level),
                    "risk_level": spec.risk_level,
                    "timeout_seconds": spec.timeout_seconds,
                    "mutates_state": spec.mutates_state,
                    "requires_confirmation": spec.requires_confirmation
                    or RiskLevel(spec.risk_level).order >= RiskLevel.HIGH.order,
                    "parameters": spec.parameters,
                    "output_schema": spec.output_schema,
                }
            )
        return rows

    def capabilities(self) -> list[dict[str, Any]]:
        """Per-area status, for the Settings screen."""
        return [
            {
                "key": group.key,
                "label": group.label,
                "description": group.description,
                "required_level": int(group.required_level),
                "required_level_label": PermissionLevel.label(int(group.required_level)),
                "enabled": self._group_enabled(group),
                "tools": [t.spec.name for t in group.builder()],
            }
            for group in CAPABILITIES
        ]


def build_computer_registry(
    policy: AgentPolicy, *, enabled: dict[str, bool] | None = None
) -> ComputerToolRegistry:
    return ComputerToolRegistry(policy, enabled=enabled)


def spec_for(registry: Any, name: str) -> Any | None:
    """The spec for `name`, from any registry shape, or None.

    **Why this exists rather than a call to `registry.get_spec`.** `get_spec` is a
    convenience that `ToolRegistry` and `UnifiedToolRegistry` happen to provide. `specs()`
    is the contract *every* registry satisfies — including the small stand-ins the test
    suite drives the executor with. The executor asked for `get_spec` directly and four
    tests that had nothing to do with tool classification stopped passing, because their
    registry was a stub with `specs()`, `names()` and `execute()` and no reason to have
    anything else.

    The answer has to be right, not merely present: the executor uses it to decide whether a
    call can be repeated after a crash, so a miss means "unknown tool", which is treated as
    unsafe. Falling through to a linear scan of `specs()` is not a fallback that weakens the
    check — it is the same question asked of the interface that is actually guaranteed.
    """
    getter = getattr(registry, "get_spec", None)
    if callable(getter):
        found = getter(name)
        if found is not None:
            return found
    lister = getattr(registry, "specs", None)
    if callable(lister):
        for spec in lister():
            if getattr(spec, "name", "") == name:
                return spec
    return None


__all__ = [
    "ARTIFACT_TOOLS",
    "CAPABILITIES",
    "CapabilityGroup",
    "ComputerToolRegistry",
    "build_computer_registry",
    "spec_for",
]

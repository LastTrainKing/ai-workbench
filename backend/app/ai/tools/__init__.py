"""The computer tool system.

This package gives the agent a controlled way to act on the machine, and it is
the only place that is allowed to. The layering is the point:

    Agent
      -> Tool Registry          (what exists, and what it costs)
      -> Permission Manager     (may it run, and must the user be asked?)
      -> Tool Executor          (validate, time out, cap output, audit)
      -> Operating System

No layer above the executor touches the OS. A tool that skipped the executor
would bypass the sandbox, the risk classification and the audit trail, so nothing
in this package is designed to be called directly by the agent.

Two properties are worth stating explicitly because they are easy to lose later:

* the agent does **not** reach the database through these tools — the business
  tools still go through Service -> Repository, unchanged;
* adding a capability means adding a tool with a declared permission level and
  risk level, not widening an existing one.
"""

from __future__ import annotations

from app.ai.tools.executor import (
    ConfirmationRequest,
    FailureKind,
    ResourceLimits,
    ToolExecutor,
    ToolOutcome,
    classify_failure,
)
from app.ai.tools.permissions import (
    AgentMode,
    AgentPolicy,
    Decision,
    PermissionLevel,
    PermissionManager,
    PermissionVerdict,
    RiskLevel,
    classify_command,
    default_workspace,
)
from app.ai.tools.registry import ComputerToolRegistry, build_computer_registry

__all__ = [
    "AgentMode",
    "AgentPolicy",
    "ComputerToolRegistry",
    "ConfirmationRequest",
    "Decision",
    "FailureKind",
    "PermissionLevel",
    "PermissionManager",
    "PermissionVerdict",
    "ResourceLimits",
    "RiskLevel",
    "ToolExecutor",
    "ToolOutcome",
    "build_computer_registry",
    "classify_command",
    "classify_failure",
    "default_workspace",
]

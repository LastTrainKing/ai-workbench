"""Is this code running *inside* a tool the agent called?

## Why this exists

The agent can read and write files, run commands and make HTTP requests. The permission
policy is what decides how much of that it may do — so the policy is the one thing the
agent must never be able to change. If it can, every other limit is a suggestion.

There is no tool that writes the policy, and that is not enough. The agent does not need a
tool for it: it can call the application's own API through `http_post`, or write the file
through `filesystem`, or run a command. Those are three different roads to the same place,
and closing one of them is not closing the road.

So the check lives at the **thing being protected**, not at the doors:

    ToolExecutor  → sets the flag around every tool call
    policy_store  → refuses to write while the flag is set

The executor says of itself: "Runs tools. The agent never calls a tool any other way."
That is the single place a flag can be set and be true.

A `ContextVar` rather than a global: two runs in the same process must not see each
other's flag, and the flag has to survive `await` boundaries without leaking between
concurrent tasks.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

#: True while a tool is executing. Read by the things the agent must not touch.
_TOOL_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar("agent_tool_active", default=False)

#: The tool currently running, for the audit line when something is refused.
_TOOL_NAME: contextvars.ContextVar[str] = contextvars.ContextVar("agent_tool_name", default="")


class AgentScopeViolation(PermissionError):
    """The agent tried to change something it is not allowed to change."""


def in_agent_scope() -> bool:
    return _TOOL_ACTIVE.get()


def current_tool() -> str:
    return _TOOL_NAME.get()


@contextmanager
def agent_tool_scope(tool_name: str) -> Iterator[None]:
    """Mark the block as "the agent is acting" for its duration."""
    active_token = _TOOL_ACTIVE.set(True)
    name_token = _TOOL_NAME.set(tool_name)
    try:
        yield
    finally:
        _TOOL_ACTIVE.reset(active_token)
        _TOOL_NAME.reset(name_token)


def refuse_if_in_agent_scope(what: str) -> None:
    """Raise when the agent is trying to change `what`.

    Called from the protected side. The message names the tool, so the audit line says
    which road was taken rather than only that one was blocked.
    """
    if in_agent_scope():
        raise AgentScopeViolation(
            f"the agent may not change {what} (attempted from tool "
            f"'{current_tool() or 'unknown'}'); permission settings are the user's"
        )


__all__ = [
    "AgentScopeViolation",
    "agent_tool_scope",
    "current_tool",
    "in_agent_scope",
    "refuse_if_in_agent_scope",
]

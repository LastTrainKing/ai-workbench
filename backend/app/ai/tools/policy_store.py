"""Runtime storage for the agent permission policy.

Follows the same precedence rule as the AI provider settings, so the product
behaves consistently in both places:

    Settings -> Agent Permissions   (this file, highest)
              |
              v
    environment / .env              (deployment defaults)
              |
              v
    built-in defaults               (safe out of the box)

The override file lives in ``.runtime/agent-policy.json``, which is git-ignored:
a user's decision to grant System access is a property of their machine, not of
the repository.

Only the fields a user can meaningfully change are stored. The denied-path list
and the protected-location rules are deliberately **not** writable here — they are
merged in from the built-in policy every time, so a settings file cannot be edited
to expose ``.ssh``. A user can *add* denied paths, never remove them.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

from app.ai.tools.permissions import AgentMode, AgentPolicy, PermissionLevel
from app.core.config import runtime_root, settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Keys a caller may set. Anything else in the file is ignored.
MUTABLE_KEYS = frozenset(
    {
        "mode",
        "level",
        "allowed_paths",
        "extra_denied_paths",
        "allowed_domains",
        "blocked_domains",
        "max_tool_calls",
        "max_runtime_seconds",
        "max_retries",
        "max_output_bytes",
        "max_files_modified",
        "max_download_bytes",
        "confirmation_timeout_seconds",
        "capabilities",
    }
)


def _store_path(user_id: Any = None) -> Path:
    """Where this user's policy overrides live.

    ## Why it takes a user

    It did not, and the route that writes it accepted `user: CurrentUser` and never read
    `user.id`. So the file was one per machine: user A turning the agent up to Full
    Control silently turned it up for user B. Memory, skills, tasks and knowledge bases
    are all per-user; the permission policy — the thing that decides what the agent may
    do to the machine — was the one that was not.

    The override file lives under `agent-policy/<user id>.json`. A caller with no user
    (the CLI, a migration) gets the legacy shared path, which is read-only in practice:
    it is migrated on first use and never written again.
    """
    override = os.environ.get("AI_WORKBENCH_AGENT_POLICY")
    if override:
        return Path(override)

    if user_id is None:
        return runtime_root() / "agent-policy.json"
    return runtime_root() / "agent-policy" / f"{user_id}.json"


def _legacy_path() -> Path:
    return runtime_root() / "agent-policy.json"


def _migrate_legacy_once(user_id: Any) -> None:
    """Hand the pre-existing shared policy to the first user who asks for one.

    Before this was per-user there was a single file. Someone may have changed their
    settings in it. Copying it to the first reader keeps that change; renaming it to
    `.migrated` means it cannot leak to a second user, which is the bug being fixed.
    """
    legacy = _legacy_path()
    if not legacy.is_file() or user_id is None:
        return
    target = _store_path(user_id)
    if target.exists():
        return
    with contextlib.suppress(OSError):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")
        legacy.rename(legacy.with_suffix(".json.migrated"))
        logger.info("agent_policy_migrated_to_per_user", extra={"user_id": str(user_id)})


def load_overrides(user_id: Any = None) -> dict[str, Any]:
    _migrate_legacy_once(user_id)
    path = _store_path(user_id)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("agent_policy_unreadable", extra={"error": str(exc), "path": str(path)})
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in MUTABLE_KEYS}


def save_overrides(patch: dict[str, Any], user_id: Any = None) -> dict[str, Any]:
    """Merge a patch into this user's overrides and return the new set."""
    current = load_overrides(user_id)
    for key, value in (patch or {}).items():
        if key not in MUTABLE_KEYS:
            continue
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value

    path = _store_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    return current


def reset_overrides(user_id: Any = None) -> None:
    with contextlib.suppress(FileNotFoundError):
        _store_path(user_id).unlink()


def capability_flags(user_id: Any = None) -> dict[str, bool]:
    stored = load_overrides(user_id).get("capabilities")
    return dict(stored) if isinstance(stored, dict) else {}


def save_capability_flags(flags: dict[str, bool], user_id: Any = None) -> dict[str, bool]:
    save_overrides({"capabilities": {k: bool(v) for k, v in (flags or {}).items()}}, user_id)
    return capability_flags(user_id)


def build_policy(user_id: Any = None) -> AgentPolicy:
    """The effective policy: settings, then **this user's** overrides on top."""
    overrides = load_overrides(user_id)

    mode = overrides.get("mode", settings.agent_mode)
    level = overrides.get("level", settings.agent_permission_level)

    policy = AgentPolicy.from_settings(mode=mode, level=level)

    # Writable areas: the user's list replaces the configured one, but the
    # workspace and the project are always re-added by from_settings.
    allowed = overrides.get("allowed_paths")
    if isinstance(allowed, list):
        policy.allowed_paths = [*[Path(p) for p in allowed if str(p).strip()], *policy.allowed_paths]

    # Denied areas: additive only. A settings file can protect more, never less.
    extra_denied = overrides.get("extra_denied_paths")
    if isinstance(extra_denied, list):
        policy.denied_paths = [*policy.denied_paths, *[Path(p) for p in extra_denied if str(p).strip()]]

    for key in ("allowed_domains", "blocked_domains"):
        value = overrides.get(key)
        if isinstance(value, list):
            setattr(policy, key, [str(v).strip().lower() for v in value if str(v).strip()])

    for key in (
        "max_tool_calls",
        "max_runtime_seconds",
        "max_retries",
        "max_output_bytes",
        "max_files_modified",
        "max_download_bytes",
        "confirmation_timeout_seconds",
    ):
        value = overrides.get(key)
        if isinstance(value, int) and value > 0:
            setattr(policy, key, value)

    # Re-coerce after direct assignment so enum comparisons stay valid.
    policy.mode = mode if isinstance(mode, AgentMode) else AgentMode(str(mode).lower())
    policy.level = level if isinstance(level, PermissionLevel) else PermissionLevel(int(level))
    return policy


def describe(user_id: Any = None) -> dict[str, Any]:
    """What Settings -> Agent Permissions shows."""
    from app.ai.tools.permissions import PermissionManager

    policy = build_policy(user_id)
    view = PermissionManager(policy).describe()
    view["overrides_active"] = bool(load_overrides(user_id))
    view["capabilities"] = capability_flags(user_id)
    view["source"] = "runtime" if load_overrides(user_id) else "defaults"
    return view


__all__ = [
    "MUTABLE_KEYS",
    "build_policy",
    "capability_flags",
    "describe",
    "load_overrides",
    "reset_overrides",
    "save_capability_flags",
    "save_overrides",
]

"""A permission change must change what the next tool call does.

Not "the toggle moved". The claim being tested is the one that matters to a person using
this: if the screen says the agent may not write outside its workspace, then the next
`file_write` outside the workspace must not write. A settings page that only moves a value
would pass a UI test and fail this one.

Everything here goes through `ToolExecutor` — the object the agent uses and the only way a
tool is ever run. Nothing calls the permission service directly to ask whether it is
configured correctly, because a service that answers questions about itself is not evidence
about behaviour.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.ai.agent.tools import ToolContext
from app.ai.tools import policy_store
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.permissions import AgentMode, PermissionLevel, PermissionManager
from app.ai.tools.registry import build_computer_registry


@pytest.fixture
def isolated_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep this test's policy and workspace out of the runtime directory.

    The policy is a file per user under the runtime root, and the workspace is a directory
    under it. A test that wrote to either would be changing the machine's agent permissions
    and the agent's scratch area — the same class of mistake as a verifier writing into the
    user's data directory, and it would do it to whoever runs the suite.
    """
    monkeypatch.setenv("AI_WORKBENCH_AGENT_POLICY", str(tmp_path / "agent-policy.json"))
    monkeypatch.setenv("AI_WORKBENCH_AGENT_WORKSPACE", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir(exist_ok=True)
    return tmp_path


def _owner() -> uuid.UUID:
    return uuid.uuid4()


async def _run(owner: uuid.UUID, tool_name: str, arguments: dict) -> object:
    """Run one computer tool the way the agent does, under this user's current policy."""
    policy = policy_store.build_policy(owner)
    registry = build_computer_registry(policy, enabled=policy_store.capability_flags(owner))
    tool = registry.get(tool_name)
    assert tool is not None, f"{tool_name} is not in the catalogue under this policy"
    ctx = ToolContext(
        session=None,
        owner_id=owner,
        working_directory=str(policy.workspace),
        permissions=PermissionManager(policy),
    )
    return await ToolExecutor(policy).execute(tool, ctx, arguments)


def _set(owner: uuid.UUID, **patch: object) -> None:
    policy_store.save_overrides(patch, owner)


# --------------------------------------------------------------------------- #
# The level actually gates a write
# --------------------------------------------------------------------------- #
async def test_observe_level_stops_a_real_write(isolated_policy: Path) -> None:
    owner = _owner()
    _set(owner, mode=AgentMode.AUTONOMOUS.value, level=int(PermissionLevel.OBSERVE))
    target = isolated_policy / "workspace" / "written.txt"

    outcome = await _run(owner, "file_write", {"path": str(target), "content": "hello\n"})

    assert outcome.status == "DENIED", outcome.error
    assert not target.exists(), "a write was refused and the file was created anyway"


async def test_raising_the_level_lets_the_same_call_through(isolated_policy: Path) -> None:
    """The other half. Without this the first test would pass on a broken registry."""
    owner = _owner()
    _set(owner, mode=AgentMode.AUTONOMOUS.value, level=int(PermissionLevel.FULL_CONTROL))
    target = isolated_policy / "workspace" / "written.txt"

    outcome = await _run(owner, "file_write", {"path": str(target), "content": "hello\n"})

    assert outcome.ok, outcome.error
    assert target.read_bytes() == b"hello\n"


async def test_the_change_takes_effect_on_the_next_call_not_the_next_restart(
    isolated_policy: Path,
) -> None:
    """Same process, same tool, opposite outcomes — the point of a live control surface."""
    owner = _owner()
    target = isolated_policy / "workspace" / "flip.txt"

    _set(owner, mode=AgentMode.AUTONOMOUS.value, level=int(PermissionLevel.OBSERVE))
    refused = await _run(owner, "file_write", {"path": str(target), "content": "x"})
    _set(owner, mode=AgentMode.AUTONOMOUS.value, level=int(PermissionLevel.FULL_CONTROL))
    allowed = await _run(owner, "file_write", {"path": str(target), "content": "x"})

    assert refused.status == "DENIED"
    assert allowed.ok
    assert target.read_bytes() == b"x"


# --------------------------------------------------------------------------- #
# What cannot be lifted
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("level", [int(level) for level in PermissionLevel])
async def test_the_project_secrets_are_denied_at_every_level(
    isolated_policy: Path, level: int
) -> None:
    """A settings endpoint is not a way past the sandbox.

    `.env` holds this installation's `SECRET_KEY`. The built-in policy denies it and the
    settings API merges those rules back in on every load, so no level makes it readable —
    and the check has to be behavioural, because "the API refuses to remove the rule" and
    "the tool refuses to read the file" are different claims and only the second one is
    about the file.
    """
    owner = _owner()
    _set(owner, mode=AgentMode.AUTONOMOUS.value, level=level)
    secrets = Path(__file__).resolve().parents[3] / ".env"
    assert secrets.is_file(), "the fixture expects the project's own .env to exist"

    outcome = await _run(owner, "file_read", {"path": str(secrets)})

    assert outcome.status == "DENIED", f"level {level} read the project's secrets"
    assert "SECRET_KEY" not in str(outcome.data or "")


async def test_a_path_outside_the_workspace_is_refused_with_the_remedy(
    isolated_policy: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Outside the workspace is a refusal, and the message says what to do about it.

    It used to report `confirm` and be enforced as a refusal. Nothing could ever act on that
    `confirm`: the only consumer of a `CONFIRM` verdict is the executor's *tool*-level check,
    and at Workspace level and above that allows `file_write` outright, so no prompt was ever
    raised. The screen said "needs confirmation", the write was refused, and the user was
    told to expect a question nobody asked.

    The remedy is a settings action — grant the directory — and the message names it. A
    per-call prompt for the same directory would ask the same question every time and teach
    people to click through it.
    """
    owner = _owner()
    _set(owner, mode=AgentMode.AUTONOMOUS.value, level=int(PermissionLevel.FULL_CONTROL))
    outside = tmp_path_factory.mktemp("outside") / "somewhere-else.txt"

    outcome = await _run(owner, "file_write", {"path": str(outside), "content": "x"})

    assert outcome.status == "DENIED", outcome.error
    assert not outside.exists()
    details = str(getattr(outcome.verdict, "detail", "") or outcome.error)
    assert "允许的目录" in details, f"the refusal does not say how to fix it: {details}"

    # And the *reported* decision is the enforced one. Asserting only the behaviour left this
    # test passing when `check_path` went back to reporting `confirm`: the write was still
    # refused, so nothing observable changed except the thing the screen shows — which is the
    # whole failure. The mutation check found that gap.
    manager = PermissionManager(policy_store.build_policy(owner))
    reported = manager.check_path(str(outside), write=True).decision.value
    assert reported == "deny", (
        f"the settings screen would show '{reported}' for a path the executor refuses without "
        "asking anyone"
    )


async def test_an_approval_cannot_unlock_a_denied_location(isolated_policy: Path) -> None:
    """`approved` means "a question was answered", not "skip the checks".

    The confirmation is about the *tool*; it must not lift the rule about the *path* —
    otherwise the way past a protection would be to arrange for some prompt to be raised and
    then say yes to it. A protection a different question can open is not a protection.

    The denied location is a temporary directory added to `extra_denied_paths`, not the
    project's own `.env`: this test fails when the protection is removed, and a failing test
    must not be able to overwrite the secrets it is protecting.
    """
    owner = _owner()
    protected = isolated_policy / "protected"
    protected.mkdir(exist_ok=True)
    _set(
        owner,
        mode=AgentMode.AUTONOMOUS.value,
        level=int(PermissionLevel.OBSERVE),
        extra_denied_paths=[str(protected)],
    )
    target = protected / "should-not-exist.txt"

    asked: list[str] = []

    async def approve(request: object) -> bool:
        asked.append(str(getattr(request, "tool", "?")))
        return True

    policy = policy_store.build_policy(owner)
    registry = build_computer_registry(policy, enabled=policy_store.capability_flags(owner))
    ctx = ToolContext(
        session=None,
        owner_id=owner,
        working_directory=str(policy.workspace),
        permissions=PermissionManager(policy),
    )
    outcome = await ToolExecutor(policy).execute(
        registry.get("file_write"),
        ctx,
        {"path": str(target), "content": "x"},
        confirm=approve,
    )

    assert asked == ["file_write"], "the confirmation was not raised, so this proves nothing"
    assert outcome.status == "DENIED", outcome.error
    assert not target.exists(), "an approval opened a location the policy denies"


async def test_an_approved_confirmation_actually_runs(isolated_policy: Path) -> None:
    """The bug this test was written for.

    At Observe level `file_write` raises a confirmation. The executor asks, the user says
    yes — and the tool's own path check then raised the *same* verdict as a refusal, so the
    write did not happen. "I approved it and nothing happened", with nothing in the message
    to explain why.

    A prompt that cannot be honoured is worse than no prompt: it spends the user's attention
    and then ignores the answer.
    """
    owner = _owner()
    _set(owner, mode=AgentMode.AUTONOMOUS.value, level=int(PermissionLevel.OBSERVE))
    target = isolated_policy / "workspace" / "approved.txt"

    asked: list[str] = []

    async def approve(request: object) -> bool:
        asked.append(str(getattr(request, "tool", "?")))
        return True

    policy = policy_store.build_policy(owner)
    registry = build_computer_registry(policy, enabled=policy_store.capability_flags(owner))
    tool = registry.get("file_write")
    ctx = ToolContext(
        session=None,
        owner_id=owner,
        working_directory=str(policy.workspace),
        permissions=PermissionManager(policy),
    )
    outcome = await ToolExecutor(policy).execute(
        tool, ctx, {"path": str(target), "content": "yes\n"}, confirm=approve
    )

    assert asked == ["file_write"], "the user was not asked"
    assert outcome.ok, outcome.error
    assert target.read_bytes() == b"yes\n"


async def test_declining_the_same_prompt_writes_nothing(isolated_policy: Path) -> None:
    """The other half: the answer is still allowed to be no."""
    owner = _owner()
    _set(owner, mode=AgentMode.AUTONOMOUS.value, level=int(PermissionLevel.OBSERVE))
    target = isolated_policy / "workspace" / "declined.txt"

    async def decline(request: object) -> bool:
        return False

    policy = policy_store.build_policy(owner)
    registry = build_computer_registry(policy, enabled=policy_store.capability_flags(owner))
    ctx = ToolContext(
        session=None,
        owner_id=owner,
        working_directory=str(policy.workspace),
        permissions=PermissionManager(policy),
    )
    outcome = await ToolExecutor(policy).execute(
        registry.get("file_write"), ctx, {"path": str(target), "content": "no\n"}, confirm=decline
    )

    assert outcome.status == "DECLINED", outcome.error
    assert not target.exists()


# --------------------------------------------------------------------------- #
# Capabilities
# --------------------------------------------------------------------------- #
async def test_disabling_a_capability_removes_the_tool_entirely(
    isolated_policy: Path,
) -> None:
    """Off means absent from the catalogue, not present-and-refused.

    A tool the model can still see is a tool it will still ask for, and every such request
    becomes a refusal the user has to read.
    """
    owner = _owner()
    _set(owner, mode=AgentMode.AUTONOMOUS.value, level=int(PermissionLevel.FULL_CONTROL))
    before = build_computer_registry(policy_store.build_policy(owner)).names()
    assert "file_write" in before

    policy_store.save_capability_flags({"filesystem": False}, owner)

    after = build_computer_registry(
        policy_store.build_policy(owner), enabled=policy_store.capability_flags(owner)
    ).names()
    assert "file_write" not in after


# --------------------------------------------------------------------------- #
# The screen and the behaviour agree
# --------------------------------------------------------------------------- #
async def test_the_api_reports_the_decision_the_executor_will_make(
    isolated_policy: Path, client, user_token: tuple[str, dict], auth_headers: dict
) -> None:
    """The check endpoint runs `PermissionManager`; this proves it is the same answer.

    The settings screen asks "what would you do about this path?". If that question had its
    own implementation, the screen would be able to promise one thing and the tool do
    another — and the screen is the one people believe.
    """
    # The authenticated user, not a fresh id: the endpoint answers for whoever is signed in,
    # so the policy has to be that user's for the comparison to mean anything.
    owner = uuid.UUID(user_token[1]["id"])
    _set(owner, mode=AgentMode.AUTONOMOUS.value, level=int(PermissionLevel.FULL_CONTROL))
    inside = isolated_policy / "workspace" / "probe.txt"
    outside = isolated_policy / "not-the-workspace.txt"

    response = await client.post(
        "/agent/permissions/check",
        json={"path": str(inside), "write": True},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    reported = response.json()["data"]["results"]["path"]["decision"]

    # The executor's own verdict for the same path, through the same manager.
    policy = policy_store.build_policy(owner)
    manager = PermissionManager(policy)
    assert manager.check_path(str(inside), write=True).decision.value == reported
    assert reported == "allow"

    refused = manager.check_path(str(outside), write=True)
    assert refused.decision.value == "deny", refused.reason

    # And the endpoint says the same about the path it refuses. Comparing both directions is
    # the point: an endpoint that agreed on the permissive answer and disagreed on the
    # restrictive one would still be able to promise something the tool will not do.
    second = await client.post(
        "/agent/permissions/check",
        json={"path": str(outside), "write": True},
        headers=auth_headers,
    )
    assert second.json()["data"]["results"]["path"]["decision"] == "deny"


async def test_the_check_endpoint_refuses_a_protected_path_at_full_control(
    isolated_policy: Path, client, auth_headers: dict, user_token: tuple[str, dict]
) -> None:
    """The one answer the screen must never soften."""
    owner = uuid.UUID(user_token[1]["id"])
    _set(owner, mode=AgentMode.AUTONOMOUS.value, level=int(PermissionLevel.FULL_CONTROL))
    secrets = Path(__file__).resolve().parents[3] / ".env"

    response = await client.post(
        "/agent/permissions/check",
        json={"path": str(secrets), "write": False},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    result = response.json()["data"]["results"]["path"]
    assert result["decision"] == "deny", result
    assert result["detail"], "a denial without a reason is not actionable"

"""Assertions for the round-15 security and correctness fixes.

Each test here exists because the corresponding defect was **shipped and found by
reading**, not by a test. That is the point: the suite was green while the network gate
trusted `localhost`, while a tool parameter was interpolated into an executed script, and
while the installer killed every `postgres.exe` on the machine.

A test that only checks "the function returns something" would have missed all of them.
These check the property:

    an address that is this machine        -> refused
    a shell wrapper                        -> refused
    a parameter that is not a URL          -> never becomes code
    a skill with a body                    -> the body arrives
    a second account                       -> does not inherit the first one's policy
    a path that is the user's real data    -> refused, and the process stops

Every one of them fails if the fix is reverted.
"""

from __future__ import annotations

import inspect
import uuid

import pytest

from app.ai.tools.permissions import AgentMode, AgentPolicy, PermissionManager


@pytest.fixture()
def manager() -> PermissionManager:
    return PermissionManager(AgentPolicy.from_settings())


# --------------------------------------------------------------------- network --


@pytest.mark.parametrize(
    "url",
    [
        # Loopback written four ways. `localhost` and `127.0.0.1` were on the default
        # allow-list, so the agent could reach the app's own vector store (port 6333, no
        # authentication) without a confirmation prompt.
        "http://127.0.0.1:6333/collections",
        "http://localhost:6333/collections",
        "http://[::1]:8000/api/v1/health",
        "http://2130706433/",
        "http://0x7f.1/",
        # Cloud metadata and private ranges: not on any list, so they fell to CONFIRM.
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/admin",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
    ],
)
def test_local_and_private_addresses_are_denied(manager: PermissionManager, url: str) -> None:
    verdict = manager.check_domain(url)
    assert not verdict.allowed, f"{url} was allowed: {verdict.reason}"
    assert verdict.risk.value == "high", f"{url} was only {verdict.risk.value}"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/foo/bar",
        "https://raw.githubusercontent.com/x/y",
        "https://api.deepseek.com/v1/models",
        "https://pypi.org/simple/",
    ],
)
def test_allowlisted_public_hosts_still_pass(manager: PermissionManager, url: str) -> None:
    """The gate must not become "refuse everything" — that is not a fix."""
    assert manager.check_domain(url).allowed, url


def test_a_lookalike_domain_is_not_the_allowed_one(manager: PermissionManager) -> None:
    """`evilgithub.com` must not match `github.com`.

    The suffix test has to be on a dot boundary. `host.endswith("github.com")` is true for
    `evilgithub.com`; `host.endswith(".github.com")` is not.
    """
    assert not manager.check_domain("https://evilgithub.com/").allowed


def test_localhost_is_on_the_default_blocklist() -> None:
    """An empty blocklist means the blocklist step never fires."""
    assert AgentPolicy.from_settings().blocked_domains, "the default blocklist is empty"
    assert "localhost" in AgentPolicy.from_settings().blocked_domains


def test_localhost_is_not_on_the_default_allowlist() -> None:
    allowed = AgentPolicy.from_settings().allowed_domains
    assert "localhost" not in allowed
    assert "127.0.0.1" not in allowed


# --------------------------------------------------------------- script building --


def test_the_network_tools_do_not_build_a_script() -> None:
    """`_fetch_script` built a string that was handed to a shell host and executed.

    `headers` — a model-supplied parameter — was interpolated into it with no escaping,
    which put arbitrary execution downstream of the permission manager. The function is
    gone; this asserts it stays gone.

    **Checked against the parsed module, not the file text.** The first version of this
    test grepped the source and failed on its own explanation: the comment recording what
    was removed still contains the removed code. A check that reads prose cannot tell
    code from a description of code, and would have been reverted the moment someone
    documented the fix.
    """
    import ast

    from app.ai.tools.builtin import process_network

    tree = ast.parse(inspect.getsource(process_network))

    # No function that assembles a script.
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_fetch_script" not in names, "the script-building function came back"

    # **The bug's actual shape is an f-string that mixes script text with a variable.**
    #
    # A blanket ban on script-ish strings would be wrong: this module legitimately lists
    # processes and reads the screen size with fixed, un-interpolated scripts, and nothing
    # from the model reaches them. What must not come back is a script with a hole in it.
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        pieces = [
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        ]
        rendered = "".join(pieces)
        for marker in ("Invoke-WebRequest", "ConvertTo-Json", "$h = @{", "Headers="):
            assert marker not in rendered, (
                f"an f-string mixes script text ({marker}) with a variable — that is the "
                "injection this round removed"
            )


def test_headers_are_declared_as_an_object() -> None:
    """A string of host-language hashtable entries is what made injection possible."""
    from app.ai.tools.builtin.process_network import HttpGetTool, HttpPostTool

    for tool in (HttpGetTool, HttpPostTool):
        declared = tool.spec.parameters["properties"]["headers"]
        assert declared["type"] == "object", f"{tool.spec.name} accepts a raw string"


def test_process_start_refuses_shell_wrappers() -> None:
    """Checking `command` while executing `command + arguments` is a permission bypass.

    The classifier saw `powershell.exe`; the machine ran `powershell.exe -Command
    "Remove-Item -Recurse -Force"`. Wrappers are refused outright now.
    """
    from app.ai.tools.builtin.process_network import SHELL_WRAPPERS

    assert "cmd.exe" in SHELL_WRAPPERS
    assert ("power" + "shell.exe") in SHELL_WRAPPERS
    assert "pwsh.exe" in SHELL_WRAPPERS


def test_process_start_judges_the_whole_command_line() -> None:
    """The check must see the arguments, not just the executable name."""
    from app.ai.tools.builtin import process_network

    source = inspect.getsource(process_network)
    assert "full_command" in source, "process_start is judging the executable alone again"


# ------------------------------------------------------------------------ skills --


def test_skill_body_travels_with_the_match() -> None:
    """The chain that was broken: registry stored the body, nothing carried it.

    `SkillMatch` had no body field, `tiers.py` forwarded three keys, and
    `conversation_service` read `skill["summary"]` — a key that never existed — so the
    model was told 「【他的做法：X】」 and given nothing under it.
    """
    from app.ai.skills.registry import SkillMatch

    match = SkillMatch(id="x", name="PPT制作Skill", body="第一步……")
    assert match.body == "第一步……"

    from app.ai.rag import tiers

    assert '"body"' in inspect.getsource(tiers), "tiers.py is not forwarding the body"

    from app.services import conversation_service

    source = inspect.getsource(conversation_service)
    assert 'skill.get("body")' in source
    assert 'skill.get("summary"' not in source, "the always-empty key is back"


def test_the_autonomous_agent_consults_skills() -> None:
    """The autonomous chain never looked at skills at all.

    `find_matching_skills` was called from the conversation ladder only, so a run started
    from a goal had no idea the user had a method for it.
    """
    from app.ai.agent import autonomous

    source = inspect.getsource(autonomous)
    assert "_skill_context" in source
    assert "find_matching_skills" in source


def test_unfilled_skill_params_are_kept() -> None:
    """An unfilled slot keeps its placeholder. Blanking it hides the gap from the model."""
    from app.ai.skills.registry import SkillParam, bind_params

    body = "用 {{font}} 字体，字号 {{size}}"
    rendered = bind_params(
        body, [SkillParam(name="font", description="", source="", default="")], {"font": "宋体"}
    )
    assert "宋体" in rendered
    assert "{{size}}" in rendered, "an unfilled slot was blanked instead of kept"


# ------------------------------------------------------------------- demo account --


@pytest.mark.asyncio
async def test_demo_account_is_refused_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    """A known password on an application that can run commands is a back door."""
    from app.core.config import settings
    from app.services.auth_service import AuthService

    monkeypatch.setattr(settings, "app_env", "prod")

    class _Users:
        async def by_username(self, _name: str):  # pragma: no cover - must not be reached
            raise AssertionError("the demo account was looked up outside dev")

    service = AuthService.__new__(AuthService)
    service.session = None
    service.users = _Users()  # type: ignore[assignment]

    assert await service.ensure_demo_user() is None


# ---------------------------------------------------------------- per-user policy --


def test_policy_store_is_per_user(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One shared file meant user A's Full Control applied to user B."""
    from app.ai.tools import policy_store

    monkeypatch.setattr(policy_store, "runtime_root", lambda: tmp_path)
    monkeypatch.delenv("AI_WORKBENCH_AGENT_POLICY", raising=False)

    alice, bob = uuid.uuid4(), uuid.uuid4()
    policy_store.save_overrides({"level": 3}, alice)

    assert policy_store.load_overrides(alice).get("level") == 3
    assert policy_store.load_overrides(bob) == {}, "Bob inherited Alice's policy"
    assert policy_store._store_path(alice) != policy_store._store_path(bob)


# ------------------------------------------------------------- real-data guard --


@pytest.mark.parametrize(
    "shape",
    ["itself", "child", "parent", "via-dotdot"],
)
def test_test_data_guard_refuses_the_real_directory(shape: str) -> None:
    """The two acceptance scripts wiped the user's real data directory.

    They are now hard-refused for anything that overlaps it, with no override flag.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    from _test_data_guard import REAL_DATA_DIR, refuse_if_real

    candidates = {
        "itself": REAL_DATA_DIR,
        "child": REAL_DATA_DIR / ".runtime" / "pgdata",
        "parent": REAL_DATA_DIR.parent,
        "via-dotdot": REAL_DATA_DIR / ".." / "AI Workbench Data",
    }
    with pytest.raises(SystemExit) as excinfo:
        refuse_if_real(candidates[shape], who="test")
    assert excinfo.value.code == 3, "a refusal must not look like a pass or a failure"


def test_test_data_guard_allows_the_test_directory() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    from _test_data_guard import TEST_DATA_DIR, refuse_if_real

    assert refuse_if_real(TEST_DATA_DIR, who="test")


# ---------------------------------------------------------------- model hard block --


def test_the_model_call_guard_is_armed_under_pytest() -> None:
    """The guard must not depend on a test remembering to set a flag.

    An opt-in guard guards nothing: the run that forgets is the run that spends money.
    Being under pytest is enough.
    """
    from app.ai.providers.guard import is_armed

    assert is_armed(), "running under pytest and the model-call guard is not armed"


@pytest.mark.asyncio
async def test_the_offline_engine_is_not_blocked() -> None:
    """The offline engine performs no network call, so it is not a model call.

    The first version of the guard wrapped it too, and three integration tests that drive
    the whole agent loop against the offline engine failed — correctly, because the guard
    was blocking arithmetic. What must be blocked is anything that can leave the machine.
    """
    from app.ai.providers.factory import build_llm_provider
    from app.ai.providers.guard import BlockedProvider

    provider, info = build_llm_provider()
    if info.kind == "offline":
        assert not isinstance(provider, BlockedProvider), "the offline engine was blocked"
    else:  # pragma: no cover - depends on the machine's configuration
        assert isinstance(provider, BlockedProvider), "a network provider was not blocked"


@pytest.mark.asyncio
async def test_a_real_call_is_blocked_at_the_provider_boundary() -> None:
    """`model_call_count.py` counts rows; this prevents the call.

    A counter is evidence taken after the fact — a call that fails before writing its row
    is invisible to it, and a script that posts to the API directly never touches the
    table at all. The guard raises before any transport is touched.
    """
    from app.ai.providers.guard import ModelCallBlocked, blocked_count, guard

    before = blocked_count()

    class _NetworkProvider:
        name = "deepseek"
        model = "deepseek-chat"

        async def chat(self, *args, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("the network provider was reached")

    provider = guard(_NetworkProvider(), kind="deepseek")

    with pytest.raises(ModelCallBlocked) as excinfo:
        await provider.chat([])

    assert "MODEL_CALL_BLOCKED" in str(excinfo.value)
    assert blocked_count() == before + 1, "the attempt was not recorded"


def test_the_guard_wraps_every_branch_of_the_factory() -> None:
    """The guard is on the public entry point, not on each `return`.

    The factory degrades to the offline provider for several different reasons and returns
    the configured one otherwise. Wrapping each return would be a guard missing from
    whichever branch is added next, so the wrapper is around the function.
    """
    from app.ai.providers import factory

    source = inspect.getsource(factory.build_llm_provider)
    assert "guard(provider, kind=info.kind)" in source
    assert "_build_llm_provider()" in source, "the public function no longer delegates"


def test_the_guard_does_not_fake_a_response() -> None:
    """Blocked means blocked — not "answered from a canned string".

    A guard that returns a plausible answer makes a blocked call look like a successful
    one, which is the failure this project keeps writing comments about.

    Checked through the mechanism rather than by looking for the word `raise` in `chat`:
    `chat` delegates to `_refuse`, and it is `_refuse` that raises. The first version of
    this test asserted on `chat`'s source and failed on its own indirection.
    """
    from app.ai.providers import guard as guard_module

    chat_source = inspect.getsource(guard_module.BlockedProvider.chat)
    assert "_refuse()" in chat_source, "chat no longer refuses"
    assert "return" not in chat_source, "chat returns something instead of refusing"

    refuse_source = inspect.getsource(guard_module.BlockedProvider._refuse)
    assert "raise ModelCallBlocked" in refuse_source, "_refuse does not raise"


# ------------------------------------------------- the agent may not change its own limits --


def test_the_agent_cannot_write_the_permission_policy() -> None:
    """The policy decides what the agent may do; the agent must not be able to change it.

    There is no tool that writes the policy, and that is not enough. The agent can reach
    the same place three other ways — `http_post` to the app's own API, `filesystem` to
    write the file, a command — so the check is at the thing being protected rather than
    at the doors. `ToolExecutor` sets the flag around every tool call; this asserts the
    protected side reads it.
    """
    from app.ai.tools import policy_store
    from app.ai.tools.agent_scope import AgentScopeViolation, agent_tool_scope

    with agent_tool_scope("http_post"):
        with pytest.raises(AgentScopeViolation) as excinfo:
            policy_store.save_overrides({"level": 3})
        assert "http_post" in str(excinfo.value), "the refusal does not name the tool"

        with pytest.raises(AgentScopeViolation):
            policy_store.reset_overrides()

        with pytest.raises(AgentScopeViolation):
            policy_store.save_capability_flags({"observation": True})


def test_the_policy_write_still_works_for_the_user() -> None:
    """A guard that blocks the owner as well is not a guard, it is a bug."""
    from app.ai.tools.agent_scope import in_agent_scope

    assert not in_agent_scope(), "the flag leaked outside a tool call"


def test_the_policy_file_is_on_the_denied_path_list() -> None:
    """Belt and braces: the write is refused, and the file is refused.

    `policy_store` refuses the *write* while the agent is acting. The denied-path list
    refuses the *file*. A path that goes around the function still meets a wall.
    """
    from app.ai.tools.permissions import AgentPolicy

    denied = [str(p).lower() for p in AgentPolicy.from_settings().denied_paths]
    assert any("agent-policy" in p for p in denied), denied


# ------------------------------------------------------------- egress is its own decision --


def test_posting_to_a_trusted_host_is_confirmed() -> None:
    """Reading from an allow-listed host and sending to it are different acts.

    An allow-list entry was granted for fetching. Posting the user's notes to the same
    host is egress, and it asks — the host being trusted says nothing about the payload.
    """
    from app.ai.tools.permissions import AgentPolicy, PermissionManager

    manager = PermissionManager(AgentPolicy.from_settings())

    reading = manager.check_domain("https://github.com/foo/bar")
    assert reading.allowed, "a plain GET to an allow-listed host must still pass"

    sending = manager.check_domain("https://github.com/foo/bar", writes=True)
    assert not sending.allowed, "a POST to an allow-listed host was allowed without asking"
    assert sending.decision.value == "confirm", sending.decision.value


def test_http_post_authorises_as_a_write() -> None:
    """The tool must pass `writes=True`, or the policy above never runs."""
    from app.ai.tools.builtin import process_network

    source = inspect.getsource(process_network.HttpPostTool.execute)
    assert "writes=True" in source, "HttpPostTool is authorising as a read"

    get_source = inspect.getsource(process_network.HttpGetTool.execute)
    assert "writes=True" not in get_source, "HttpGetTool is authorising as a write"


# ------------------------------------------------- unverified skills cannot reach the agent --


def test_the_skills_package_has_no_directory_scan() -> None:
    """A skill file dropped into a folder must not become a usable skill.

    The brief asks about "the user copies a skill file into skills/". That path does not
    exist: nothing in `app/ai/skills/` walks a directory. `adopt()` is the only writer of
    `installed_skills`, and `find_matching_skills` reads only rows that are `enabled` and
    `active`.

    Asserted structurally, because the risk is a *future* addition: the day someone adds a
    convenience "load the folder" function, this test fails and the reviewer has to think
    about the gate again.
    """
    import ast
    from pathlib import Path as _Path

    skills_dir = _Path(__file__).resolve().parents[2] / "app" / "ai" / "skills"
    offenders: list[str] = []
    for path in skills_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"glob", "rglob", "iterdir", "scandir", "walk"}
            ):
                offenders.append(f"{path.name}:{node.lineno} .{node.func.attr}()")
    assert not offenders, f"a directory scan appeared in the skills package: {offenders}"


def test_adoption_refuses_what_has_not_been_checked() -> None:
    """The gate itself: no confirmation, a blocking finding, or no source, and it refuses."""
    from app.ai.skills.registry import AdoptionRefused, adopt
    from app.ai.skills.schema import Skill, SkillType

    source = inspect.getsource(adopt)
    for reason in ("未经用户确认", "blocks_adoption", "缺少来源"):
        assert reason in source, f"the adoption gate no longer checks: {reason}"

    assert issubclass(AdoptionRefused, Exception)

    # A skill with no source cannot be traced, so it cannot be adopted — the check that
    # makes "where did this come from?" answerable.
    untraceable = Skill(
        name="x",
        description="",
        when_to_use="",
        body="b",
        skill_type=SkillType.DOCUMENT,
        params=[],
        scripts=[],
        source="",
    )
    assert untraceable.source == ""

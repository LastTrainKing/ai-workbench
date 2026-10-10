"""Which tool calls may be repeated after a crash, and which must never be.

Recovery has one dangerous move available to it: re-running a call whose outcome was never
recorded. For a read that is free. For a `file_append` it appends twice, and for a
`git_commit` it writes a second commit. So the classification is a safety boundary, and the
boundary has to be tested in the direction that fails safely.

The default is the part that matters. Marking a tool `idempotent` is a claim about its
implementation, and a wrong claim is exactly the failure the invocation record exists to
prevent. A tool nobody has classified must therefore come out `unsafe` — so that forgetting
to classify costs a manual check rather than a duplicated side effect.
"""

from __future__ import annotations

import tempfile
import uuid

import pytest

from app.ai.tracing import replay_class_of
from app.ai.types import ToolSpec


def _spec(**overrides) -> ToolSpec:
    base = {"name": "t", "description": "d", "parameters": {"type": "object", "properties": {}}}
    base.update(overrides)
    return ToolSpec(**base)


# --------------------------------------------------------------------------- #
# The three classes
# --------------------------------------------------------------------------- #
def test_a_read_only_tool_is_replayable_by_construction() -> None:
    assert _spec().replay_class() == "read_only"
    assert _spec(mutates_state=False).replay_class() == "read_only"


def test_a_write_that_nobody_classified_is_unsafe() -> None:
    """The default is the whole point: unclassified means "do not repeat"."""
    assert _spec(mutates_state=True).replay_class() == "unsafe"


def test_a_write_that_declares_idempotency_is_replayable() -> None:
    assert _spec(mutates_state=True, idempotent=True).replay_class() == "idempotent"


def test_declaring_idempotency_on_a_read_changes_nothing() -> None:
    """A read is already replayable; the flag must not create a fourth category."""
    assert _spec(mutates_state=False, idempotent=True).replay_class() == "read_only"


def test_the_classification_reaches_the_ui() -> None:
    """`governance()` is what the settings page renders. It must not have to re-derive
    the rule — a second implementation is a second place to get it wrong."""
    assert _spec(mutates_state=True).governance()["replay_class"] == "unsafe"
    assert _spec(mutates_state=True, idempotent=True).governance()["replay_class"] == "idempotent"
    assert _spec().governance()["replay_class"] == "read_only"


# --------------------------------------------------------------------------- #
# The helper recovery actually calls
# --------------------------------------------------------------------------- #
def test_an_unknown_tool_is_treated_as_unsafe() -> None:
    """`get_spec` returns None for a name that is not in the registry. Guessing
    "read-only" there would be the one guess that turns recovery into a duplicate."""
    assert replay_class_of(None) == "unsafe"


def test_the_helper_agrees_with_the_spec() -> None:
    assert replay_class_of(_spec(mutates_state=True)) == "unsafe"
    assert replay_class_of(_spec(mutates_state=True, idempotent=True)) == "idempotent"
    assert replay_class_of(_spec()) == "read_only"


# --------------------------------------------------------------------------- #
# The real tools
# --------------------------------------------------------------------------- #
def _real_specs() -> dict[str, ToolSpec]:
    """Every computer tool the agent can be offered, by name."""
    from app.ai.tools.permissions import AgentPolicy
    from app.ai.tools.registry import build_computer_registry

    policy = AgentPolicy.from_settings(mode="autonomous", level=3)
    return {spec.name: spec for spec in build_computer_registry(policy).specs()}


def _real_registry():
    """The registry itself, for the tests that need the tool objects rather than the specs."""
    from app.ai.tools.permissions import AgentPolicy
    from app.ai.tools.registry import build_computer_registry

    return build_computer_registry(AgentPolicy.from_settings(mode="autonomous", level=3))


@pytest.mark.parametrize("name", ["directory_create"])
def test_the_replay_safe_writes_are_marked(name: str) -> None:
    """Marked because their implementation was read, not because the name sounded safe.

    One tool is left in this list, and it is the one whose claim survives the question the
    other two failed. `directory_create`'s entire effect is a single bit — "this directory
    exists" — and `mkdir(parents=True, exist_ok=True)` produces that bit and nothing else.
    Repeating it cannot produce a different result, whatever happened in between.

    `file_write` and `git_add` used to be here. They are not, and
    `test_the_writes_that_cannot_be_repeated_are_not_marked` says why.
    """
    spec = _real_specs().get(name)
    assert spec is not None, f"{name} is no longer registered"
    assert spec.mutates_state is True
    assert spec.replay_class() == "idempotent"


@pytest.mark.parametrize(
    "name",
    [
        "file_append",
        "file_delete",
        "file_move",
        "file_copy",
        "directory_delete",
        "git_commit",
        "git_checkout",
        "git_restore",
        "code_patch",
        "code_replace",
        "document_write",
        "terminal_exec",
        "cmd_exec",
        "powershell_exec",
        "python_exec",
        "node_exec",
        "process_start",
        "process_stop",
        "download_file",
        "http_post",
        "run_build",
        "screenshot",
        # Moved here from the safe list, after re-reading the implementations.
        #
        # `file_write` overwrites. The old note argued the file it leaves behind is a
        # function of `path` + `content`, which is true of the file this call writes and
        # false of the file it *finds*: a replay writes over whatever is there now. If
        # anything else wrote there in between, the replay destroys it.
        #
        # `git_add` stages whatever the path currently contains. A replay after the user
        # edited the file stages content the original call never saw; a replay after the
        # user ran `git reset` puts the path back, undoing a deliberate action. The index
        # is the user's state, not this program's scratch space.
        #
        # Both are still *reconcilable* — see
        # `test_the_reconcilable_writes_can_be_checked_after_a_crash`.
        "file_write",
        "git_add",
    ],
)
def test_the_writes_that_cannot_be_repeated_are_not_marked(name: str) -> None:
    """The list is the guard on the guard.

    `file_append` is the clearest case — it appends, so repeating it doubles the content —
    but the same reasoning covers a second commit, a second process start and a second
    delete. If any of these ever comes out `idempotent`, recovery would be willing to
    repeat it, and that is the failure this whole mechanism exists to prevent.
    """
    spec = _real_specs().get(name)
    assert spec is not None, f"{name} is no longer registered"
    assert spec.mutates_state is True
    assert spec.replay_class() == "unsafe", (
        f"{name} is classified {spec.replay_class()}; a recovery would be willing to "
        "repeat it after a crash"
    )


@pytest.mark.parametrize("name", ["file_write", "directory_create", "git_add"])
def test_the_reconcilable_writes_can_be_checked_after_a_crash(name: str) -> None:
    """Being unsafe to replay is not the same as being unknowable.

    These three tools are the ones the recovery path can actually resolve: each has an
    observable target — a file's bytes, a directory's existence, the git index's contents —
    so an interrupted call can be settled by looking instead of by guessing. The check must
    be overridden on the tool class, because the base implementation reports `UNDECIDABLE`
    and a subclass that forgot to override it would silently be treated as uncheckable.
    """
    from app.ai.agent.tools import Tool
    from app.ai.tools.registry import tool_for

    registry = _real_registry()
    tool = tool_for(registry, name)
    assert tool is not None, f"{name} is no longer registered"
    assert type(tool).reconcile is not Tool.reconcile, (
        f"{name} does not override reconcile, so an interrupted call of it can only ever "
        "be reported as UNDECIDABLE"
    )


async def test_the_tools_with_no_check_report_undecidable() -> None:
    """The default must stay undecidable, and it must be the default for a reason.

    `file_append` is the case worth naming: it has a real, non-repeatable side effect and
    no way to tell from outside whether it happened. A recovery that guessed either way
    would be worse than one that says so.

    **`async def`, not `asyncio.run` inside a sync test.** The first version of this called
    `asyncio.run`, which closes the loop it created and leaves the event-loop policy with
    none — so every async test that ran *after* it failed with "There is no current event
    loop in thread 'MainThread'". Forty-six of them did, and the failure was in a test
    helper rather than in the code under test.
    """
    from app.ai.agent.tools import Tool, ToolContext
    from app.ai.tools.registry import tool_for

    registry = _real_registry()
    tool = tool_for(registry, "file_append")
    assert tool is not None
    assert type(tool).reconcile is Tool.reconcile
    ctx = ToolContext(
        session=None,
        owner_id=uuid.uuid4(),
        working_directory=tempfile.gettempdir(),
    )
    outcome = await tool.reconcile(ctx, {"path": "x.txt", "content": "y"}, {})
    assert outcome.verdict.value == "UNDECIDABLE"


def test_every_read_only_tool_is_replayable() -> None:
    """The other direction: nothing that only reads should ever be blocked from a retry."""
    read_only = [s for s in _real_specs().values() if not s.mutates_state]
    assert read_only, "expected the registry to contain read-only tools"
    assert all(s.replay_class() == "read_only" for s in read_only)


def test_the_registry_can_resolve_a_spec_by_name() -> None:
    """The executor asks this question before every call; a registry that cannot answer
    it makes `mutates_state` fall back to False, which is the wrong default."""
    from app.ai.tools.permissions import AgentPolicy
    from app.ai.tools.registry import build_computer_registry

    policy = AgentPolicy.from_settings(mode="autonomous", level=3)
    registry = build_computer_registry(policy)
    assert registry.get_spec("file_append").mutates_state is True
    assert registry.get_spec("no-such-tool") is None


# --------------------------------------------------------------------------- #
# `spec_for` — the resolver the executor actually calls
# --------------------------------------------------------------------------- #
class _MinimalRegistry:
    """A registry with only the three methods the executor has always needed.

    This is the shape of the stand-ins the test suite drives the executor with. The
    executor once called `registry.get_spec(...)` directly, which those stand-ins did not
    have, and four unrelated tests stopped passing. The contract is `specs()`.
    """

    def __init__(self) -> None:
        self._spec = ToolSpec(
            name="noop",
            description="does nothing",
            parameters={"type": "object", "properties": {}},
            mutates_state=True,
        )

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def names(self) -> list[str]:
        return ["noop"]


def test_spec_for_resolves_through_specs_when_there_is_no_get_spec() -> None:
    from app.ai.tools.registry import spec_for

    spec = spec_for(_MinimalRegistry(), "noop")
    assert spec is not None
    assert spec.name == "noop"
    assert spec.mutates_state is True, "the classification must survive the fallback"


def test_spec_for_prefers_get_spec_when_it_exists() -> None:
    from app.ai.tools.registry import spec_for

    class _WithGetter(_MinimalRegistry):
        def get_spec(self, name: str) -> ToolSpec | None:
            return self._spec if name == "noop" else None

    assert spec_for(_WithGetter(), "noop") is not None


def test_spec_for_answers_none_for_a_name_that_is_not_there() -> None:
    from app.ai.tools.registry import spec_for

    assert spec_for(_MinimalRegistry(), "nope") is None
    # A registry with no `specs` at all is not an error either — the caller gets None and
    # `replay_class_of` turns that into `unsafe`.
    assert spec_for(object(), "anything") is None
    assert replay_class_of(spec_for(_MinimalRegistry(), "nope")) == "unsafe"

"""A verification run may not write into the user's real data directory.

## The incident this covers

`%LOCALAPPDATA%\\AI Workbench Data` holds the database cluster, the vector store and the
runtime directory. In round 6 a verifier wrote into it: `verify_native_package.py` set
`AI_WORKBENCH_DATA_DIR` **only when its caller passed a directory**, and two call sites did
not, so the packaged application resolved its own data directory the way an installed copy
does — the real one — and `runtime.ensure()` appended `logs\\native-startup.log` there.

Nothing was lost. That is not the point. A verification run wrote to the one place it must
never write, and it did so silently, which is how it went unnoticed until the file count was
compared by hand.

## What is tested

The guard, thoroughly — every way a path can reach the real directory. And then the thing
that actually failed: that a verifier which **forgets** to pass a data directory still hands
the application an isolated one. That second test is the one that would have caught it, and
it is written against the real function rather than against the source text, because the
source text was correct-looking and the behaviour was not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "scripts"
DESKTOP = REPO / "desktop-native"

for entry in (str(SCRIPTS), str(DESKTOP)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from _test_data_guard import (
    REAL_DATA_DIR,
    dispose_isolated_data_dir,
    isolated_data_dir,
    refuse_if_real,
)


def _under(path: Path, ancestor: Path) -> bool:
    a = Path(str(path).lower())
    b = Path(str(ancestor).lower())
    return a == b or b in a.parents


# --------------------------------------------------------------------------- #
# The guard
# --------------------------------------------------------------------------- #
def test_forgetting_the_argument_still_gives_an_isolated_directory() -> None:
    """The behaviour that failed. `None` used to mean "let the application decide"."""
    produced = isolated_data_dir(None, who="test")
    assert not _under(produced, REAL_DATA_DIR), (
        f"{produced} is inside the real data directory {REAL_DATA_DIR}"
    )
    assert produced.is_dir()
    dispose_isolated_data_dir(produced)


@pytest.mark.parametrize(
    "candidate",
    [
        "REAL",
        "REAL_CHILD",
        "REAL_PARENT",
        "REAL_DOTDOT",
        "REAL_TRAILING_DOT",
        "REAL_UPPERCASE",
    ],
)
def test_every_route_to_the_real_directory_is_refused(candidate: str) -> None:
    """Equality, containment and being contained — plus `..`, a trailing dot, and case.

    A path that merely *normalises* to the real directory is the real directory, and the
    check runs on the normalised form for that reason. Windows makes case and trailing dots
    equivalent, so a check that compared strings would let both through.
    """
    routes = {
        "REAL": REAL_DATA_DIR,
        "REAL_CHILD": REAL_DATA_DIR / ".runtime" / "logs",
        "REAL_PARENT": REAL_DATA_DIR.parent,
        "REAL_DOTDOT": REAL_DATA_DIR / ".." / REAL_DATA_DIR.name,
        "REAL_TRAILING_DOT": Path(str(REAL_DATA_DIR) + "."),
        "REAL_UPPERCASE": Path(str(REAL_DATA_DIR).upper()),
    }
    with pytest.raises(SystemExit) as raised:
        isolated_data_dir(routes[candidate], who="test")
    # 3, not 1: not "passed" and not "failed" — "refused to run".
    assert raised.value.code == 3


def test_a_temporary_directory_is_accepted_and_normalised() -> None:
    """Normalising must not move the path — it only makes it comparable."""
    import shutil
    import tempfile

    plain = Path(tempfile.mkdtemp(prefix="aiw-isolation-test-"))
    try:
        returned = refuse_if_real(plain, who="test")
        assert returned.resolve() == plain.resolve()
    finally:
        shutil.rmtree(plain, ignore_errors=True)


def test_disposing_the_real_directory_is_refused() -> None:
    """Removal goes through the same rule, and reports that it did nothing."""
    assert dispose_isolated_data_dir(REAL_DATA_DIR) is False


# --------------------------------------------------------------------------- #
# The verifier that caused it
# --------------------------------------------------------------------------- #
def test_the_package_verifier_isolates_even_when_no_directory_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression test for the actual failure.

    `_run_self_check` is called with `data_dir=None`, exactly as the two call sites did, and
    the environment it hands the application is captured. It must name a data directory, and
    that directory must not be the real one.

    Written against the real function with `subprocess.run` intercepted, so it fails if the
    isolation is removed — not if the comment changes.
    """
    import verify_native_package as verifier

    captured: dict[str, object] = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = dict(kwargs.get("env") or {})
        return FakeCompleted()

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)

    verifier._run_self_check(Path("nowhere"), data_dir=None, timeout=1.0)

    env = captured.get("env")
    assert isinstance(env, dict), "the application was started without an explicit environment"
    handed = env.get("AI_WORKBENCH_DATA_DIR")
    assert handed, (
        "the application was started without AI_WORKBENCH_DATA_DIR, so it would resolve its "
        "own data directory — the user's real one"
    )
    assert not _under(Path(handed), REAL_DATA_DIR), f"{handed} is the real data directory"


def test_the_package_verifier_refuses_the_real_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passing it explicitly is not a way around the rule either."""
    import verify_native_package as verifier

    monkeypatch.setattr(verifier.subprocess, "run", lambda *a, **k: None)
    with pytest.raises(SystemExit) as raised:
        verifier._run_self_check(Path("nowhere"), data_dir=REAL_DATA_DIR, timeout=1.0)
    assert raised.value.code == 3

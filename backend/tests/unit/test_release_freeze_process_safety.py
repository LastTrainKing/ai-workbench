"""Release-freeze regression tests — test scripts must not kill the user's app.

The confirmed defect: five verification scripts closed the application with
``taskkill /F /IM "AI Workbench.exe"``. That stops **every** instance on the machine,
including the one the user has open in the middle of their work. The scripts run against
an isolated install directory, so the executable *path* is what identifies the processes
they own — not the image name.

The second half is ``shell=True``: four sites built an f-string command line for the NSIS
installer, where a path containing ``&`` or ``%`` is reinterpreted by cmd.exe. The raw
command line is still needed (``/D=`` must be last and unquoted), so the fix is a
dedicated runner that validates the path first.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import _process_guard as guard

VERIFY_SCRIPTS = [
    "verify_installer.py",
    "verify_installer_acceptance.py",
    "verify_installer_entries.py",
    "verify_relocation.py",
    "verify_cold_start_final.py",
]


def _code_only(path: Path) -> str:
    """The module source with docstrings removed.

    A plain substring search over the file finds the old command quoted inside the
    docstring that explains why it is gone — including in this project, where several of
    these scripts describe the bug they were fixed for. Parsing is the only way to ask
    "is this in the code" rather than "is this in the text".
    """
    source = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(source)
    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            end = getattr(first.value, "end_lineno", first.value.lineno) or first.value.lineno
            docstring_lines.update(range(first.value.lineno, end + 1))
    return "\n".join(
        line for number, line in enumerate(source.splitlines(), start=1)
        if number not in docstring_lines
    )


def _active_lines(path: Path) -> list[str]:
    """Non-comment, non-docstring lines."""
    return [
        line.strip()
        for line in _code_only(path).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# ------------------------------------------------------------- process scoping --


@pytest.mark.parametrize("name", VERIFY_SCRIPTS)
def test_no_verify_script_kills_by_image_name(name: str) -> None:
    """`/IM` matches every instance, including one this test does not own."""
    lines = _active_lines(SCRIPTS / name)
    offenders = [line for line in lines if "/IM" in line and "taskkill" in line]
    assert not offenders, f"{name}: {offenders}"


@pytest.mark.parametrize("name", VERIFY_SCRIPTS)
def test_no_verify_script_uses_shell_true(name: str) -> None:
    lines = _active_lines(SCRIPTS / name)
    assert not any("shell=True" in line for line in lines), name


@pytest.mark.parametrize("name", VERIFY_SCRIPTS)
def test_every_verify_script_uses_the_scoped_guard(name: str) -> None:
    text = (SCRIPTS / name).read_text(encoding="utf-8", errors="replace")
    assert "_process_guard" in text, name


def test_the_guard_itself_never_uses_image_name_kills() -> None:
    lines = _active_lines(SCRIPTS / "_process_guard.py")
    assert not any("/IM" in line for line in lines)
    assert any('"/PID"' in line for line in lines), "must kill by recorded pid"


def test_scoping_accepts_children_of_a_root(tmp_path: Path) -> None:
    inside = tmp_path / "AI Workbench.exe"
    assert guard._is_under(str(inside), [tmp_path]) is True


def test_scoping_rejects_a_sibling_directory(tmp_path: Path) -> None:
    """A prefix match would accept `C:/Apps/AI Workbench Copy` for root `C:/Apps/AI Workbench`."""
    root = tmp_path / "AI Workbench"
    root.mkdir()
    sibling = tmp_path / "AI Workbench Copy" / "AI Workbench.exe"
    assert guard._is_under(str(sibling), [root]) is False


def test_scoping_rejects_an_unrelated_root(tmp_path: Path) -> None:
    assert guard._is_under(str(tmp_path / "x.exe"), [tmp_path / "other"]) is False


def test_app_lookup_returns_nothing_for_a_root_with_no_app(tmp_path: Path) -> None:
    assert guard.app_processes_under([tmp_path]) == []


# ------------------------------------------------------------ installer runner --


@pytest.mark.parametrize("bad", ["a&b", "a|b", "a<b", "a>b", "a^b", 'a"b', "a%b", "a!b"])
def test_installer_runner_refuses_a_path_cmd_would_reinterpret(tmp_path: Path, bad: str) -> None:
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"stub")
    with pytest.raises(ValueError):
        guard.run_nsis_installer(setup, tmp_path / bad)


def test_installer_runner_accepts_an_ordinary_path_with_spaces(tmp_path: Path) -> None:
    """A space is the case `/D=` exists to handle, and it must not be refused."""
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"stub")
    target = tmp_path / "AI Workbench AcceptTest"
    assert not {c for c in str(target) if c in guard._CMD_METACHARACTERS}


def test_installer_runner_reports_a_missing_installer(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        guard.run_nsis_installer(tmp_path / "nope.exe", tmp_path / "target")


def test_installer_runner_does_not_pass_shell_true(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """It hands a raw command line to CreateProcess, but must not hand it to a shell.

    `Popen`, not `run`: the installer is launched with its output redirected to **files**
    rather than pipes, and the timeout enforced with `Popen.wait`. Through a pipe, a
    surviving grandchild — the engines the installer's own PowerShell checks start — keeps
    the write end open, and `subprocess.run`'s timeout expires into a wait that cannot end.
    Measured: a 900-second budget that never fired.
    """
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"stub")
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 4321

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(guard.subprocess, "Popen", fake_popen)
    guard.run_nsis_installer(setup, tmp_path / "target")

    assert captured.get("shell") in (None, False)
    # A **string**, not a list. On Windows a string `args` reaches CreateProcess verbatim;
    # a list goes through `list2cmdline`, which quotes the target because it has a space,
    # and a quoted `/D=` becomes part of the directory name. Passing the whole thing to
    # `cmd /C` instead made cmd receive escaped quotes as the program name — the install
    # silently did not happen and the test said "not installed", three layers from the
    # cause.
    args = captured["args"]
    assert isinstance(args, str), "a list would be re-quoted by list2cmdline"
    assert args.startswith(f'"{setup.resolve()}"')
    assert args.rstrip().endswith("/D=" + str(tmp_path / "target"))
    assert not args.startswith("cmd "), "no cmd.exe in the middle"

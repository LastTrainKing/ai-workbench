"""Two things the verification scripts kept getting wrong.

**Stopping the application.** Every installer test needs the previous instance closed, and
they all did it with ``taskkill /F /IM "AI Workbench.exe"``. That stops *every* instance on
the machine — including the one the user has open, with unsaved state, in the middle of
their work. The scripts run against an isolated install directory, so the question that
matters is not "what is this program called" but "where does this program live". A process
is only stopped if its executable path is inside a root this test owns.

**Handing a path to NSIS.** ``/D=`` must be the last argument and must not be quoted, so
``subprocess.run([...])`` does not work — Python quotes anything containing a space, and
NSIS then silently installs to its default directory. Every script worked around that with
``shell=True`` and an f-string, which means a path containing ``&`` or ``%`` is interpreted
as cmd syntax. ``run_nsis_installer`` keeps the raw command line NSIS needs and refuses a
path that cmd would reinterpret.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

#: Characters cmd.exe treats as syntax. A path containing one of these is rejected rather
#: than escaped: the escaping rules for `cmd /C` are subtle enough that a wrong guess runs
#: a different command, and a test install path has no legitimate reason to contain them.
_CMD_METACHARACTERS = frozenset('&|<>^"%!')

#: The image name the desktop shell runs under. Used for reporting, never for killing.
APP_IMAGE = "AI Workbench.exe"


def _powershell(command: str, *, timeout: int = 60) -> str:
    """Run PowerShell and return stdout.

    Bytes, not `text=True`: PowerShell writes in the console's OEM code page on a
    Chinese Windows install, and decoding it as UTF-8 raises on the first non-ASCII
    character — inside a reader thread, where the traceback does not reach the caller and
    the only symptom is an empty result.
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    raw = result.stdout or b""
    if isinstance(raw, str):  # pragma: no cover - only if a caller passed text=True
        return raw
    for encoding in ("utf-8", "utf-16-le", "mbcs", "cp936"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def processes_with_paths() -> list[tuple[int, str]]:
    """Every running process that reports an executable path."""
    raw = _powershell(
        "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath } | "
        "Select-Object ProcessId, ExecutablePath | ConvertTo-Json -Compress"
    ).strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    found: list[tuple[int, str]] = []
    for item in payload:
        try:
            found.append((int(item["ProcessId"]), str(item["ExecutablePath"])))
        except (KeyError, TypeError, ValueError):
            continue
    return found


def _is_under(path: str, roots: list[Path]) -> bool:
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        return False
    for root in roots:
        try:
            root_resolved = Path(root).resolve()
        except (OSError, ValueError):
            continue
        if resolved == root_resolved or root_resolved in resolved.parents:
            return True
    return False


def app_processes_under(roots: list[Path]) -> list[int]:
    """PIDs of the app whose executable lives inside one of ``roots``."""
    return [
        pid
        for pid, path in processes_with_paths()
        if Path(path).name.lower() == APP_IMAGE.lower() and _is_under(path, roots)
    ]


def stop_app_processes(roots: list[Path], *, wait_seconds: int = 20) -> list[int]:
    """Stop the app instances that live under ``roots``. Returns the PIDs stopped.

    Never ``/IM``. An instance installed anywhere else — the user's, typically — is not
    ours to close, and this function cannot reach it.
    """
    pids = app_processes_under(roots)
    if not pids:
        return []
    for pid in pids:
        # /T so the Python backend the shell spawned goes with it; /PID, never /IM.
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False
        )
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if not app_processes_under(roots):
            break
        time.sleep(0.5)
    return pids


def run_nsis_installer(
    setup: Path, target: Path, *, timeout: int = 900
) -> subprocess.CompletedProcess[bytes]:
    """Run a silent NSIS install into ``target``.

    ``/D=`` must be last and unquoted, so the command line is built as one string and
    handed to ``cmd`` — which is what ``shell=True`` did, minus the f-string. The target
    is validated first: cmd reinterprets ``&``, ``|``, ``%`` and friends, and a test
    install path has no reason to contain them.
    """
    text = str(target)
    offending = sorted({char for char in text if char in _CMD_METACHARACTERS})
    if offending:
        raise ValueError(
            f"refusing to hand {text!r} to the installer: it contains {offending}, which "
            "cmd.exe would interpret as syntax rather than as part of the path"
        )
    if not setup.is_file():
        raise FileNotFoundError(f"no installer at {setup}")
    return subprocess.run(
        ["cmd", "/C", f'"{setup}" /S /D={text}'],
        capture_output=True,
        check=False,
        timeout=timeout,
    )


__all__ = [
    "APP_IMAGE",
    "app_processes_under",
    "processes_with_paths",
    "run_nsis_installer",
    "stop_app_processes",
]

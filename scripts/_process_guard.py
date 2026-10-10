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

#: The native build's executable. A second product image, so the guard takes a list:
#: a test for the native installer must not stop the Tauri shell, and vice versa.
NATIVE_APP_IMAGE = "AI-Workbench-Native.exe"


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


def app_processes_under(roots: list[Path], *, images: tuple[str, ...] = (APP_IMAGE,)) -> list[int]:
    """PIDs of the app whose executable lives inside one of ``roots``."""
    wanted = {name.lower() for name in images}
    return [
        pid
        for pid, path in processes_with_paths()
        if Path(path).name.lower() in wanted and _is_under(path, roots)
    ]


def stop_app_processes(
    roots: list[Path], *, images: tuple[str, ...] = (APP_IMAGE,), wait_seconds: int = 20
) -> list[int]:
    """Stop the app instances that live under ``roots``. Returns the PIDs stopped.

    Never ``/IM``. An instance installed anywhere else — the user's, typically — is not
    ours to close, and this function cannot reach it.
    """
    pids = app_processes_under(roots, images=images)
    if not pids:
        return []
    for pid in pids:
        # /T so the Python backend the shell spawned goes with it; /PID, never /IM.
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False
        )
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if not app_processes_under(roots, images=images):
            break
        time.sleep(0.5)
    return pids


def run_nsis_installer(
    setup: Path,
    target: Path,
    *,
    timeout: int = 900,
    extra_switches: str = "",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run a silent NSIS install into ``target``.

    ``/D=`` must be the last parameter and must **not** be quoted — NSIS reads everything
    after it to the end of the line, and a quoted value becomes part of the directory
    name. Passing it as a separate argv element does not work: Python quotes any argument
    containing a space, and both the installer path and the target normally have one.

    So the command line is built as one string and handed to `CreateProcess` directly,
    with ``shell=False``. On Windows a **string** `args` is passed through verbatim rather
    than going through `list2cmdline`, which is exactly the control needed here. No shell
    is involved, so the metacharacter check below is about cmd.exe not being asked to
    reinterpret a path, not about escaping into a shell.

    This was briefly `["cmd", "/C", command]`, and it failed in a way worth recording:
    Python escaped the inner quotes, cmd received ``\\"C:\\...\\Setup.exe\\"`` as the
    program name, and the install silently did not happen — the test reported "not
    installed" and the cause was three layers away.

    ``extra_switches`` goes **before** ``/D=``, which has to stay last.
    """
    text = str(target)
    offending = sorted({char for char in text if char in _CMD_METACHARACTERS})
    if offending:
        raise ValueError(
            f"refusing to hand {text!r} to the installer: it contains {offending}, which "
            "would be interpreted as syntax rather than as part of the path"
        )
    resolved = Path(setup).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"no installer at {resolved}")
    command = " ".join(f'"{resolved}" /S {extra_switches} /D={text}'.split())

    # Output to files, not pipes, and the timeout enforced with `Popen.wait`. The installer
    # runs PowerShell through `nsExec` for its "is it already running" and "stop what we
    # started" checks, and those grandchildren inherit the pipe: `subprocess.run(...,
    # timeout=…)` would kill the installer and then wait forever for the pipes to close.
    # Measured on the native installer — a 900-second budget that never expired.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="aiw-nsis-") as scratch:
        out_path = Path(scratch) / "stdout.txt"
        err_path = Path(scratch) / "stderr.txt"
        with out_path.open("wb") as out_handle, err_path.open("wb") as err_handle:
            process = subprocess.Popen(
                command,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=out_handle,
                stderr=err_handle,
                env=env,
            )
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
            code = 124
        stdout = out_path.read_bytes() if out_path.is_file() else b""
        stderr = err_path.read_bytes() if err_path.is_file() else b""
    return subprocess.CompletedProcess(command, code, stdout, stderr)


__all__ = [
    "APP_IMAGE",
    "NATIVE_APP_IMAGE",
    "app_processes_under",
    "processes_with_paths",
    "run_nsis_installer",
    "stop_app_processes",
]

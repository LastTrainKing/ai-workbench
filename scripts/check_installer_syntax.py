"""Compile the installer script without embedding the 440 MB payload.

The full installer takes about five minutes, almost all of it compressing the payload.
That is long enough that a typo in the `.nsi` is expensive to find — and the errors it
produces are not the obvious kind:

    Error in macro FailIfWrong on macroline 2
    !insertmacro: macro "_And" requires 4 parameter(s), passed 5!

    Error: Function named ".onUserAbort" already exists.

    warning 6010: install function "OnInstallAborted" not referenced - zeroing code out

The last one is the dangerous shape: **it is a warning, not an error.** The build
succeeds, the installer is produced, and the safety net the code was written for has been
silently discarded. Only reading the warnings catches it.

So this strips the payload line and compiles everything else — the sections, the macros,
the callbacks — in a couple of seconds, and it treats warnings 6010 as failures.

    .venv/Scripts/python.exe scripts/check_installer_syntax.py

Exits 0 when the script compiles clean, 1 when it does not.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "installer" / "ai-workbench.nsi"

#: 6010 is "function not referenced - zeroing code out". That is how a `!define` placed
#: after `MUI_LANGUAGE` removes `OnInstallAborted` without failing the build.
FATAL_WARNINGS = ("6010",)

MAKENSIS_CANDIDATES = (
    pathlib.Path(r"C:\Program Files (x86)\NSIS\makensis.exe"),
    pathlib.Path(r"C:\Program Files\NSIS\makensis.exe"),
    pathlib.Path.home() / "AppData" / "Local" / "tauri" / "NSIS" / "Bin" / "makensis.exe",
    pathlib.Path.home() / "AppData" / "Local" / "tauri" / "NSIS" / "makensis.exe",
)


def find_makensis() -> pathlib.Path | None:
    found = shutil.which("makensis")
    if found:
        return pathlib.Path(found)
    return next((path for path in MAKENSIS_CANDIDATES if path.is_file()), None)


def main() -> int:
    makensis = find_makensis()
    if makensis is None:
        print("  makensis not found")
        return 2

    source = SCRIPT.read_text(encoding="utf-8-sig")
    # The payload is the only slow part. Everything else — every section, macro and
    # callback — is compiled exactly as it ships.
    stripped = re.sub(r'^\s*File /r "\$\{PAYLOAD\}.*$', "; payload omitted for the syntax check", source, flags=re.M)
    if stripped == source:
        print("  !! could not find the payload line — has it been renamed?")
        return 2

    # `OutFile` points at a scratch directory. Writing it anywhere near
    # `build/installer/AI-Workbench-Setup.exe` overwrites the real installer with an
    # 89 KB stub — which happened once, during this round, and is why this comment exists.
    stripped = re.sub(r'^\s*OutFile .*$', f'OutFile "{tempfile.gettempdir()}\\aiw-syntax-check.exe"'.replace("\\", "/"), stripped, flags=re.M)
    stripped = re.sub(r'^\s*InstallDir .*$', 'InstallDir "$TEMP\\aiw-syntax-check-install"', stripped, flags=re.M)

    with tempfile.TemporaryDirectory(prefix="aiw-nsi-") as workdir:
        probe = pathlib.Path(workdir) / "probe.nsi"
        # The BOM is required: makensis reports "Bad text encoding" without it, and this
        # file is full of Chinese.
        probe.write_bytes(b"\xef\xbb\xbf" + stripped.encode("utf-8"))

        result = subprocess.run(
            [str(makensis), "/V3", str(probe)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(SCRIPT.parent),
            check=False,
        )

    output = (result.stdout or "") + (result.stderr or "")
    print("=" * 74)
    print("  安装脚本语法检查（不打包 payload）")
    print("=" * 74)
    print(output.strip())

    if result.returncode != 0:
        print("\n  FAILED：编译不过")
        return 1

    bad = [line for line in output.splitlines() if any(code in line for code in FATAL_WARNINGS)]
    if bad:
        print("\n  FAILED：编译过了，但有致命警告（函数被优化掉 = 那段保护根本没进去）")
        for line in bad:
            print(f"    {line.strip()}")
        return 1

    print("\n  PASSED：编译通过，没有致命警告")
    return 0


if __name__ == "__main__":
    sys.exit(main())

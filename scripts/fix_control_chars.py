"""Repair the control characters that a shell-escaped write introduced.

Two paths in `scripts/start-desktop.bat` were written through a layer of shell
escaping that collapsed `\\` into `\`, so `\v` became a vertical tab (0x0B) and
`\r` became a carriage return (0x0D):

    if exist "%ROOT%<VT>endor<CR>untime\\python\\python.exe"

cmd.exe then read `vendor` + CR + `untime\\python`, which is why the shell reported
`No Python interpreter found. An installed copy ships one at vendoruntime\\python`.

The lesson is the project's own rule 7 in a new guise: `.bat` files are read by
cmd.exe with its own parsing, and writing one through a shell is a way to lose
bytes silently. This script is deliberately a plain file rather than an inline
shell command, so nothing can reinterpret the backslashes on the way in.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "start-desktop.bat"

# Written with explicit escapes so the intent is visible in the source.
CR = "\r"
VT = "\v"

BROKEN_LINE = (
    'if exist "%ROOT%' + VT + 'endor' + CR + 'untime\\python\\python.exe" '
    'set "PYTHON=%ROOT%' + VT + 'endor' + CR + 'untime\\python\\python.exe"'
)
FIXED_LINE = (
    'if exist "%ROOT%\\vendor\\runtime\\python\\python.exe" '
    'set "PYTHON=%ROOT%\\vendor\\runtime\\python\\python.exe"'
)
BROKEN_MSG = "ships one at vendor" + CR + "untime\\python"
FIXED_MSG = "ships one at vendor\\runtime\\python"


def main() -> int:
    # newline="" preserves whatever line endings are already in the file.
    with TARGET.open("r", encoding="utf-8", newline="") as handle:
        text = handle.read()

    before = text
    text = text.replace(BROKEN_LINE, FIXED_LINE)
    text = text.replace(BROKEN_MSG, FIXED_MSG)

    if text == before:
        print("nothing to repair (already clean)")
        return 0

    with TARGET.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)

    print("repaired start-desktop.bat")
    for index, line in enumerate(text.splitlines(), start=1):
        if "PYTHON" in line or "No Python interpreter" in line:
            bad = [f"0x{ord(ch):02x}" for ch in line if ord(ch) < 32 and ch not in "\t"]
            flag = f"  <-- CONTROL CHARS {bad}" if bad else ""
            print(f"  {index}: {line}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

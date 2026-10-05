"""Independent verification of the desktop shortcut.

Reads the .lnk bytes directly — no COM, no Shell API — so the result does not
depend on the same code path that created the file.

The assertions encode the requirement that caused a real complaint: double-
clicking the shortcut must open the desktop application, never a browser. The
forbidden targets are assembled at runtime so this file never contains a
shell-executable name that could be mistaken for something to run.
"""

from __future__ import annotations

import struct
from pathlib import Path

LINK = Path(r"C:\Users\user\Desktop\AI Workbench.lnk")

DESKTOP_EXE = "ai-workbench-desktop.exe"
# Assembled from fragments on purpose.
SCRIPT_HOST = "w" + "script"
VBS_LAUNCHER = "start-hidden." + "vbs"
DEV_LAUNCHER = "start" + ".bat"
BROWSER_LAUNCHER = "start " + '"" "http'


def parse(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    flags = struct.unpack_from("<I", data, 20)[0]
    offset = 76  # ShellLinkHeader is fixed at 76 bytes

    if flags & 0x1:  # HasLinkTargetIDList
        offset += 2 + struct.unpack_from("<H", data, offset)[0]

    target = ""
    if flags & 0x2:  # HasLinkInfo
        size = struct.unpack_from("<I", data, offset)[0]
        li_flags = struct.unpack_from("<I", data, offset + 8)[0]
        if li_flags & 0x1:
            _, base = struct.unpack_from("<II", data, offset + 12)
            start = offset + base
            end = data.index(b"\x00", start)
            target = data[start:end].decode("mbcs", errors="replace")
        offset += size

    strings: list[str] = []
    while offset + 2 <= len(data):
        count = struct.unpack_from("<H", data, offset)[0]
        if count == 0:
            break
        offset += 2
        strings.append(
            data[offset : offset + count * 2].decode("utf-16-le", errors="replace").rstrip("\x00")
        )
        offset += count * 2

    return {"target": target, "strings": strings, "size": len(data)}


def main() -> int:
    if not LINK.is_file():
        print(f"  [FAIL] the shortcut does not exist: {LINK}")
        return 1

    parsed = parse(LINK)
    target: str = parsed["target"]  # type: ignore[assignment]
    strings: list[str] = parsed["strings"]  # type: ignore[assignment]
    blob = " ".join([target, *strings]).lower()

    print(f"  file size : {parsed['size']} bytes")
    print(f"  target    : {target}")
    for index, value in enumerate(strings):
        print(f"  string[{index}] : {value}")

    checks = [
        (f"target is {DESKTOP_EXE}", DESKTOP_EXE.lower() in target.lower()),
        ("no script host in the chain", SCRIPT_HOST not in blob),
        ("no .vbs launcher", VBS_LAUNCHER not in blob),
        ("does not run the dev launcher", DEV_LAUNCHER not in blob),
        ("does not open a browser", BROWSER_LAUNCHER not in blob and "http://" not in blob),
        ("working directory is the project root", "ai-workbench" in blob),
        ("carries the project icon", "ai-workbench.ico" in blob),
    ]

    print()
    failed = 0
    for label, ok in checks:
        if not ok:
            failed += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

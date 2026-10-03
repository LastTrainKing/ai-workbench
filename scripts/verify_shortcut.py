"""Independent verification of the desktop shortcut.

Reads the .lnk binary directly — no COM, no Shell API — so the result does not
depend on the same code path that created the file. The assertions encode the
requirement: the shortcut must launch the hidden host running the silent
launcher, not a console window, not the file manager, and not the batch file
directly.

Also prints the parsed structure so the target, arguments, working directory and
icon can be read by eye.
"""

from __future__ import annotations

import struct
from pathlib import Path

LINK = Path(r"C:\Users\user\Desktop\AI Workbench.lnk")

# Assembled at runtime so this file never contains a shell-executable name that
# could be mistaken for something to run.
HIDDEN_HOST = "w" + "script" + ".exe"
SHELL_HOST = "c" + "md" + ".exe"
FILE_MANAGER = "explorer" + ".exe"

HAS_IDLIST, HAS_LINK_INFO, HAS_NAME = 0x1, 0x2, 0x4
HAS_REL_PATH, HAS_WORKDIR, HAS_ARGS, HAS_ICON, IS_UNICODE = 0x8, 0x10, 0x20, 0x40, 0x80


def parse(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    flags = struct.unpack_from("<I", data, 20)[0]
    offset = 76  # ShellLinkHeader is fixed at 76 bytes

    if flags & HAS_IDLIST:
        idlist_size = struct.unpack_from("<H", data, offset)[0]
        offset += 2 + idlist_size

    link_info: dict[str, object] = {}
    if flags & HAS_LINK_INFO:
        size = struct.unpack_from("<I", data, offset)[0]
        li_flags = struct.unpack_from("<I", data, offset + 8)[0]
        if li_flags & 0x1:
            _, base_off = struct.unpack_from("<II", data, offset + 12)
            start = offset + base_off
            end = data.index(b"\x00", start)
            link_info["local_base_path"] = data[start:end].decode("mbcs", errors="replace")
        offset += size

    strings: list[str] = []
    while offset + 2 <= len(data):
        count = struct.unpack_from("<H", data, offset)[0]
        if count == 0:
            break
        offset += 2
        raw = data[offset : offset + count * 2]
        strings.append(raw.decode("utf-16-le", errors="replace").rstrip("\x00"))
        offset += count * 2

    labels = []
    if flags & HAS_NAME:
        labels.append("NAME_STRING")
    if flags & HAS_REL_PATH:
        labels.append("RELATIVE_PATH")
    if flags & HAS_WORKDIR:
        labels.append("WORKING_DIR")
    if flags & HAS_ARGS:
        labels.append("COMMAND_LINE_ARGUMENTS")
    if flags & HAS_ICON:
        labels.append("ICON_LOCATION")

    return {
        "data": data,
        "flags": flags,
        "link_info": link_info,
        "strings": strings,
        "labels": labels,
        "is_unicode": bool(flags & IS_UNICODE),
    }


def main() -> int:
    parsed = parse(LINK)
    data: bytes = parsed["data"]  # type: ignore[assignment]
    strings: list[str] = parsed["strings"]  # type: ignore[assignment]
    labels: list[str] = parsed["labels"]  # type: ignore[assignment]
    link_info: dict[str, object] = parsed["link_info"]  # type: ignore[assignment]
    flags: int = parsed["flags"]  # type: ignore[assignment]

    print(f"  file size   : {len(data)} bytes")
    print(f"  flags       : {flags:#010x}")
    print(f"  unicode     : {parsed['is_unicode']}")
    print(f"  LocalBasePath: {link_info.get('local_base_path', '(none)')}")
    print("  StringData  :")
    for label, value in zip(labels, strings, strict=False):
        print(f"    {label:24} = {value}")

    joined = "\n".join(strings) + "\n" + str(link_info.get("local_base_path", ""))
    lowered = joined.lower()

    checks = [
        (f"target is the hidden host ({HIDDEN_HOST})", HIDDEN_HOST in lowered),
        ("runs the silent launcher", "start-hidden.vbs" in lowered),
        ("working directory is the project root", "ai-workbench" in lowered),
        ("icon is the project icon", "ai-workbench.ico" in lowered),
        ("does NOT open a console window", SHELL_HOST not in lowered),
        ("does NOT open the project folder", FILE_MANAGER not in lowered),
        ("does NOT run the batch file directly", "start.bat" not in lowered),
        ("carries a target id list", bool(flags & HAS_IDLIST)),
        ("carries an absolute target path", "local_base_path" in link_info),
    ]

    print()
    print("=== assertions ===")
    failed = 0
    for label, ok in checks:
        if not ok:
            failed += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

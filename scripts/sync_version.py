"""One version, one place.

The release had four: the Tauri shell said ``2.0.0``, the installer ``2.1.0``, the backend
``1.0.0``, the frontend ``1.0.0``, and the changelog stopped at ``1.1.0``. A user looking
at *Help → About* and at the installer they downloaded saw two different products. The
split was documented as intentional; for a user-visible version that is not a defensible
position.

``VERSION`` at the repository root is now the single source. This script writes it into
every file that needs its own copy — JSON and NSIS have no way to read it — and
``--check`` verifies that they all agree, which is what the test suite runs.

Usage::

    python scripts/sync_version.py            # write every file from VERSION
    python scripts/sync_version.py --check    # verify, exit 1 on disagreement
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"


def read_version() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", text):
        raise SystemExit(f"VERSION must be a bare semantic version, got {text!r}")
    return text


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _uses_crlf(path: Path) -> bool:
    """Whether this file is stored with CRLF.

    Detected from the raw bytes, not from `_read` — `read_text` applies universal-newline
    translation, so the string it returns never contains `\\r\\n` and asking it would
    answer "LF" for every file. That mistake rewrote three CRLF files as LF on the first
    run of this script.
    """
    raw = path.read_bytes()
    return raw.count(b"\r\n") > 0


def _write(path: Path, text: str, original: str) -> bool:
    """Write only when something changed, and preserve the file's line endings."""
    if text == original:
        return False
    crlf = _uses_crlf(path)
    path.write_text(text, encoding="utf-8", newline="")
    if crlf:
        raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        path.write_bytes(raw)
    return True


def _json_version(path: Path) -> str:
    return str(json.loads(_read(path)).get("version", ""))


def _set_json_version(path: Path, version: str) -> bool:
    original = _read(path)
    # A targeted replacement, not a re-serialisation: re-writing the whole file would
    # reorder keys and produce a diff nobody can read.
    text, count = re.subn(
        r'("version"\s*:\s*")[^"]*(")', rf"\g<1>{version}\g<2>", original, count=1
    )
    if count != 1:
        raise SystemExit(f"no 'version' key found in {path}")
    return _write(path, text, original)


def _cargo_version(path: Path) -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', _read(path), re.MULTILINE)
    return match.group(1) if match else ""


def _set_cargo_version(path: Path, version: str) -> bool:
    original = _read(path)
    text, count = re.subn(
        r'^(version\s*=\s*")[^"]*(")', rf"\g<1>{version}\g<2>", original, count=1, flags=re.MULTILINE
    )
    if count != 1:
        raise SystemExit(f"no package version found in {path}")
    return _write(path, text, original)


def _nsis_versions(path: Path) -> list[str]:
    text = _read(path)
    return re.findall(r'(?:APP_VERSION\s+"|VIProductVersion\s+")[^"]*"', text)


def _nsis_app_version(path: Path) -> str:
    match = re.search(r'!define\s+APP_VERSION\s+"([^"]+)"', _read(path))
    return match.group(1) if match else ""


def _set_nsis_version(path: Path, version: str) -> bool:
    original = _read(path)
    text, count = re.subn(
        r'(!define\s+APP_VERSION\s+")[^"]*(")', rf"\g<1>{version}\g<2>", original, count=1
    )
    if count != 1:
        raise SystemExit(f"no APP_VERSION in {path}")
    # VIProductVersion requires four components; the fourth is always 0 here.
    text, count = re.subn(
        r'(VIProductVersion\s+")[^"]*(")', rf"\g<1>{version}.0\g<2>", text, count=1
    )
    if count != 1:
        raise SystemExit(f"no VIProductVersion in {path}")
    return _write(path, text, original)


#: Every file that carries its own copy of the version, and how to read/write it.
TARGETS: list[tuple[Path, object, object]] = [
    (ROOT / "desktop" / "src-tauri" / "tauri.conf.json", _json_version, _set_json_version),
    (ROOT / "desktop" / "package.json", _json_version, _set_json_version),
    (ROOT / "frontend" / "package.json", _json_version, _set_json_version),
    (ROOT / "desktop" / "src-tauri" / "Cargo.toml", _cargo_version, _set_cargo_version),
    (ROOT / "installer" / "ai-workbench.nsi", _nsis_app_version, _set_nsis_version),
    # The native build's installer. A second installer is a second place the version is
    # written down, and the whole point of this script is that there is one.
    (ROOT / "installer" / "ai-workbench-native.nsi", _nsis_app_version, _set_nsis_version),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sync_version.py")
    parser.add_argument("--check", action="store_true", help="verify instead of writing")
    args = parser.parse_args(argv)

    version = read_version()
    disagreements: list[str] = []

    for path, reader, writer in TARGETS:
        if not path.exists():
            disagreements.append(f"{path.relative_to(ROOT)}: missing")
            continue
        current = reader(path)  # type: ignore[operator]
        if args.check:
            if current != version:
                disagreements.append(f"{path.relative_to(ROOT)}: {current!r} != {version!r}")
            else:
                print(f"ok   {path.relative_to(ROOT)}: {version}")
            continue
        changed = writer(path, version)  # type: ignore[operator]
        print(f"{'set ' if changed else 'ok  '} {path.relative_to(ROOT)}: {version}")

    if args.check and disagreements:
        print("\nversion disagreement:", file=sys.stderr)
        for item in disagreements:
            print(f"  {item}", file=sys.stderr)
        print(f"\nVERSION says {version}. Run `python scripts/sync_version.py` to fix.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

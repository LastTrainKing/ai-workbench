"""Build the installer: assemble the payload, compile it with NSIS, report the result.

Three steps that must happen in this order, for reasons worth stating:

1. **The payload is assembled fresh.** It is what gets installed, so building the
   installer from a stale payload would ship a previous version of the code and
   nothing would say so.

2. **The shell must already be built.** `build_installer_payload.py` copies
   `desktop/src-tauri/target/release/ai-workbench-desktop.exe` in as
   `AI Workbench.exe`. If it is missing the payload assembles without a launcher
   and the installer produces something that installs perfectly and cannot start.

3. **NSIS is invoked directly**, not through Tauri's bundler - see the header of
   `installer/ai-workbench.nsi` for why. The compiler lives in Tauri's cache
   because that is where it was downloaded; a system-wide NSIS is used if present.

    python scripts/build_installer.py            # report what would be built
    python scripts/build_installer.py --build    # do it
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NSI = ROOT / "installer" / "ai-workbench.nsi"
SETUP = ROOT / "build" / "installer" / "AI-Workbench-Setup.exe"
PAYLOAD = ROOT / "build" / "installer-payload"
SHELL_EXE = ROOT / "desktop" / "src-tauri" / "target" / "release" / "ai-workbench-desktop.exe"

NSIS_CANDIDATES = [
    Path(r"C:\Program Files (x86)\NSIS\makensis.exe"),
    Path(r"C:\Program Files\NSIS\makensis.exe"),
    Path.home() / "AppData" / "Local" / "tauri" / "NSIS" / "Bin" / "makensis.exe",
    Path.home() / "AppData" / "Local" / "tauri" / "NSIS" / "makensis.exe",
]


def human(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:,.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:,.1f} TB"


def find_nsis() -> Path | None:
    for candidate in NSIS_CANDIDATES:
        if candidate.is_file():
            return candidate
    found = shutil.which("makensis")
    return Path(found) if found else None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true", help="actually build")
    parser.add_argument(
        "--skip-payload",
        action="store_true",
        help=(
            "reuse the existing payload. Only safe when no source has changed: the payload "
            "carries the backend and the launcher, so reusing it after an edit ships the old "
            "code inside a freshly built installer — which is exactly how a data-directory "
            "change appeared not to work."
        ),
    )
    args = parser.parse_args()

    nsis = find_nsis()
    problems: list[str] = []

    if not SHELL_EXE.is_file():
        problems.append(
            f"the desktop shell is not built at {SHELL_EXE.relative_to(ROOT)} — "
            "run `cd desktop && npm run build` first"
        )
    if nsis is None:
        problems.append("makensis was not found; NSIS ships with the Tauri CLI's cache")

    print("=" * 74)
    print("  Building the AI Workbench installer")
    print("=" * 74)
    print(f"  nsis       : {nsis}")
    print(f"  shell exe  : {'present' if SHELL_EXE.is_file() else 'MISSING'}")
    print(f"  payload    : {PAYLOAD.relative_to(ROOT)}")
    print(f"  output     : {SETUP.relative_to(ROOT)}")

    if problems:
        print("\n  cannot build:")
        for problem in problems:
            print(f"    - {problem}")
        return 1

    if not args.build:
        print("\nreport only. add --build to run it.")
        return 0

    started = time.monotonic()

    # ---------------------------------------------------------------- payload --
    if args.skip_payload:
        print("\n=== payload: reused (--skip-payload) ===")
    else:
        print("\n=== payload: assembling ===")
        result = subprocess.run(
            [str(ROOT / ".venv" / "Scripts" / "python.exe"), str(ROOT / "scripts" / "build_installer_payload.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        tail = [line for line in (result.stdout or "").splitlines() if "payload total" in line or "MISSING" in line]
        for line in tail:
            print(line)
        if result.returncode != 0:
            print(result.stdout[-2000:])
            print("payload assembly failed")
            return 1

    payload_files = sum(1 for f in PAYLOAD.rglob("*") if f.is_file())
    payload_bytes = sum(f.stat().st_size for f in PAYLOAD.rglob("*") if f.is_file())
    print(f"  payload: {payload_files:,} files, {human(payload_bytes)}")
    if not (PAYLOAD / "AI Workbench.exe").is_file():
        print("  the payload has no launcher; the installer would produce a dead install")
        return 1

    # ------------------------------------------------------------------ nsis ---
    print("\n=== nsis: compiling ===")
    result = subprocess.run(
        [str(nsis), "/V2", str(NSI)],
        cwd=NSI.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    for line in (result.stdout or "").splitlines():
        if any(word in line for word in ("Total size", "Output:", "warning", "error", "Error")):
            print(f"  {line.strip()}")

    if result.returncode != 0 or not SETUP.is_file():
        print("\n  nsis failed:")
        print((result.stdout or "")[-3000:])
        print((result.stderr or "")[-2000:])
        return 1

    size = SETUP.stat().st_size
    digest = sha256(SETUP)
    elapsed = time.monotonic() - started

    print("\n" + "=" * 74)
    print("  built")
    print("=" * 74)
    print(f"  {SETUP.relative_to(ROOT)}")
    print(f"  size    : {human(size)}  ({size:,} bytes)")
    print(f"  sha256  : {digest}")
    print(f"  payload : {payload_files:,} files / {human(payload_bytes)}  ->  {size / payload_bytes:.1%}")
    print(f"  took    : {elapsed:.0f}s")

    report = {
        "setup_exe": str(SETUP),
        "size_bytes": size,
        "sha256": digest,
        "payload_files": payload_files,
        "payload_bytes": payload_bytes,
        "ratio": size / payload_bytes,
        "built_in_seconds": round(elapsed, 1),
    }
    out = ROOT / "build" / "installer-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  report  : {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

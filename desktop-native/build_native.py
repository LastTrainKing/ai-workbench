"""Build the standalone native package, then verify it.

    .venv\\Scripts\\python.exe desktop-native\\build_native.py

Steps, in order, and the second one is not optional:

1. Run PyInstaller with `ai-workbench-native.spec` into `desktop-native/dist/`.
2. Run `verify_native_package.py` against the result and **fail if any check fails**.

Building without verifying is how a package gets shipped that starts on the machine that
built it and nowhere else. The verifier launches the built executable, so a build that
produces a directory full of files and no working application is a build that fails here.

Pass `--skip-verify` to build only, for iterating on the spec.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SPEC = HERE / "ai-workbench-native.spec"
DIST = HERE / "dist"
BUILD = HERE / "build"
APP_NAME = "AI-Workbench-Native"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_native")
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument(
        "--clean", action="store_true", help="remove build/ and dist/ first"
    )
    args = parser.parse_args(argv)

    if args.clean:
        for directory in (BUILD, DIST):
            if directory.exists():
                shutil.rmtree(directory)
                print(f"removed {directory}")

    if not SPEC.is_file():
        print(f"spec not found: {SPEC}")
        return 2

    started = time.monotonic()
    # `--noconfirm` overwrites the output directory without asking; the spec already names
    # it, so an interactive prompt would only ever be answered the same way.
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD),
        str(SPEC),
    ]
    print("$ " + " ".join(command))
    result = subprocess.run(command, cwd=str(REPO), check=False)
    if result.returncode != 0:
        print(f"PyInstaller failed with exit code {result.returncode}")
        return result.returncode

    app = DIST / APP_NAME
    print(f"\nbuilt {app} in {time.monotonic() - started:.1f}s")

    if args.skip_verify:
        return 0

    verify = HERE / "verify_native_package.py"
    return subprocess.run(
        [sys.executable, str(verify), "--app", str(app)], cwd=str(REPO), check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())

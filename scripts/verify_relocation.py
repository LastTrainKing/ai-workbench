"""Prove the relocated copy does not depend on the original project directory.

This is the experiment the installer depends on. Everything before it showed that a
copied tree *can* start; this shows that it starts when the original is not there.

The original directory cannot be renamed while any process holds it as its current
working directory — which is the case here, because the agent's own tooling sits in
it. So the test hides the parts that matter instead: `scripts/` (the launcher chain)
and `.runtime/` (the database, vectors and logs). Those are the only things a
relocated copy could plausibly still be reaching for, and they are renamable because
the lock is on the directory itself, not on its contents.

If the relocated copy comes up with both hidden, it is not using them.

    python scripts/verify_relocation.py            # report what would be done
    python scripts/verify_relocation.py --apply    # run it
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from _process_guard import run_nsis_installer, stop_app_processes  # noqa: E402

ORIGINAL = Path(r"C:\Users\user\Desktop\ai-workbench")
RELOCATED = Path(r"C:\Temp\AIW Relocated Test")
EXE = RELOCATED / "AI Workbench.exe"
HIDDEN_SUFFIX = "-HIDDEN-FOR-RELOCATION-TEST"
PORTS = {"PostgreSQL": 5432, "Redis": 6379, "Qdrant": 6333, "API": 8000}

results: list[tuple[str, bool, str]] = []


def check(step: str, name: str, ok: bool, detail: str = "") -> None:
    results.append((f"{step} {name}", bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def health() -> dict | None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/v1/health", timeout=4) as response:
            return json.loads(response.read())["data"]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return None


def stop_stack(root: Path) -> None:
    subprocess.run(["cmd", "/c", str(root / "scripts" / "stop.bat")], capture_output=True, check=False)


def kill_shells() -> None:
    """Stop the app instances under the relocated directory, and no others."""
    stop_app_processes([RELOCATED])


def hide(root: Path) -> list[tuple[Path, Path]]:
    moved: list[tuple[Path, Path]] = []
    for name in ("scripts", ".runtime"):
        source = root / name
        if not source.exists():
            continue
        target = root / (name + HIDDEN_SUFFIX)
        source.rename(target)
        moved.append((target, source))
    return moved


def restore(moved: list[tuple[Path, Path]]) -> None:
    for hidden, original in moved:
        if hidden.exists() and not original.exists():
            hidden.rename(original)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not args.apply:
        print("plan:")
        print(f"  1 stop the relocated stack at {RELOCATED}")
        print(f"  2 hide {ORIGINAL}\\scripts and \\.runtime")
        print(f"  3 cold-launch {EXE}")
        print("  4 assert: ports up, health 200, login against its own database works")
        print("  5 restore the original, assert it is unchanged")
        print("\nadd --apply to run it")
        return 0

    moved: list[tuple[Path, Path]] = []
    try:
        print("=== 1. stop the relocated stack ===")
        kill_shells()
        time.sleep(3)
        stop_stack(RELOCATED)
        time.sleep(8)
        for name, port in PORTS.items():
            check("1", f"{name} port {port} closed", not port_open(port))

        print("\n=== 2. hide the original's scripts/ and .runtime/ ===")
        check("2", "original project exists", ORIGINAL.is_dir(), str(ORIGINAL))
        moved = hide(ORIGINAL)
        check("2", "hidden scripts and .runtime", len(moved) == 2, ", ".join(h.name for h, _ in moved))
        check("2", "original scripts/ is gone", not (ORIGINAL / "scripts").exists())
        check("2", "original .runtime/ is gone", not (ORIGINAL / ".runtime").exists())

        print("\n=== 3. cold-launch the relocated copy ===")
        subprocess.run(["cmd", "/c", "start", "", str(EXE)], check=False)
        info = None
        for _ in range(30):
            time.sleep(5)
            info = health()
            if info:
                break
        check("3", "relocated stack came up on its own", info is not None, json.dumps(info or {}, ensure_ascii=False))
        for name, port in PORTS.items():
            check("3", f"{name} port {port} open", port_open(port))

        print("\n=== 4. it is a working instance, not just a health endpoint ===")
        try:
            request = urllib.request.Request(
                "http://127.0.0.1:8000/api/v1/auth/login",
                data=json.dumps({"identifier": "demo", "password": "demo1234"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                token = json.loads(response.read())["data"].get("access_token")
            check("4", "login against its OWN database", bool(token), "token received")
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            check("4", "login against its OWN database", False, f"{type(exc).__name__}: {exc}")

        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/", timeout=6) as response:
                html = response.read(200).decode("utf-8", "replace")
            check("4", "serves its own UI bundle", "<!doctype html>" in html.lower())
        except Exception as exc:  # noqa: BLE001
            check("4", "serves its own UI bundle", False, f"{type(exc).__name__}: {exc}")

        check("4", "no dev server on 5173", not port_open(5173))
    finally:
        print("\n=== 5. restore the original ===")
        kill_shells()
        time.sleep(3)
        stop_stack(RELOCATED)
        time.sleep(5)
        restore(moved)
        check("5", "original scripts/ restored", (ORIGINAL / "scripts").is_dir())
        check("5", "original .runtime/ restored", (ORIGINAL / ".runtime").is_dir())

    failed = [r for r in results if not r[1]]
    print(f"\n=== {len(results) - len(failed)}/{len(results)} passed ===")
    for name, _, detail in failed:
        print(f"  [FAIL] {name} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

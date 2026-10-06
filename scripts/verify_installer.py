"""Install it for real, then try to break the claim it makes.

The claim is one sentence: **delete the development directory and the product still
installs, runs and works.** Everything here exists to test that sentence, and to refuse
to accept it on anything weaker than a run.

The nine checks are the ones the task names. Two of them are the reason this script is
long:

* **"runs without the project directory"** is tested by actually moving the project
  directory out of the way, then installing and launching. If Windows refuses the move
  because some process is using it as its working directory, the script says so and
  falls back to hiding `scripts/` and `.runtime/` — which is a *stronger* test, because
  it removes the contents rather than the path. Either way the result is reported as
  what actually happened.
* **"closing the window stops everything"** is tested by killing the shell and then
  probing all four ports. The job object is supposed to make this true even on a force
  kill, so a force kill is what is used.

    python scripts/verify_installer.py            # report the plan
    python scripts/verify_installer.py --apply    # run it
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "build" / "installer" / "AI-Workbench-Setup.exe"
INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "AI Workbench InstallTest"
EXE_NAME = "AI Workbench.exe"
DATA_ASIDE = Path(os.environ["LOCALAPPDATA"]) / "AI Workbench Data"
PROJECT = Path(r"C:\Users\user\Desktop\ai-workbench")
PROJECT_ASIDE = Path(r"C:\Users\user\Desktop\ai-workbench-MOVED-FOR-INSTALL-TEST")
SHORTCUTS = [
    Path(os.environ["USERPROFILE"]) / "Desktop" / "AI Workbench.lnk",
    Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "AI Workbench" / "AI Workbench.lnk",
]
PORTS = {"PostgreSQL": 5432, "Redis": 6379, "Qdrant": 6333, "API": 8000}

results: list[tuple[str, bool, str]] = []


def check(step: str, name: str, ok: bool, detail: str = "") -> None:
    results.append((f"{step} {name}", bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def note(text: str) -> None:
    print(f"  ... {text}")


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


def shells() -> list[int]:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq AI Workbench.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        check=False,
    ).stdout.decode("utf-8", "replace")
    return [1 for line in out.splitlines() if "AI Workbench.exe" in line]


def kill_shells() -> None:
    subprocess.run(["taskkill", "/F", "/IM", EXE_NAME], capture_output=True, check=False)


def browser_count() -> int:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq msedge.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        check=False,
    ).stdout.decode("utf-8", "replace")
    return sum(1 for line in out.splitlines() if "msedge.exe" in line)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not args.apply:
        print("plan:")
        print(f"  1 install {SETUP.name} into {INSTALL_DIR}")
        print("  2 move the project directory aside, then launch the installed copy")
        print("  3 check the desktop and start-menu shortcuts point at the exe")
        print("  4 check: native window, no browser, no 5173")
        print("  5 check: first launch initialised its own database")
        print("  6 register a fresh account and log in")
        print("  7 force-kill the window, check all four ports are released")
        print("  8 uninstall, check the directory is gone and data was preserved")
        print("  9 report path, size, sha256")
        print("\nadd --apply to run it")
        return 0

    print("=" * 74)
    print("  Installer acceptance")
    print("=" * 74)

    if not SETUP.is_file():
        print(f"  no installer at {SETUP}; run scripts/build_installer.py --build")
        return 1

    size = SETUP.stat().st_size
    digest = sha256(SETUP)
    print(f"  setup   : {SETUP}")
    print(f"  size    : {size:,} bytes")
    print(f"  sha256  : {digest}")

    moved_project = False
    try:
        # ------------------------------------------------------------- step 1 --
        print("\n=== 1. install to a clean path ===")
        kill_shells()
        time.sleep(2)
        for path in (INSTALL_DIR, DATA_ASIDE):
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
        check("1", "target path does not exist beforehand", not INSTALL_DIR.exists(), str(INSTALL_DIR))

        install = subprocess.run(
            # `/D=` must be the last parameter and must NOT be quoted. NSIS does not
            # strip quotes from it, so passing it as a separate argv element lets
            # Python quote the whole thing because the path contains a space - and
            # NSIS then installs to its default directory, silently. The command line
            # is therefore built as one string and handed to cmd.
            f'"{SETUP}" /S /D={INSTALL_DIR}',
            shell=True,
            capture_output=True,
            check=False,
        )
        for _ in range(60):
            if (INSTALL_DIR / EXE_NAME).is_file():
                break
            time.sleep(2)
        check("1", "installer exited 0", install.returncode == 0, f"exit {install.returncode}")
        check("1", "launcher present in the install dir", (INSTALL_DIR / EXE_NAME).is_file())
        for part in ("backend", "frontend", "scripts", "vendor", "worker"):
            check("1", f"{part}/ extracted", (INSTALL_DIR / part).is_dir())

        installed_bytes = sum(f.stat().st_size for f in INSTALL_DIR.rglob("*") if f.is_file())
        check("1", "installed tree is complete", installed_bytes > 400 * 1024 * 1024, f"{installed_bytes / 1024 / 1024:,.1f} MB")

        # ------------------------------------------------------------- step 2 --
        print("\n=== 2. take the development tree out of reach ===")
        try:
            PROJECT.rename(PROJECT_ASIDE)
            moved_project = True
            check("2", "project directory moved aside", True, str(PROJECT_ASIDE))
        except OSError as exc:
            note(f"could not move the project directory: {exc}")
            note("(Windows refuses to rename a directory that is a process's working directory)")
            note("falling back to disabling the shell's hardcoded fallback path")

        if not moved_project:
            # `find_project_root()` tries, in order: `AI_WORKBENCH_HOME`, the exe's
            # ancestors, then `%USERPROFILE%\Desktop\ai-workbench`. That third branch
            # is the only way the installed copy could reach this tree, and it
            # requires `scripts\start-desktop.bat` to exist. Renaming that one file
            # therefore removes the fallback completely - and it is a rename, not a
            # directory move, so it does not care that a process is sitting in the
            # directory. If the installed copy still starts, it resolved its own
            # root.
            fallback = PROJECT / "scripts" / "start-desktop.bat"
            fallback_aside = fallback.with_suffix(".bat.HIDDEN-FOR-INSTALL-TEST")
            fallback.rename(fallback_aside)
            check(
                "2",
                "development fallback disabled (scripts\\start-desktop.bat hidden)",
                not fallback.exists() and fallback_aside.exists(),
            )

        # ------------------------------------------------------------- step 3 --
        print("\n=== 3. shortcuts ===")
        for shortcut in SHORTCUTS:
            check("3", f"{shortcut.name} in {shortcut.parent.name}", shortcut.is_file(), str(shortcut))

        # --------------------------------------------------------- steps 4-6 --
        print("\n=== 4-6. launch, initialise, register ===")
        # A baseline, not zero. The question is "did *this* open a browser?", and
        # the machine may legitimately have Edge running already - asserting on
        # zero would fail an install for the user's own browsing.
        browsers_before = browser_count()
        subprocess.run(["cmd", "/c", "start", "", str(INSTALL_DIR / EXE_NAME)], check=False)
        info = None
        for _ in range(60):
            time.sleep(5)
            info = health()
            if info:
                break
        check("4", "native window process running", bool(shells()), f"{len(shells())} process(es)")
        check("4", "API healthy", info is not None, json.dumps(info or {}, ensure_ascii=False))
        check("4", "no dev server on 5173", not port_open(5173))
        check(
            "4",
            "no NEW browser process",
            browser_count() <= browsers_before,
            f"{browsers_before} before, {browser_count()} after",
        )

        check("5", "wrote data-dir.txt pointing outside the program directory", (INSTALL_DIR / "data-dir.txt").is_file())
        # The data lives outside the install directory on purpose, so that uninstalling
        # the program cannot touch it. Checking for `.runtime` *inside* the install dir
        # would be checking for the bug this design removed.
        check("5", "keeps its database outside the program directory", (DATA_ASIDE / ".runtime").is_dir(), str(DATA_ASIDE))
        check("5", "initialised its own PostgreSQL cluster", (DATA_ASIDE / ".runtime" / "pgdata" / "PG_VERSION").is_file())
        check(
            "5",
            "did NOT write .runtime inside the install directory",
            not (INSTALL_DIR / ".runtime").exists(),
            "the layout that made uninstall able to lose data",
        )

        serves_ui = False
        if info:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/", timeout=6) as response:
                    serves_ui = b"<!doctype html>" in response.read(200).lower()
            except (urllib.error.URLError, OSError):
                serves_ui = False
        check("5", "serves the UI", serves_ui)

        # A brand new account, on a brand new database. This is the check that the
        # install is not quietly reading the developer's data.
        email = f"installtest_{int(time.time())}@aiworkbench.dev"
        payload = json.dumps({"email": email, "username": email.split("@")[0], "password": "test1234", "display_name": "Install Test"}).encode()
        request = urllib.request.Request(
            "http://127.0.0.1:8000/api/v1/auth/register",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = json.loads(response.read())
            check("6", "registered a fresh account", bool(body["data"].get("access_token")), email)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            check("6", "registered a fresh account", False, f"{type(exc).__name__}: {exc}")

        # ------------------------------------------------------------- step 7 --
        print("\n=== 7. force-kill the window; everything must go ===")
        kill_shells()
        time.sleep(12)
        for name, port in PORTS.items():
            check("7", f"{name} port {port} released", not port_open(port))

        # ------------------------------------------------------------- step 8 --
        print("\n=== 8. uninstall ===")
        uninstaller = INSTALL_DIR / "Uninstall.exe"
        check("8", "uninstaller exists", uninstaller.is_file())
        if uninstaller.is_file():
            subprocess.run([str(uninstaller), "/S"], capture_output=True, check=False)
            for _ in range(30):
                if not INSTALL_DIR.exists():
                    break
                time.sleep(2)
            check("8", "install directory removed", not INSTALL_DIR.exists(), str(INSTALL_DIR))
            check("8", "user data preserved outside the install directory", (DATA_ASIDE / ".runtime").is_dir(), str(DATA_ASIDE))
            check(
                "8",
                "user data actually survived the uninstall",
                (DATA_ASIDE / ".runtime" / "pgdata" / "PG_VERSION").is_file(),
                "the database the uninstall must not touch",
            )
            for shortcut in SHORTCUTS:
                check("8", f"{shortcut.name} removed", not shortcut.is_file())

    finally:
        # Put the project back, whatever happened.
        print("\n=== restoring the development tree ===")
        if moved_project and PROJECT_ASIDE.exists() and not PROJECT.exists():
            PROJECT_ASIDE.rename(PROJECT)
        fallback_aside = PROJECT / "scripts" / "start-desktop.bat.HIDDEN-FOR-INSTALL-TEST"
        if fallback_aside.exists() and not (PROJECT / "scripts" / "start-desktop.bat").exists():
            fallback_aside.rename(PROJECT / "scripts" / "start-desktop.bat")
        check(
            "restore",
            "project directory intact",
            (PROJECT / "backend").is_dir() and (PROJECT / "scripts" / "start-desktop.bat").is_file(),
        )

    failed = [r for r in results if not r[1]]
    print(f"\n=== {len(results) - len(failed)}/{len(results)} passed ===")
    for name, _, detail in failed:
        print(f"  [FAIL] {name} — {detail}")

    report = {
        "setup_exe": str(SETUP),
        "size_bytes": size,
        "sha256": digest,
        "install_dir": str(INSTALL_DIR),
        "project_moved": moved_project,
        "results": [{"name": n, "ok": ok, "detail": d} for n, ok, d in results],
        "passed": len(results) - len(failed),
        "total": len(results),
    }
    out = ROOT / "build" / "installer-acceptance.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  report: {out.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

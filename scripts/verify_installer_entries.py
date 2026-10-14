"""The installer, tested through the entry points a user actually touches.

Two things this checks, both of which have been got wrong before in this project:

  1. **`repair-install.bat`, run the way the shortcut runs it.** Round 14 verified
     `install_selfcheck.py --repair` by calling Python directly, and shipped a `.bat`
     whose `%~dp0` had been written as `$~dp0` — it exited 2 and repaired nothing. The
     test passed and the feature did not exist. So this runs the batch file, from the
     install directory, exactly as double-clicking the shortcut would.

  2. **A same-named process that belongs to someone else survives.** The uninstaller used
     to finish with `taskkill /F /IM "postgres.exe"`, which matches on the image name and
     therefore kills every copy on the machine — including a user's own database. This
     starts an unrelated process *called* `postgres.exe`, runs the uninstaller, and
     requires the PID to still be alive.

     The stand-in is a copy of a harmless system binary renamed to `postgres.exe`. It is
     not a database and does nothing; the only thing it has in common with the real one is
     the name, which is precisely the thing the old code matched on.

    python scripts/verify_installer_entries.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from _test_data_guard import TEST_DATA_DIR, TEST_INSTALL_DIR, safe_rmtree  # noqa: E402

from _process_guard import run_nsis_installer, stop_app_processes  # noqa: E402

SETUP = ROOT / "build" / "installer" / "AI-Workbench-Setup.exe"
results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")


def pid_alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True, text=True, encoding="mbcs", errors="replace", check=False,
    ).stdout
    return str(pid) in out


def main() -> int:
    if not SETUP.is_file():
        print(f"  no installer at {SETUP}")
        return 2

    print("=== install to the test path ===")
    # A previous run's window holds `AI Workbench.exe` open, and `safe_rmtree` no longer
    # swallows that — it raises, which is how this was noticed. Close it first.
    # Only the instances under this test's install directory; never by image name.
    stop_app_processes([TEST_INSTALL_DIR])
    time.sleep(3)
    safe_rmtree(TEST_INSTALL_DIR, who="verify_installer_entries (clean slate)")
    safe_rmtree(TEST_DATA_DIR, who="verify_installer_entries (clean slate)")
    run_nsis_installer(SETUP, TEST_INSTALL_DIR)
    for _ in range(60):
        if (TEST_INSTALL_DIR / "AI Workbench.exe").is_file():
            break
        time.sleep(2)
    check("installed", (TEST_INSTALL_DIR / "AI Workbench.exe").is_file(), str(TEST_INSTALL_DIR))

    # ---------------------------------------------------------- 1. the repair entry --
    print("\n=== 1. repair-install.bat, from the user's entry point ===")
    repair = TEST_INSTALL_DIR / "repair-install.bat"
    check("repair-install.bat exists", repair.is_file(), str(repair))

    if repair.is_file():
        first_line = ""
        for line in repair.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().lower().startswith("set ") and "here" in line.lower():
                first_line = line.strip()
                break
        check(
            "it computes its own directory with %~dp0, not $~dp0",
            "%~dp0" in first_line and "$~dp0" not in first_line,
            first_line or "(no HERE line found)",
        )

        run = subprocess.run(
            ["cmd", "/c", str(repair)],
            cwd=str(TEST_INSTALL_DIR),
            capture_output=True, text=True, encoding="mbcs", errors="replace", check=False,
            timeout=180,
        )
        print(f"      exit={run.returncode}")
        for line in (run.stdout or "").splitlines()[:6]:
            print(f"      | {line}")
        check("running it exits 0 (nothing to repair)", run.returncode == 0, f"exit={run.returncode}")

        # Break something it is supposed to fix, and run it again.
        print("\n=== 1b. break something repairable, then repair it ===")
        desktop_link = Path(os.environ["USERPROFILE"]) / "Desktop" / "AI Workbench.lnk"
        had_link = desktop_link.is_file()
        if had_link:
            desktop_link.unlink()
        check("desktop shortcut removed on purpose", not desktop_link.is_file())

        run = subprocess.run(
            ["cmd", "/c", str(repair)],
            cwd=str(TEST_INSTALL_DIR),
            capture_output=True, text=True, encoding="mbcs", errors="replace", check=False,
            timeout=180,
        )
        print(f"      exit={run.returncode}")
        check("repair exits 0", run.returncode == 0, f"exit={run.returncode}")
        check("**the shortcut is back**", desktop_link.is_file())

    # ------------------------------------------------- 2. the same-named process --
    print("\n=== 2. an unrelated process called postgres.exe must survive ===")
    with tempfile.TemporaryDirectory(prefix="aiw-decoy-") as tmp:
        decoy = Path(tmp) / "postgres.exe"
        # A copy of a harmless system binary, renamed. It is not a database; the only
        # thing it shares with one is the image name.
        # `timeout.exe` needs a console and exits immediately without one; `ping -t` runs
        # until told to stop and does not care what its own image is called.
        source = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "PING.EXE"
        if not source.is_file():
            check("found a stand-in binary", False, str(source))
        else:
            shutil.copy2(source, decoy)
            process = subprocess.Popen(
                [str(decoy), "-t", "127.0.0.1"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            check("decoy process started", pid_alive(process.pid), f"pid={process.pid}")

            print("\n=== 2b. uninstall, and see whether it survived ===")
            uninstaller = TEST_INSTALL_DIR / "Uninstall.exe"
            if uninstaller.is_file():
                subprocess.run([str(uninstaller), "/S"], check=False, timeout=300)
                # An NSIS uninstaller copies itself to %TEMP% and relaunches, so the
                # process we started returns before the work is done. Waiting a fixed
                # eight seconds and then declaring failure is how the first version of
                # this script reported 8,807 files still present with nothing running.
                for _ in range(120):
                    if not TEST_INSTALL_DIR.exists():
                        break
                    time.sleep(1)
            check(
                "install directory removed",
                not TEST_INSTALL_DIR.exists(),
                str(TEST_INSTALL_DIR) if TEST_INSTALL_DIR.exists() else "",
            )

            alive = pid_alive(process.pid)
            check(
                "**the unrelated same-named process is still alive**",
                alive,
                f"pid={process.pid} alive={alive}",
            )
            # Kill it *inside* the temp directory's lifetime. A running process holds its
            # own image open, so leaving it alive made the directory unremovable and the
            # cleanup error looked like a test failure. The check above already recorded
            # the answer.
            if alive:
                process.kill()
            process.wait(timeout=10)

    failed = [r for r in results if not r[1]]
    print(f"\n=== {len(results) - len(failed)}/{len(results)} passed ===")
    for label, _ok, detail in failed:
        print(f"  [FAIL] {label} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

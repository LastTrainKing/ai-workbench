"""Cold start versus warm start, on the installed copy, with the raw log lines.

The user's acceptance criterion:

    冷启动一次，贴出完整时序；**第二次启动的总秒数**要显著低于第一次，并给出两次的原始日志。

    **别只说"变快了"。**

So this does not print a summary of a summary. It prints the startup log's own lines for
each launch, plus the total measured from the first line to `desktop startup complete`.

**Why the first launch is the slow one, and why that is correct.** `initdb` creates a
PostgreSQL cluster from nothing and only happens when there is none — the check is
`Test-Path $PgData\\PG_VERSION`. The acceptance run always looks like a cold start
because `verify_installer.py` deletes the data directory before installing (source,
lines 147-148), so it is always testing "the first launch after installing". A person
who launches the application twice does not pay for `initdb` twice. This script proves
that by measuring both, on the same installed copy, back to back.

    .venv/Scripts/python.exe scripts/measure_startup.py

Requires ports 5432/6379/6333/8000/5173 to be free.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

LOCALAPPDATA = Path(os.environ["LOCALAPPDATA"])
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _test_data_guard import (  # noqa: E402
    TEST_DATA_DIR,
    point_install_at_test_data,
    safe_rmtree,
)

SETUP = Path(__file__).resolve().parent.parent / "build" / "installer" / "AI-Workbench-Setup.exe"
INSTALL_DIR = LOCALAPPDATA / "AI Workbench InstallTest"

#: The cold-start measurement needs a data directory it may wipe. It used to wipe the
#: user's real one — `%LOCALAPPDATA%\AI Workbench Data`, the same directory the installed
#: app uses — which destroyed 50 MB of real data in round 14. It is now a separate
#: directory, and `safe_rmtree` refuses anything that overlaps the real one.
DATA_DIR = TEST_DATA_DIR
STARTUP_LOG = DATA_DIR / ".runtime" / "logs" / "startup.log"
HEALTH = "http://127.0.0.1:8000/api/v1/health"

#: Lines that mark a milestone rather than a service transition, so the timeline reads
#: as a sequence instead of a wall.
INTERESTING = re.compile(
    r"PostgreSQL|Redis|Qdrant|Infrastructure|api |Application|desktop (start|startup|shutdown)"
)


def free_ports() -> dict[int, int]:
    counts = {}
    for port in (5432, 6379, 6333, 8000, 5173):
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, errors="replace", check=False
        ).stdout
        counts[port] = sum(1 for line in out.splitlines() if "LISTENING" in line and f":{port} " in line)
    return counts


def read_log() -> str:
    if not STARTUP_LOG.exists():
        return ""
    return STARTUP_LOG.read_bytes().decode("utf-8", errors="replace")


def install() -> None:
    safe_rmtree(INSTALL_DIR, who="measure_startup.py install (clean slate)")
    print("  installing...", flush=True)
    subprocess.run(
        f'"{SETUP}" /S /D={INSTALL_DIR}',
        shell=True,
        capture_output=True,
        check=False,
    )
    if not (INSTALL_DIR / "AI Workbench.exe").is_file():
        raise SystemExit("install failed")
    # Point this copy at the test data directory. The installer hard-codes the real one,
    # so without this the measurement would wipe and measure the user's actual data.
    point_install_at_test_data(INSTALL_DIR)


def launch_and_time(label: str, *, wipe_data: bool) -> list[str]:
    """Launch, wait for the API, then return this run's log lines."""
    if wipe_data:
        print(f"  [{label}] wiping the test data directory (this is what makes it a cold start)")
        print(f"  [{label}] it is {DATA_DIR} — never the user's real data directory")
        safe_rmtree(DATA_DIR, who="measure_startup.py cold start (wipe test data)")

    before = len(read_log())
    started = time.perf_counter()
    subprocess.Popen(
        [str(INSTALL_DIR / "AI Workbench.exe")],
        cwd=str(INSTALL_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    healthy = False
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH, timeout=3) as response:
                healthy = response.status == 200
                if healthy:
                    break
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    wall = time.perf_counter() - started

    # Give the launcher a moment to write its final line.
    time.sleep(2.5)
    lines = [line for line in read_log()[before:].splitlines() if line.strip()]
    print(f"  [{label}] API answered 200 after {wall:.1f}s (measured by polling)")
    if not healthy:
        print(f"  [{label}] !! the API never answered")
    return lines


def stop() -> None:
    bat = INSTALL_DIR / "scripts" / "stop-desktop.bat"
    if bat.is_file():
        subprocess.run(["cmd", "/C", str(bat)], capture_output=True, check=False)
    time.sleep(4)


def total_seconds(lines: list[str]) -> float | None:
    """From the launcher's first line to `desktop startup complete`, both stamped."""
    stamps = []
    for line in lines:
        match = re.match(r"(\d{4})/(\d{2})/(\d{2})\s+\S+\s+(\d{2}):(\d{2}):(\d{2})\.(\d+)", line)
        if match:
            y, mo, d, h, mi, s, frac = match.groups()
            stamps.append(
                time.mktime((int(y), int(mo), int(d), int(h), int(mi), int(s), 0, 0, -1))
                + int(frac) / 100
            )
        else:
            match = re.match(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})", line)
            if match:
                y, mo, d, h, mi, s = match.groups()
                stamps.append(time.mktime((int(y), int(mo), int(d), int(h), int(mi), int(s), 0, 0, -1)))
    return (max(stamps) - min(stamps)) if len(stamps) >= 2 else None


def main() -> int:
    counts = free_ports()
    busy = {port: n for port, n in counts.items() if n}
    if busy:
        print(f"端口还被占着，先停开发环境：{busy}")
        return 2

    print("=== 装一份（清掉旧的安装目录）===")
    install()

    print("\n=== 第一次启动：数据目录是空的（等于装完第一次）===")
    cold = launch_and_time("cold", wipe_data=True)
    for line in cold:
        print(f"    {line}")
    cold_total = total_seconds(cold)
    stop()

    print("\n=== 第二次启动：数据目录还在（用户双击第二次）===")
    warm = launch_and_time("warm", wipe_data=False)
    for line in warm:
        print(f"    {line}")
    warm_total = total_seconds(warm)
    stop()

    print("\n=== 结论 ===")
    cold_has_initdb = any("initialising cluster" in line for line in cold)
    warm_has_initdb = any("initialising cluster" in line for line in warm)
    print(f"  第一次日志里有 initialising cluster? {cold_has_initdb}")
    print(f"  第二次日志里有 initialising cluster? {warm_has_initdb}")
    if cold_total and warm_total:
        print(f"  第一次 {cold_total:.1f}s  →  第二次 {warm_total:.1f}s  （差 {cold_total - warm_total:.1f}s）")
        print(f"  第二次比第一次快 {100 * (cold_total - warm_total) / cold_total:.0f}%")
    else:
        print("  时间戳没解析出来，看上面的原始行")

    report = {
        "cold_lines": cold,
        "warm_lines": warm,
        "cold_seconds": cold_total,
        "warm_seconds": warm_total,
        "cold_initialised_cluster": cold_has_initdb,
        "warm_initialised_cluster": warm_has_initdb,
    }
    out = Path(__file__).resolve().parent.parent / "build" / "startup-cold-warm.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

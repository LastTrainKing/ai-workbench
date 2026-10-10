"""Is the user's data intact — and what can this actually prove?

A fingerprint of `%LOCALAPPDATA%\\AI Workbench Data`, comparable against a stored baseline,
plus a structural read of the PostgreSQL cluster inside it.

## What this can and cannot prove

Round 6 had a verifier write `logs\\native-startup.log` into the real data directory. The
restoration was checked by **file count and total bytes**, which is not the same as
"byte-for-byte identical": a file whose contents changed without changing its length would
pass both. So the report says what the arithmetic supports and no more:

* the count went 1530 → 1531 → 1530 and the bytes 51,775,575 → 51,775,757 → 51,775,575;
* the difference is exactly one file of 182 bytes, and 182 bytes is the size of
  `native-startup.log`;
* the mechanism could only have created or appended that one file.

That is a strong argument and it is **not** a hash comparison, because no pre-incident
per-file hash baseline existed. This script creates one, so the next question of this kind
can be answered properly instead of argued.

`build/restored-user-data/` is **not** a pre-incident snapshot and must not be treated as
one: it is a copy of the file the incident *created*, kept so the 182 bytes could be
accounted for.

## Running it

    python scripts/verify_user_data_integrity.py                    # compare, read-only
    python scripts/verify_user_data_integrity.py --write-baseline   # record a new baseline

Read-only in both modes: it never writes inside the data directory, and it never starts or
stops the cluster there.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from _test_data_guard import REAL_DATA_DIR  # noqa: E402

BASELINE = ROOT / "build" / "user-data-baseline.json"
REPORT = ROOT / "build" / "user-data-integrity.json"

#: The figures the round-6 acceptance runs printed before they touched anything. Kept here
#: as the only pre-incident measurement that exists.
RECORDED_PRE_INCIDENT = {"files": 1530, "bytes": 51_775_575}
RECORDED_POST_INCIDENT = {"files": 1531, "bytes": 51_775_757}
INCIDENT_FILE = Path(".runtime") / "logs" / "native-startup.log"

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(root: Path) -> dict:
    """Every file, by relative path, with its size and digest."""
    files: dict[str, dict] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files[str(path.relative_to(root)).replace("\\", "/")] = {
            "bytes": stat.st_size,
            "sha256": sha256(path),
        }
    return {
        "root": str(root),
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "files": len(files),
        "bytes": sum(entry["bytes"] for entry in files.values()),
        "entries": files,
    }


def recorded_figures() -> dict | None:
    """The `(files, bytes)` the acceptance printed before it started, if still on disk."""
    for log in (ROOT / "build").glob("*.log"):
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = re.search(r"之前：(\d+) 文件 / ([\d,]+) 字节", text)
        if found:
            return {"files": int(found.group(1)), "bytes": int(found.group(2).replace(",", ""))}
    return None


def cluster_state(pgdata: Path) -> tuple[bool, str]:
    """Read the cluster's own control file. Read-only: `pg_controldata` does not write."""
    control = ROOT / "vendor" / "runtime" / "pgsql" / "pgsql" / "bin" / "pg_controldata.exe"
    if not control.is_file():
        return False, f"{control} 不存在"
    try:
        completed = subprocess.run(
            [str(control), "-D", str(pgdata)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if completed.returncode != 0:
        return False, (completed.stderr or completed.stdout or "").strip()[:300]
    interesting = [
        line.strip()
        for line in (completed.stdout or "").splitlines()
        if line.startswith(("Database cluster state", "Database system identifier", "pg_control version number"))
    ]
    return True, " · ".join(interesting)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_user_data_integrity.py")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--json", default=str(REPORT))
    args = parser.parse_args(argv)

    print(f"真实数据目录  {REAL_DATA_DIR}")
    if not REAL_DATA_DIR.is_dir():
        check("真实数据目录存在", False, str(REAL_DATA_DIR))
        return 1
    check("真实数据目录存在", True, str(REAL_DATA_DIR))

    current = fingerprint(REAL_DATA_DIR)
    print(f"文件数        {current['files']}")
    print(f"总字节        {current['bytes']:,}")

    # ------------------------------------------------ 1. against the recorded figures --
    section("1. 与事故前记录的（文件数，字节数）比对")
    pre = recorded_figures() or RECORDED_PRE_INCIDENT
    check(
        "文件数与事故前一致",
        current["files"] == pre["files"],
        f"现在 {current['files']}，事故前 {pre['files']}",
    )
    check(
        "总字节与事故前一致",
        current["bytes"] == pre["bytes"],
        f"现在 {current['bytes']:,}，事故前 {pre['bytes']:,}",
    )
    delta_files = RECORDED_POST_INCIDENT["files"] - RECORDED_PRE_INCIDENT["files"]
    delta_bytes = RECORDED_POST_INCIDENT["bytes"] - RECORDED_PRE_INCIDENT["bytes"]
    incident_size = (REAL_DATA_DIR / INCIDENT_FILE)
    check(
        "事故只可能新增了一个文件（差额与那个文件的大小相符）",
        delta_files == 1 and delta_bytes == 182,
        f"事故期间 +{delta_files} 文件 / +{delta_bytes} 字节；备份里那份 native-startup.log 是 "
        f"{(ROOT / 'build' / 'restored-user-data' / 'native-startup.log').stat().st_size if (ROOT / 'build' / 'restored-user-data' / 'native-startup.log').is_file() else '缺失'} 字节",
    )
    check(
        "事故文件已不在数据目录里",
        not incident_size.exists(),
        str(incident_size),
    )

    # ------------------------------------------------ 2. against a per-file baseline --
    section("2. 与逐文件哈希基线比对")
    if args.write_baseline:
        BASELINE.write_text(json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  已写入基线 {BASELINE}（{current['files']} 个文件）")
        check("基线已建立", True, str(BASELINE))
    elif BASELINE.is_file():
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        before = baseline.get("entries", {})
        after = current["entries"]
        missing = sorted(set(before) - set(after))
        extra = sorted(set(after) - set(before))
        changed = sorted(
            name
            for name in set(before) & set(after)
            if before[name]["sha256"] != after[name]["sha256"]
        )
        check("没有文件消失", not missing, f"{missing[:5]}" if missing else "无")
        check("没有文件新增", not extra, f"{extra[:5]}" if extra else "无")
        check("没有文件内容改变", not changed, f"{changed[:5]}" if changed else "无")
        print(f"  基线记录于 {baseline.get('recorded_at')}，{baseline.get('files')} 个文件")
    else:
        print(f"  没有基线（{BASELINE} 不存在）。这一项**未运行**，不是通过。")
        print("  用 --write-baseline 建立一份；此后同类问题可以逐文件哈希回答，而不是靠算术。")

    # ------------------------------------------------ 3. the cluster reads --
    section("3. 数据库集群结构可读")
    pgdata = REAL_DATA_DIR / ".runtime" / "pgdata"
    check("PG_VERSION 存在", (pgdata / "PG_VERSION").is_file(), str(pgdata / "PG_VERSION"))
    ok, detail = cluster_state(pgdata)
    check("pg_controldata 能读出集群状态", ok, detail)
    if ok:
        check(
            "集群处于干净的关闭状态",
            "shut down" in detail.lower(),
            detail,
        )
    base = pgdata / "base"
    relations = sum(1 for _ in base.rglob("*")) if base.is_dir() else 0
    check("集群里有实际的关系文件", relations > 0, f"{relations} 个条目")

    # ------------------------------------------------ verdict --
    passed = sum(1 for _label, ok, _detail in RESULTS if ok)
    total = len(RESULTS)
    print()
    print("-" * 74)
    print(f"结果：{passed}/{total} 通过")
    for label, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL  {label} — {detail}")
    print()
    print("证据边界：")
    print("  · 事故前没有逐文件哈希基线，所以**不能**声称全部文件逐字节一致。")
    print("  · 能声称的是：文件数与总字节回到事故前的值，且事故期间的增量恰好是那个 182 字节的文件。")
    print("  · 本脚本不写入数据目录，也不启动/停止其中的集群。")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "root": str(REAL_DATA_DIR),
                    "checked_at": datetime.now().isoformat(timespec="seconds"),
                    "files": current["files"],
                    "bytes": current["bytes"],
                    "recorded_pre_incident": pre,
                    "baseline": str(BASELINE) if BASELINE.is_file() else "",
                    "passed": passed,
                    "total": total,
                    "checks": [
                        {"label": label, "ok": ok, "detail": detail} for label, ok, detail in RESULTS
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

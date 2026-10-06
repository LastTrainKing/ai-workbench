"""Trim the vendored PostgreSQL distribution down to what actually runs.

The EDB binary zip is a *developer* distribution: it ships a full pgAdmin 4 GUI, a
StackBuilder installer, C headers for compiling extensions, and 156 MB of debug
symbols. None of that is reachable from this product — it starts `postgres.exe` as a
child process and talks to it over a socket.

Measured on this tree:

    pgAdmin 4     654 MB
    symbols       156 MB
    doc            19 MB
    include        16 MB
    StackBuilder  0.8 MB
    ------------------------
    removable     846 MB   (of 967 MB)

That is the difference between an installer nobody wants to download and one that is
merely large, and it costs nothing: what remains is `bin`, `lib` and `share`, which is
the whole of what a server needs.

**This is not a destructive operation.** Everything removed here is re-fetched by
`scripts/fetch-runtime.ps1`, so a mistake is recoverable with one command — which is
why the script is willing to delete at all.

    python scripts/trim_runtime.py            # report only
    python scripts/trim_runtime.py --apply    # trim
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PG_ROOT = ROOT / "vendor" / "runtime" / "pgsql" / "pgsql"

#: Directory name -> why it is not needed at runtime.
REMOVABLE: dict[str, str] = {
    "pgAdmin 4": "a separate GUI administration tool; the product never launches it",
    "symbols": "debug symbols for crash analysis; ~156 MB of .pdb files",
    "doc": "offline HTML/PDF documentation",
    "include": "C headers, only needed to compile extensions against the server",
    "StackBuilder": "an installer for third-party add-ons",
}

#: Kept, and why — so that "what is left" is as explicit as "what goes".
KEPT = {
    "bin": "postgres.exe, initdb, pg_ctl, psql, pg_isready",
    "lib": "shared libraries, timezone data, procedural languages",
    "share": "locale data and extension SQL the server reads at startup",
}


def human(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:,.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:,.1f} TB"


def size_of(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually remove the directories")
    args = parser.parse_args()

    if not PG_ROOT.is_dir():
        print(f"PostgreSQL is not vendored at {PG_ROOT}")
        print("Run: powershell -File scripts\\fetch-runtime.ps1")
        return 1

    before = size_of(PG_ROOT)
    print("=" * 72)
    print(f"  PostgreSQL distribution: {human(before)}")
    print("=" * 72)

    planned: dict[str, int] = {}
    for name, why in REMOVABLE.items():
        target = PG_ROOT / name
        size = size_of(target)
        planned[name] = size
        state = "present" if target.exists() else "already gone"
        print(f"  [remove] {name:14} {human(size):>11}  ({state})")
        print(f"           {why}")

    print()
    for name, why in KEPT.items():
        print(f"  [keep]   {name:14} {human(size_of(PG_ROOT / name)):>11}  {why}")

    removable = sum(planned.values())
    print("-" * 72)
    print(f"  removable: {human(removable)}   ->  after trim: {human(before - removable)}")

    if not args.apply:
        print("\nreport only. add --apply to trim.")
        return 0

    removed: dict[str, int] = {}
    for name in REMOVABLE:
        target = PG_ROOT / name
        if not target.exists():
            continue
        shutil.rmtree(target)
        removed[name] = planned[name]
        print(f"  removed {name}")

    after = size_of(PG_ROOT)
    print(f"\n  {human(before)} -> {human(after)}  (saved {human(before - after)})")

    report = {
        "before_bytes": before,
        "after_bytes": after,
        "saved_bytes": before - after,
        "removed": removed,
        "kept": {name: size_of(PG_ROOT / name) for name in KEPT},
    }
    out = ROOT / "build" / "pgsql-trim-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  report: {out.relative_to(ROOT)}")
    print("\n  NEXT: PostgreSQL must still start. Run:")
    print("    powershell -File scripts\\infra-start.ps1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

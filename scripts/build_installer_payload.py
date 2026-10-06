"""Assemble the installer payload, and measure it before anything is built.

Step one of the installer work is a size and shape experiment, not a build. The task
is explicit about why: an installer this large usually needs a few attempts, and
finding out that NSIS cannot carry the payload *after* writing the hooks is expensive.

So this script does one thing — it builds the exact tree the installer will ship, from
an explicit include/exclude policy, and prints what each part weighs. Nothing is
packaged; `--zip` additionally produces a plain ZIP so the size can be compared with
what the installer produces.

    python scripts/build_installer_payload.py            # assemble + measure
    python scripts/build_installer_payload.py --zip      # also emit a ZIP

The policy is a list of names, not a glob, because every entry here is a decision:
`frontend/dist` ships and `frontend/node_modules` does not; `scripts/` ships whole
(it is 492 KB and the launcher chain is the product's entry point); `.runtime` never
ships because it is the *developer's* database.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "build" / "installer-payload"
DIST = ROOT / "build" / "installer"

#: Copied into the payload, in this order.
#:
#: The shell goes in as `AI Workbench.exe` at the tree root, which is where
#: `find_project_root()`'s ancestor search expects to find it: the exe's own parent
#: directory is the first candidate, so a root-level exe resolves the root. Placing it
#: anywhere else would require `AI_WORKBENCH_HOME` to be set by the shortcut, and an
#: environment variable in a shortcut is a fragile thing to depend on.
INCLUDE: list[tuple[str, str]] = [
    ("desktop/src-tauri/target/release/ai-workbench-desktop.exe", "AI Workbench.exe"),
    ("backend", "backend"),
    ("frontend/dist", "frontend/dist"),
    ("scripts", "scripts"),
    ("worker", "worker"),
    ("vendor/runtime", "vendor/runtime"),
    (".env.example", ".env.example"),
    ("README.md", "README.md"),
    ("LICENSE", "LICENSE"),
]

#: Never copied. Each one is a decision, so each one is named.
EXCLUDE_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    ".git",
    "target",
    "tests",
    "test",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp"}
#: The download caches. `fetch-runtime.ps1` leaves the archives it expanded next to
#: what it expanded — `vendor/runtime/python-nuget.zip` and friends. Shipping them
#: would put the same bytes in the installer twice, once compressed inside the other.
#: Nothing at runtime reads a `.zip`.
EXCLUDE_SUFFIXES |= {".zip"}
#: The developer's database, uploads and logs. Shipping this would hand a stranger a
#: copy of someone else's work, and the first launch would try to use it.
EXCLUDE_ANY = {".runtime", ".venv", "archive", "samples", "docs"}


def human(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:,.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:,.1f} TB"


def skip(relative: Path) -> bool:
    parts = set(relative.parts)
    if parts & EXCLUDE_ANY:
        return True
    if parts & EXCLUDE_DIRS:
        return True
    return relative.suffix.lower() in EXCLUDE_SUFFIXES


def copy_tree(source: Path, target: Path) -> tuple[int, int]:
    """Copy a file or a directory tree, honouring the exclusion policy.

    Both cases matter: `frontend/dist` is a tree and `.env.example` is a single file.
    The first version only handled trees — `Path.rglob("*")` yields nothing for a
    file — so the single-file entries silently contributed zero bytes and the
    payload shipped without a README or an example environment file.
    """
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return 1, source.stat().st_size

    files = 0
    total = 0
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if skip(relative):
            continue
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        files += 1
        total += item.stat().st_size
    return files, total


def measure(root: Path) -> tuple[int, int]:
    files = 0
    total = 0
    for item in root.rglob("*"):
        if item.is_file():
            files += 1
            total += item.stat().st_size
    return files, total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", action="store_true", help="also emit a ZIP of the payload")
    args = parser.parse_args()

    if PAYLOAD.exists():
        shutil.rmtree(PAYLOAD)
    PAYLOAD.mkdir(parents=True)

    print("=" * 74)
    print("  Assembling the installer payload")
    print("=" * 74)

    report: dict[str, dict[str, object]] = {}
    missing: list[str] = []
    started = time.monotonic()

    for source_name, target_name in INCLUDE:
        source = ROOT / source_name
        if not source.exists():
            missing.append(source_name)
            print(f"  [SKIP] {source_name:24} not present")
            continue
        target = PAYLOAD / target_name
        # Only directories get pre-created. Calling mkdir on a single-file target
        # creates a *directory* with the file's name, and the copy then lands inside
        # it — which is how the launcher ended up as
        # `AI Workbench.exe/ai-workbench-desktop.exe` and the installer refused to
        # build because the payload "had no launcher".
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        files, size = copy_tree(source, target)
        report[target_name] = {"files": files, "bytes": size}
        print(f"  {target_name:28} {files:>7,} files  {human(size):>12}")

    print("-" * 74)
    total_files, total_bytes = measure(PAYLOAD)
    print(f"  {'payload total':28} {total_files:>7,} files  {human(total_bytes):>12}")

    if missing:
        print(f"\n  MISSING: {', '.join(missing)}")
        print("  A payload without these will install but not run. Run fetch-runtime.ps1 first.")

    # ------------------------------------------------------------- zip probe --
    zip_size = 0
    if args.zip:
        DIST.mkdir(parents=True, exist_ok=True)
        archive = DIST / "ai-workbench-payload.zip"
        print(f"\n  writing {archive.relative_to(ROOT)} ...")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for item in PAYLOAD.rglob("*"):
                if item.is_file():
                    bundle.write(item, item.relative_to(PAYLOAD))
        zip_size = archive.stat().st_size
        print(f"  zip: {human(zip_size)}  ({zip_size / max(total_bytes, 1):.1%} of raw)")

    elapsed = time.monotonic() - started
    print(f"\n  assembled in {elapsed:.1f}s")

    summary = {
        "payload_dir": str(PAYLOAD),
        "files": total_files,
        "bytes": total_bytes,
        "parts": report,
        "missing": missing,
        "zip_bytes": zip_size,
    }
    out = ROOT / "build" / "payload-report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  report: {out.relative_to(ROOT)}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Assemble the native installer payload, compile it with NSIS, and report what was built.

Three steps, in this order, for reasons worth stating:

1. **The payload is assembled fresh, from an explicit include list.** The native
   application is the PyInstaller tree in `desktop-native/dist/`; the engines come from
   `vendor/runtime`; the scripts that start them come from `scripts/`. Nothing else goes in.
   A stale payload ships a previous version of the product and nothing says so.

2. **The payload is measured before it is compiled.** An installer this large usually needs
   a few attempts, and finding out that NSIS cannot carry it *after* writing the hooks is
   expensive.

3. **NSIS is invoked directly.** Same compiler the previous installer used, found in the
   Tauri cache or on the PATH — the toolchain is not being replaced.

    python scripts/build_native_installer.py                 # report what would be built
    python scripts/build_native_installer.py --payload-only  # assemble and measure
    python scripts/build_native_installer.py --build         # assemble and compile

**What is deliberately not in the payload**, and why each one matters:

* ``vendor/runtime/python`` (323 MB). The previous shell was a Tauri binary that ran
  `scripts/start-desktop.bat`, which needed a Python interpreter to start the FastAPI
  server. The native application *is* the interpreter — PyInstaller embeds one — and it
  never runs `dev.ps1`. Shipping 323 MB of interpreter that nothing starts is not
  "self-contained", it is weight.
* ``vendor/runtime/python-nuget.zip``. The download cache from `fetch-runtime.ps1`. Nothing
  at runtime reads a `.zip`.
* The rest of ``scripts/``. `start-desktop.bat`, `start.bat`, `dev.ps1` and the Tauri
  launcher chain are the *previous* architecture's entry points. Shipping them in a native
  install would put two contradictory descriptions of how the product starts inside one
  program directory.
* ``.env``, ``.runtime``, ``data-dir.txt``, ``pgdata``. The deployment's secrets and the
  user's database. The installer writes `data-dir.txt` itself, pointing outside the program
  directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NSI = ROOT / "installer" / "ai-workbench-native.nsi"
SETUP = ROOT / "build" / "installer" / "AI-Workbench-Native-Setup.exe"
PAYLOAD = ROOT / "build" / "native-installer-payload"
REPORT = ROOT / "build" / "native-installer-report.json"

#: The PyInstaller tree. Built by `desktop-native/build_native.py`.
NATIVE_DIST = ROOT / "desktop-native" / "dist" / "AI-Workbench-Native"

#: Sources copied **verbatim**, with no exclusion policy applied. Only the PyInstaller
#: bundle: what goes inside it is the bootloader's decision, not this script's.
VERBATIM_SOURCES = {"desktop-native/dist/AI-Workbench-Native"}

#: Copied into the payload, in this order. `(source, destination relative to the payload)`.
INCLUDE: list[tuple[str, str]] = [
    ("desktop-native/dist/AI-Workbench-Native", "."),
    ("vendor/runtime/pgsql", "vendor/runtime/pgsql"),
    ("vendor/runtime/redis", "vendor/runtime/redis"),
    ("vendor/runtime/qdrant", "vendor/runtime/qdrant"),
    ("scripts/lib.ps1", "scripts/lib.ps1"),
    ("scripts/infra-start.ps1", "scripts/infra-start.ps1"),
    ("scripts/stop-by-pid.ps1", "scripts/stop-by-pid.ps1"),
    ("scripts/_restricted-launch.ps1", "scripts/_restricted-launch.ps1"),
    ("scripts/_start-postgres.ps1", "scripts/_start-postgres.ps1"),
    ("VERSION", "VERSION"),
    ("README.md", "README.md"),
    ("LICENSE", "LICENSE"),
]

#: Never copied, wherever they appear. Each is a decision, so each is named.
EXCLUDE_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "node_modules",
    ".git",
    "tests",
    # The PostgreSQL cluster. It lives in the *data* directory; one here would mean the
    # installer shipped someone's database.
    "pgdata",
    "snapshots",
}
#: Extensions dropped **from the repository parts of the payload** (vendor, scripts).
#:
#: `.zip` is deliberately not here. It was, and it dropped `base_library.zip` —
#: PyInstaller's bootstrap archive — from the PyInstaller tree. The result installed
#: perfectly, contained 3,912 files instead of 3,910, and **hung at the bootloader with no
#: message, no log and no window**: a windowed build that fails before Python starts has
#: nowhere to report. Two acceptance runs died on it before the file counts were compared.
#:
#: The PyInstaller tree is now copied verbatim (`copy_verbatim=True`) and its file count is
#: checked against the source afterwards, so this cannot recur through a suffix rule.
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp"}
EXCLUDE_ANY = {".env", ".runtime", "data-dir.txt", "qdrant-api-key.txt", "postmaster.pid"}

#: Download caches `fetch-runtime.ps1` leaves beside what it expanded. Named rather than
#: matched by suffix, so nothing else that happens to end in `.zip` can be caught with them.
EXCLUDE_ANY |= {"python-nuget.zip", "pgsql-win.zip", "qdrant-win.zip", "redis-win.zip"}

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


def measure(path: Path) -> tuple[int, int]:
    """(bytes, files) under a path."""
    total = 0
    count = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            total += entry.stat().st_size
            count += 1
    return total, count


def _excluded(path: Path) -> bool:
    if path.name in EXCLUDE_ANY:
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    return any(part in EXCLUDE_DIRS for part in path.parts)


def _copy_tree(source: Path, destination: Path, *, verbatim: bool = False) -> None:
    """Copy a tree, applying the exclusion policy unless it is PyInstaller's.

    `verbatim=True` for the PyInstaller bundle. Its contents are its own business: the
    bootloader needs `base_library.zip` and the `__pycache__` trees it wrote, and a policy
    about what belongs in an *installer* has no standing over what belongs in a *bundle*.
    """
    for entry in source.rglob("*"):
        relative = entry.relative_to(source)
        if not verbatim and _excluded(relative):
            continue
        target = destination / relative
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(entry, target)


def assemble() -> int:
    """Build the payload tree. Returns 0, or 1 with the reason printed."""
    if not NATIVE_DIST.is_dir():
        print(
            f"缺少原生应用：{NATIVE_DIST}\n"
            "先运行：python desktop-native/build_native.py",
            file=sys.stderr,
        )
        return 1

    exe = NATIVE_DIST / "AI-Workbench-Native.exe"
    if not exe.is_file():
        print(f"缺少可执行文件：{exe}", file=sys.stderr)
        return 1

    if PAYLOAD.exists():
        shutil.rmtree(PAYLOAD)
    PAYLOAD.mkdir(parents=True)

    print(f"组装 payload -> {PAYLOAD}")
    rows: list[tuple[str, int, int]] = []
    for source_name, destination_name in INCLUDE:
        source = ROOT / source_name
        if not source.exists():
            print(f"  缺少 {source_name}", file=sys.stderr)
            return 1
        destination = PAYLOAD / destination_name
        before_bytes, before_files = measure(PAYLOAD)
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            _copy_tree(source, destination, verbatim=source_name in VERBATIM_SOURCES)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        after_bytes, after_files = measure(PAYLOAD)
        rows.append((destination_name, after_bytes - before_bytes, after_files - before_files))
        print(f"  {destination_name:<28} {human(after_bytes - before_bytes):>12}  {after_files - before_files:>7} 个文件")

        if source_name in VERBATIM_SOURCES:
            # The invariant that would have caught the missing bootstrap archive. A verbatim
            # copy must be complete: anything missing from a PyInstaller bundle is a
            # bootloader failure, and a bootloader failure in a windowed build is a hang
            # with no evidence anywhere.
            expected = sum(1 for p in source.rglob("*") if p.is_file())
            copied = sum(1 for p in destination.rglob("*") if p.is_file())
            if copied != expected:
                print(
                    f"  !! {destination_name} 拷贝不完整：{copied}/{expected} 个文件。"
                    "PyInstaller 的产物必须原样打包。",
                    file=sys.stderr,
                )
                return 1

    total_bytes, total_files = measure(PAYLOAD)
    print(f"  {'合计':<28} {human(total_bytes):>12}  {total_files:>7} 个文件")

    # The two files whose absence produces an installer that installs perfectly and cannot
    # start. Checked here rather than discovered by the user.
    required = [
        "AI-Workbench-Native.exe",
        "_internal",
        # PyInstaller's bootstrap archive. Its absence is a silent hang, which is why it is
        # named here as well as protected by the verbatim copy above.
        "_internal/base_library.zip",
        "vendor/runtime/pgsql",
        "scripts/infra-start.ps1",
    ]
    for required_path in required:
        if not (PAYLOAD / required_path).exists():
            print(f"payload 缺少 {required_path}", file=sys.stderr)
            return 1

    # Nothing that is a credential or the user's data. The list is the same one the package
    # verifier uses, applied to the tree that is about to be compiled.
    for entry in PAYLOAD.rglob("*"):
        if entry.is_file() and entry.name in EXCLUDE_ANY:
            print(f"payload 里出现了不该打包的文件：{entry.relative_to(PAYLOAD)}", file=sys.stderr)
            return 1
    return 0


def build() -> int:
    if assemble() != 0:
        return 1
    nsis = find_nsis()
    if nsis is None:
        print("找不到 makensis（NSIS）。", file=sys.stderr)
        return 1
    SETUP.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = subprocess.run(
        [str(nsis), "/V2", str(NSI)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    elapsed = time.monotonic() - started
    print(result.stdout.strip()[-4000:])
    if result.returncode != 0:
        print(result.stderr.strip()[-4000:], file=sys.stderr)
        print(f"makensis 失败（退出码 {result.returncode}）", file=sys.stderr)
        return 1
    if not SETUP.is_file():
        print(f"makensis 报告成功，但没有生成 {SETUP}", file=sys.stderr)
        return 1

    size = SETUP.stat().st_size
    digest = sha256(SETUP)
    payload_bytes, payload_files = measure(PAYLOAD)
    report = {
        "installer": str(SETUP),
        "installer_bytes": size,
        "installer_human": human(size),
        "sha256": digest,
        "payload_bytes": payload_bytes,
        "payload_human": human(payload_bytes),
        "payload_files": payload_files,
        "compression_ratio": round(payload_bytes / size, 2) if size else 0,
        "nsis": str(nsis),
        "build_seconds": round(elapsed, 1),
        "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"安装包   {SETUP}")
    print(f"大小     {human(size)}")
    print(f"SHA-256  {digest}")
    print(f"报告     {REPORT}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_native_installer.py")
    parser.add_argument("--payload-only", action="store_true", help="assemble and measure")
    parser.add_argument("--build", action="store_true", help="assemble and compile")
    args = parser.parse_args(argv)
    if args.build:
        return build()
    return assemble()


if __name__ == "__main__":
    raise SystemExit(main())

"""Build the final delivery archive.

Uses ``git ls-files`` as the source of truth. That is the strongest guarantee
available that the archive contains only intended source: anything git-ignored
(``.env`` with the real API key, ``.runtime/`` database and uploads,
``node_modules``, the downloaded runtimes, build output, caches, logs) is
excluded by construction rather than by a hand-maintained exclusion list.

A verification pass then re-opens the finished archive and asserts that no
credential and no runtime artefact made it in.

Usage::

    python scripts/build_delivery_zip.py
    python scripts/build_delivery_zip.py --output "D:/ai-workbench-1.1.0-final.zip"
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT.parent / "ai-workbench-1.1.0-final.zip"
ARCHIVE_PREFIX = "ai-workbench"

#: Anything matching these must never appear inside the archive.
FORBIDDEN_PARTS = {
    "node_modules", ".venv", "venv", ".runtime", "vendor", "dist", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git", "coverage",
    ".workbuddy-ai", "test-results", "playwright-report",
}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".log", ".zip", ".tsbuildinfo"}
FORBIDDEN_NAMES = {".env", "ai-config.json", ".coverage"}

#: Credential shapes that must not appear in any archived text file.
#: Deliberately generic — a real key is never hard-coded here. The concrete
#: values are read at build time from `.env` and the environment (see
#: ``collect_secrets``), so this file itself can be archived safely.
_GENERIC_SECRET = re.compile(r"sk-[A-Za-z0-9]{20,}")
_SYNTHETIC_HINTS = ("xxx", "your", "example", "dummy", "fake", "placeholder", "0123456789abcdef")

TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".json", ".md", ".txt", ".toml",
    ".ini", ".cfg", ".yaml", ".yml", ".bat", ".sh", ".ps1", ".html", ".css",
    ".mako", ".example", ".gitignore", ".dockerignore", ".env",
}


def collect_secrets() -> set[str]:
    """Read every real credential this machine knows about.

    Sources: ``.env`` (the git-ignored file) and the process environment. Values
    are kept in memory only and never written anywhere.
    """
    secrets: set[str] = set()

    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if "KEY" in key.upper() or "SECRET" in key.upper() or "PASSWORD" in key.upper():
                value = value.strip().strip("'\"")
                if len(value) >= 16:
                    secrets.add(value)

    for name, value in os.environ.items():
        if ("KEY" in name.upper() or "SECRET" in name.upper()) and len(value) >= 16:
            secrets.add(value.strip())

    return secrets


def looks_synthetic(value: str) -> bool:
    lowered = value.lower()
    if any(hint in lowered for hint in _SYNTHETIC_HINTS):
        return True
    body = lowered.removeprefix("sk-")
    return bool(body) and len(set(body)) <= 2


def find_git() -> str:
    """Locate `git`, and say where it looked when it cannot be found.

    `git` is not on this machine's PATH. The previous version called the bare name, so
    the failure surfaced as a bare `FileNotFoundError: [WinError 2]` from `subprocess`
    — which says nothing about what was missing or what to do about it, and reads like
    a bug in the script rather than a missing dependency.

    Search order: PATH, then the places git is actually installed on a Windows
    developer machine, including the portable copy that ships with this environment.
    """
    candidates: list[str] = []

    on_path = shutil.which("git")
    if on_path:
        candidates.append(on_path)

    candidates.extend(
        [
            r"C:\Program Files\Git\cmd\git.exe",
            r"C:\Program Files (x86)\Git\cmd\git.exe",
            str(Path.home() / "AppData" / "Local" / "Programs" / "Git" / "cmd" / "git.exe"),
        ]
    )

    # The portable copy used by this environment, under any version.
    portable = Path.home() / ".workbuddy-ai" / "binaries" / "PortableGit" / "versions"
    if portable.is_dir():
        for version in sorted(portable.iterdir(), reverse=True):
            candidates.append(str(version / "cmd" / "git.exe"))

    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate

    raise SystemExit(
        "git was not found, and this script uses `git ls-files` as its file list.\n"
        "Looked in:\n  " + "\n  ".join(candidates) + "\n"
        "Install git, or add it to PATH, or pass the path in GIT_EXECUTABLE."
    )


def tracked_files() -> list[Path]:
    git = os.environ.get("GIT_EXECUTABLE") or find_git()
    result = subprocess.run(
        [git, "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    names = [n for n in result.stdout.decode("utf-8").split("\0") if n]
    return [ROOT / name for name in names]


def is_forbidden(relative: Path) -> bool:
    if any(part in FORBIDDEN_PARTS for part in relative.parts):
        return True
    if relative.name in FORBIDDEN_NAMES:
        return True
    return relative.suffix.lower() in FORBIDDEN_SUFFIXES


def build(output: Path) -> int:
    files = tracked_files()
    kept = [p for p in files if not is_forbidden(p.relative_to(ROOT))]
    skipped = [p for p in files if is_forbidden(p.relative_to(ROOT))]

    print(f"  tracked files      : {len(files)}")
    print(f"  included           : {len(kept)}")
    print(f"  excluded by policy : {len(skipped)}")
    for path in skipped:
        print(f"      - {path.relative_to(ROOT)}")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(kept):
            relative = path.relative_to(ROOT)
            archive.write(path, arcname=f"{ARCHIVE_PREFIX}/{relative.as_posix()}")

    return len(kept)


def verify(output: Path) -> list[str]:
    """Re-open the archive and prove it is clean."""
    problems: list[str] = []
    secrets = collect_secrets()
    print(f"  known credentials to look for : {len(secrets)}")

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        for name in names:
            parts = Path(name).parts[1:]  # drop the ai-workbench/ prefix
            if any(part in FORBIDDEN_PARTS for part in parts):
                problems.append(f"forbidden directory in archive: {name}")
            if Path(name).name in FORBIDDEN_NAMES:
                problems.append(f"forbidden file in archive: {name}")
            if Path(name).suffix.lower() in FORBIDDEN_SUFFIXES:
                problems.append(f"forbidden file type in archive: {name}")

        for name in names:
            if Path(name).suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                payload = archive.read(name).decode("utf-8", errors="ignore")
            except (KeyError, OSError):
                continue
            for secret in secrets:
                if secret in payload:
                    problems.append(f"credential found in archive: {name}")
            for match in _GENERIC_SECRET.finditer(payload):
                if not looks_synthetic(match.group(0)):
                    problems.append(
                        f"credential-shaped string in archive: {name} ({match.group(0)[:12]}…)"
                    )

        required = [
            f"{ARCHIVE_PREFIX}/README.md",
            f"{ARCHIVE_PREFIX}/DELIVERY.md",
            f"{ARCHIVE_PREFIX}/.env.example",
            f"{ARCHIVE_PREFIX}/.gitignore",
            f"{ARCHIVE_PREFIX}/LICENSE",
            f"{ARCHIVE_PREFIX}/docker-compose.yml",
            f"{ARCHIVE_PREFIX}/start.bat",
            f"{ARCHIVE_PREFIX}/stop.bat",
            f"{ARCHIVE_PREFIX}/scripts/start.bat",
            f"{ARCHIVE_PREFIX}/scripts/stop.bat",
            f"{ARCHIVE_PREFIX}/scripts/restart.bat",
            f"{ARCHIVE_PREFIX}/docs/deepseek-setup.md",
            f"{ARCHIVE_PREFIX}/docs/api.md",
            f"{ARCHIVE_PREFIX}/docs/architecture.md",
            f"{ARCHIVE_PREFIX}/backend/requirements.txt",
            f"{ARCHIVE_PREFIX}/backend/app/main.py",
            f"{ARCHIVE_PREFIX}/backend/tests/conftest.py",
            f"{ARCHIVE_PREFIX}/frontend/package.json",
            f"{ARCHIVE_PREFIX}/frontend/src/lib/api.ts",
            f"{ARCHIVE_PREFIX}/worker/run.py",
        ]
        for item in required:
            if item not in names:
                problems.append(f"missing required entry: {item}")

        migrations = [n for n in names if "/alembic/versions/" in n and n.endswith(".py")]
        if not migrations:
            problems.append("no Alembic migration in the archive")

        screenshots = [n for n in names if "/screenshots/" in n and n.endswith(".png")]
        if len(screenshots) < 5:
            problems.append(f"expected screenshots in the archive, found {len(screenshots)}")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the delivery archive")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    print("=" * 74)
    print("  Building the delivery archive")
    print("=" * 74)

    count = build(args.output)
    size_mb = args.output.stat().st_size / (1024 * 1024)
    print(f"\n  archive : {args.output}")
    print(f"  entries : {count}")
    print(f"  size    : {size_mb:.1f} MB")

    print("\n  verifying the archive ...")
    problems = verify(args.output)
    if problems:
        print("\n  [FAIL] verification found problems:")
        for problem in problems:
            print(f"      {problem}")
        return 1

    print("  [PASS] no credentials, no runtime artefacts, all required entries present")
    return 0


if __name__ == "__main__":
    sys.exit(main())

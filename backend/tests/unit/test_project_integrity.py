"""Import every module in the application package.

This is a cheap but extremely effective regression test: it catches stale
relative imports left behind by refactoring, missing `__init__.py` files,
circular imports and import-time side effects.

It exists because a real bug shipped during development — after two service
modules were merged, the agent tools still imported the old module paths, and
because tool errors are returned as ``ok: false`` results instead of raising,
the failure was invisible until a test inspected the tool output.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

import pytest

import app

APP_ROOT = Path(app.__file__).resolve().parent
#: Repository root, used by the secret-hygiene checks.
REPO_ROOT = APP_ROOT.parent.parent


def _discover_modules() -> list[str]:
    names: list[str] = []
    for module in pkgutil.walk_packages([str(APP_ROOT)], prefix="app."):
        if any(part.startswith("_") for part in module.name.split(".")):
            continue
        names.append(module.name)
    return sorted(names)


MODULES = _discover_modules()


def test_module_discovery_is_not_empty() -> None:
    assert len(MODULES) > 30, f"expected to discover the app package, got {MODULES[:5]}"


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name: str) -> None:
    importlib.import_module(module_name)


def test_no_stale_service_module_references() -> None:
    """Every ``app.services.<name>`` referenced anywhere must actually exist."""
    pattern = re.compile(r"app\.services\.([a-z_]+)")
    referenced: set[str] = set()
    for path in APP_ROOT.rglob("*.py"):
        referenced.update(pattern.findall(path.read_text(encoding="utf-8")))

    assert referenced, "expected to find service references"
    services_dir = APP_ROOT / "services"
    available = {
        p.stem for p in services_dir.glob("*.py") if p.name != "__init__.py"
    }
    missing = referenced - available
    assert not missing, f"stale service imports: {sorted(missing)} (available: {sorted(available)})"


def test_no_reserved_logging_keys_in_extra_dicts() -> None:
    """``extra={...}`` must never use a name that logging already owns.

    ``logging`` raises ``KeyError: Attempt to overwrite 'x' in LogRecord`` which,
    inside an exception handler, turns a clean 4xx into a 500.
    """
    import re

    reserved = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
    }
    key_pattern = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')
    offenders: list[str] = []

    for path in APP_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"extra\s*=\s*\{([^}]*)\}", text, re.DOTALL):
            for key in key_pattern.findall(match.group(1)):
                if key in reserved:
                    offenders.append(f"{path.relative_to(APP_ROOT)}: extra['{key}']")

    assert not offenders, "reserved log record keys used in extra=: " + "; ".join(offenders)


def test_router_registration_is_complete() -> None:
    from app.api.v1 import api_router
    from app.main import app as fastapi_app

    schema = fastapi_app.openapi()
    paths = schema["paths"]
    assert len(paths) >= 45, f"only {len(paths)} API paths registered"
    assert api_router.routes, "api_router must include sub-routers"


def test_all_models_are_registered_in_metadata() -> None:
    from app.models import Base

    expected_tables = {
        "users", "projects", "knowledge_bases", "documents", "document_chunks",
        "tags", "document_tags", "concepts", "document_concepts", "tasks",
        "task_dependencies", "task_tags", "task_documents", "notes", "prompts",
        "prompt_versions", "ai_runs", "agent_runs", "tool_calls", "ai_run_sources",
        "tool_audit_log",
        "user_profile", "user_preferences", "memory_entries",
        "memory_candidates", "context_snapshots",
        "build_runs", "deliverables",
        "jobs", "activity_events",
    }
    assert set(Base.metadata.tables) == expected_tables


def test_no_duplicate_index_names() -> None:
    """A column-level ``index=True`` plus an explicit ``Index`` with the same
    name breaks ``Base.metadata.create_all`` with DuplicateTableError."""
    from collections import Counter

    from app.models import Base

    names = [idx.name for table in Base.metadata.tables.values() for idx in table.indexes]
    duplicates = [name for name, count in Counter(names).items() if count > 1]
    assert not duplicates, f"duplicate index names: {duplicates}"


# --------------------------------------------------------------------------- #
# secret hygiene
# --------------------------------------------------------------------------- #
#: Directories that legitimately contain generated or third-party content.
_SKIP_DIRS = {
    ".git", ".venv", "node_modules", ".runtime", "vendor", "dist",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "coverage",
    # Build output. Not scanned because it is generated, not authored, and the
    # Rust target directory alone is tens of thousands of files — walking it made
    # this test time out rather than fail.
    "target", "build", "out", "gen", ".next", ".turbo", "bin", "obj",
}

#: Patterns that indicate a real credential rather than a placeholder.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?:api[_-]?key|secret[_-]?key|password)\s*[:=]\s*['\"][^'\"\s]{16,}['\"]", re.I),
]

#: Files allowed to hold a secret: they are git-ignored and never shipped.
_ALLOWED = {".env", "ai-config.json"}

#: An inline marker that exempts a single line — the convention used by
#: detect-secrets, so the intent is obvious to anyone reading the file.
_ALLOW_MARKERS = ("allowlist secret", "secret-scan: allow", "pragma: allowlist")

#: Values that are plainly synthetic rather than a real credential.
_SYNTHETIC_HINTS = (
    "xxx", "your", "example", "dummy", "fake", "placeholder", "sample",
    "0000000000", "1234567890", "0123456789abcdef", "aaaaaaaa",
)


def _iter_repo_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.name in _ALLOWED:
            continue
        if path.suffix.lower() in {
            ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".woff", ".woff2",
        }:
            continue
        files.append(path)
    return files


def _looks_synthetic(candidate: str) -> bool:
    lowered = candidate.lower()
    if any(hint in lowered for hint in _SYNTHETIC_HINTS):
        return True
    # A single repeated character, e.g. sk-aaaaaaaaaaaaaaaaaaaa
    body = lowered.removeprefix("sk-")
    return bool(body) and len(set(body)) <= 2


def test_no_credentials_are_committed_anywhere() -> None:
    """No source, test, doc, config template or script may contain a real key.

    This is the guard for a mistake that was actually made: a real DeepSeek key
    was used as the sample value in a masking unit test. It only existed in the
    working tree for a few minutes, but a commit would have published it
    permanently — and rewriting Git history costs far more than this test.

    A line may opt out with an inline ``allowlist secret`` comment; obvious
    synthetic values (``sk-0123456789abcdef…``) are ignored automatically.
    """
    offenders: list[str] = []
    for path in _iter_repo_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            if any(marker in lowered for marker in _ALLOW_MARKERS):
                continue
            for pattern in _SECRET_PATTERNS:
                for match in pattern.finditer(line):
                    candidate = match.group(0)
                    if any(token in candidate.lower() for token in ("xxx", "your", "example", "test", "dummy", "fake", "placeholder", "...", "<")):
                        continue
                    if _looks_synthetic(candidate):
                        continue
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{line_number}: {candidate[:24]}…"
                    )

    assert not offenders, "possible credentials found in tracked files:\n" + "\n".join(offenders)


def test_env_is_git_ignored_and_template_is_not() -> None:
    """``.env`` must be ignored; ``.env.example`` must be committed."""
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    lines = {line.strip() for line in ignore.splitlines()}
    assert ".env" in lines, ".env must be git-ignored"
    assert "!.env.example" in lines, ".env.example must be re-included"

    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("DEEPSEEK_API_KEY=", "AI_PROVIDER=", "DEEPSEEK_MODEL="):
        assert key in example, f"{key} missing from .env.example"


def test_env_example_never_ships_a_real_key() -> None:
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if "API_KEY" in line and "=" in line:
            value = line.split("=", 1)[1].strip()
            assert not value.startswith("sk-") or value in {"", "sk-..."}, (
                f".env.example must not contain a key: {line}"
            )


def test_no_credential_exists_anywhere_in_git_history() -> None:
    """Scan every commit, not just the working tree.

    The working-tree check above is not enough. A real key was once committed and
    only fixed in a *later* commit, which leaves it permanently readable in
    history. This test is the guard for that: it walks every commit, extracts
    every credential-shaped string, and fails on any that is not obviously
    synthetic.

    Skips (rather than fails) when the directory is not a Git repository, so the
    suite still runs from an extracted source archive.
    """
    import subprocess

    if not (REPO_ROOT / ".git").exists():
        pytest.skip("not a git repository - history cannot be scanned")

    try:
        commits = subprocess.run(
            ["git", "rev-list", "--all"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"git unavailable: {exc}")

    if not commits:
        pytest.skip("no commits yet")

    offenders: list[str] = []
    for commit in commits:
        # -o prints only the matched text, so synthetic samples are filtered out
        # the same way the working-tree scan filters them.
        result = subprocess.run(
            ["git", "grep", "-I", "-o", "-E", r"sk-[A-Za-z0-9]{20,}", commit],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            _, _, match = line.partition(":")
            if match and not _looks_synthetic(match):
                offenders.append(f"{commit[:8]}: {match[:16]}...")

    assert not offenders, (
        "credentials found in git history - rewrite it before publishing: "
        + ", ".join(sorted(set(offenders)))
    )

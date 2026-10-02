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
from pathlib import Path

import pytest

import app

APP_ROOT = Path(app.__file__).resolve().parent


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
    import re

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

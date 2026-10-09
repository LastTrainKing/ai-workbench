"""Release-freeze regression tests — one version for one product.

The confirmed defect: the Tauri shell said ``2.0.0``, the installer ``2.1.0``, the backend
``1.0.0``, the frontend ``1.0.0`` and the changelog stopped at ``1.1.0``. A user reading
*Help → About* and looking at the installer they downloaded saw two different products,
and the split was documented as intentional. For a user-visible version that is not a
defensible position.

``VERSION`` at the repository root is now the source, ``scripts/sync_version.py``
propagates it, and these tests fail the moment a file drifts.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sync_version


def test_version_file_is_a_bare_semantic_version() -> None:
    text = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", text), text


def test_every_file_agrees_with_the_version_file() -> None:
    """The defect, stated as an assertion across all five carriers."""
    version = sync_version.read_version()
    disagreements = [
        f"{path.relative_to(REPO_ROOT)}: {reader(path)!r}"  # type: ignore[operator]
        for path, reader, _writer in sync_version.TARGETS
        if reader(path) != version  # type: ignore[operator]
    ]
    assert not disagreements, f"VERSION says {version}, but: {disagreements}"


def test_the_sync_script_check_mode_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "sync_version.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_installer_declares_a_four_part_product_version() -> None:
    """NSIS's VIProductVersion needs four components; it must match the three-part one."""
    version = sync_version.read_version()
    nsi = (REPO_ROOT / "installer" / "ai-workbench.nsi").read_text(encoding="utf-8", errors="replace")
    assert f'!define APP_VERSION     "{version}"' in nsi
    assert f'VIProductVersion "{version}.0"' in nsi


def test_backend_reports_the_same_version() -> None:
    from app.core.config import settings

    assert settings.app_version == sync_version.read_version()


def test_backend_does_not_fall_back_to_a_plausible_version() -> None:
    """A missing VERSION must look wrong, not look like a release."""
    from app.core import config as config_module

    original = config_module.PROJECT_ROOT
    try:
        config_module.PROJECT_ROOT = Path("Z:/definitely-not-here")
        assert config_module._read_version() == "0.0.0-unknown"
    finally:
        config_module.PROJECT_ROOT = original


def test_frontend_and_shell_metadata_match() -> None:
    version = sync_version.read_version()
    for relative in (
        "desktop/src-tauri/tauri.conf.json",
        "desktop/package.json",
        "frontend/package.json",
    ):
        data = json.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))
        assert data["version"] == version, relative


def test_changelog_documents_the_current_version() -> None:
    version = sync_version.read_version()
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8", errors="replace")
    assert f"## [{version}]" in changelog


def test_version_file_is_shipped_in_the_installer_payload() -> None:
    """An installed copy has to be able to report its own version."""
    payload = (REPO_ROOT / "scripts" / "build_installer_payload.py").read_text(encoding="utf-8")
    assert '("VERSION", "VERSION")' in payload


def test_pytest_addopts_has_no_duplicate_quiet_flag() -> None:
    """`-q` here plus `-q` on the command line is `-qq`, which hides the summary."""
    pyproject = (REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^addopts\s*=\s*"([^"]*)"', pyproject, re.MULTILINE)
    assert match is not None
    assert "-q" not in match.group(1).split()


@pytest.mark.parametrize("target", [p for p, _r, _w in sync_version.TARGETS])
def test_sync_targets_exist(target: Path) -> None:
    assert target.exists(), target

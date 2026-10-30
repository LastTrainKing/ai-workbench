"""Release-freeze regression tests — local service exposure and Qdrant authentication.

The confirmed defects, both in files that ship:

* ``docker-compose.yml`` published PostgreSQL, Redis, Qdrant, the API and the frontend as
  ``${PORT}:PORT``, which binds every interface. PostgreSQL's password defaulted to
  ``aiworkbench``, Redis had no authentication at all and Qdrant had none — on a file
  whose header invited the reader to trust it.
* ``scripts/infra-start.ps1`` generated a Qdrant config with ``enable_cors: true``. The
  Python client never sent a key, because none was configured anywhere.

These are static assertions on the files, which is what can be verified on a machine with
no Docker. The runtime checks (does compose actually start, does Qdrant reject an
unauthenticated request) are marked BLOCKED in docs/archive/ACCEPTANCE_MATRIX.md rather than claimed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"
INFRA_START = REPO_ROOT / "scripts" / "infra-start.ps1"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# ------------------------------------------------------------------- exposure --


def test_data_services_are_not_published_to_the_host(compose: dict) -> None:
    """The database, the cache and the vector store are reachable only inside the network."""
    for name in ("postgres", "redis", "qdrant"):
        service = compose["services"][name]
        assert not service.get("ports"), f"{name} is still published to the host"
        assert service.get("expose"), f"{name} should expose its port to the network"


def test_every_published_port_is_bound_to_loopback(compose: dict) -> None:
    """Anything the host can reach must be reachable from this machine only."""
    for name, service in compose["services"].items():
        for entry in service.get("ports") or []:
            text = str(entry)
            assert text.startswith("127.0.0.1:"), (
                f"{name} publishes {text!r}, which binds every interface"
            )


def test_no_weak_default_credentials_remain(compose: dict) -> None:
    """A `:-default` on a credential is a shipped password."""
    text = COMPOSE.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD:-" not in text
    assert "REDIS_PASSWORD:-" not in text
    assert "QDRANT_API_KEY:-" not in text
    # ... and the required form is present, so compose refuses rather than defaults.
    assert "${POSTGRES_PASSWORD:?" in text
    assert "${REDIS_PASSWORD:?" in text
    assert "${QDRANT_API_KEY:?" in text


def test_redis_requires_a_password(compose: dict) -> None:
    command = compose["services"]["redis"]["command"]
    assert "--requirepass" in command


def test_qdrant_disables_cors_and_requires_a_key(compose: dict) -> None:
    env = compose["services"]["qdrant"]["environment"]
    assert env["QDRANT__SERVICE__ENABLE_CORS"] == "false"
    assert "QDRANT__SERVICE__API_KEY" in env


def test_api_and_worker_pass_the_qdrant_key(compose: dict) -> None:
    """A key Qdrant requires but the client never sends is an outage, not a fix."""
    for name in ("api", "worker"):
        env = compose["services"][name]["environment"]
        assert "QDRANT_API_KEY" in env


def test_api_and_worker_use_authenticated_urls(compose: dict) -> None:
    for name in ("api", "worker"):
        env = compose["services"][name]["environment"]
        # The password must be interpolated with no fallback. `${POSTGRES_DB:-aiworkbench}`
        # is fine — a database *name* is not a credential.
        assert "${POSTGRES_PASSWORD}" in env["DATABASE_URL"]
        assert "${POSTGRES_PASSWORD:-" not in env["DATABASE_URL"]
        assert "${REDIS_PASSWORD}" in env["REDIS_URL"]
        assert "${REDIS_PASSWORD:-" not in env["REDIS_URL"]


# -------------------------------------------------------------------- qdrant --


def test_infra_script_does_not_enable_cors() -> None:
    """Asserted on live config lines, not on the whole file.

    The script's own comment quotes the old setting while explaining why it is gone, so a
    naive substring search over the file would fail on the explanation.
    """
    active = [
        line.strip()
        for line in INFRA_START.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any(line.startswith("enable_cors: true") for line in active)
    assert any(line.startswith("enable_cors: false") for line in active)


def test_infra_script_generates_and_persists_a_key() -> None:
    """The key has to be the same value on both sides, across restarts."""
    text = INFRA_START.read_text(encoding="utf-8")
    assert "RandomNumberGenerator" in text, "the key must be generated, not hard-coded"
    assert "qdrant-api-key.txt" in text, "the key must persist so the backend can find it"
    assert "api_key: $env:QDRANT_API_KEY" in text


def test_backend_reads_the_launcher_key_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backend and the launcher are separate processes; the file is how they agree."""
    from app.core import config as config_module
    from app.core.config import Settings

    key_file = tmp_path / "qdrant-api-key.txt"
    key_file.write_text("generated-key-value", encoding="utf-8")
    monkeypatch.setattr(config_module, "RUNTIME_ROOT", tmp_path)

    settings = Settings(_env_file=None, qdrant_api_key="")
    assert settings.qdrant_api_key == "generated-key-value"

    # An explicit value always wins over the file.
    explicit = Settings(_env_file=None, qdrant_api_key="explicit-key")
    assert explicit.qdrant_api_key == "explicit-key"


def test_backend_tolerates_a_missing_key_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import config as config_module
    from app.core.config import Settings

    monkeypatch.setattr(config_module, "RUNTIME_ROOT", tmp_path)
    settings = Settings(_env_file=None, qdrant_api_key="")
    assert settings.qdrant_api_key == ""


# ------------------------------------------------------------- env template --


def test_env_example_has_no_secret_value() -> None:
    """The template shipped a value that looked like a key and passed the length check."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    match = re.search(r"^SECRET_KEY=(.*)$", text, re.MULTILINE)
    assert match is not None
    assert match.group(1).strip() == "", "the template must not ship a SECRET_KEY value"


def test_env_example_warns_that_it_is_not_an_install_template() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "NOT AN INSTALLATION TEMPLATE" in text

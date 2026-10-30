"""First-run production environment.

The installer used to copy ``.env.example`` to ``.env``. That template selects
``APP_ENV=dev``, sets ``DEBUG=true`` and carries a placeholder ``SECRET_KEY`` — and in
that configuration the backend seeds ``demo / demo1234``. So a fresh install came up in
development mode, with a known account, on a machine where this product reads files, runs
commands and reaches the network. The sign-in screen even printed the credentials.

This module writes the production file instead, and it is deliberately the only thing
that writes it:

* **A fresh install gets a new random key.** 48 bytes from ``secrets.token_urlsafe``,
  which is ~64 characters and well past the 32 the validator requires.
* **An existing install keeps its file, byte for byte.** Re-running the installer or the
  launcher must not rotate the key: it signs the user's sessions, and rotating it silently
  logs everybody out and can strand data encrypted under the old value.

It is called before the API starts, from the launcher — not from ``main.py``, because
``Settings`` reads ``.env`` at import time and a file written after that would not be
picked up by the process that wrote it.
"""

from __future__ import annotations

import contextlib
import secrets
from pathlib import Path

#: Names that must never appear in a generated file, as a belt-and-braces check that this
#: module did not fall back to the example template.
_FORBIDDEN = ("APP_ENV=dev", "DEBUG=true", "change-me")

_TEMPLATE = """\
# =============================================================================
# AI Workbench — production environment
#
# Written once, on first run. Edited by hand if you need to.
# Regenerating this file replaces SECRET_KEY and logs every session out; keep a
# copy if you are changing anything here.
# =============================================================================

APP_NAME=AI Workbench
# `prod` refuses to start on a placeholder or short SECRET_KEY, and turns off
# /docs, /redoc, /openapi.json and the interactive debug output.
APP_ENV=prod
DEBUG=false
API_V1_PREFIX=/api/v1
SECRET_KEY={secret_key}
ACCESS_TOKEN_EXPIRE_MINUTES=1440
CORS_ORIGINS=http://127.0.0.1:8000,http://localhost:8000

# --- services ---------------------------------------------------------------
# These default to the processes the launcher starts on loopback. Change them
# only if you run the dependencies yourself.
DATABASE_URL=postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/aiworkbench
REDIS_URL=redis://127.0.0.1:6379/0
QDRANT_URL=http://127.0.0.1:6333

# --- AI ---------------------------------------------------------------------
# No key ships with the product. Configure one in Settings, or set it here.
# AI_PROVIDER=deepseek
# DEEPSEEK_API_KEY=

LOG_LEVEL=INFO
METRICS_ENABLED=true
"""


def generate_secret_key() -> str:
    """A fresh key, long enough that the production validator accepts it."""
    return secrets.token_urlsafe(48)


def render_production_env(secret_key: str) -> str:
    """The file contents for a given key. Split out so it can be asserted on."""
    text = _TEMPLATE.format(secret_key=secret_key)
    for forbidden in _FORBIDDEN:
        if forbidden in text:
            raise AssertionError(
                f"the generated production environment contains {forbidden!r}; "
                "a shipped file must never select development mode"
            )
    return text


def ensure_production_env(root: Path) -> Path | None:
    """Write ``<root>/.env`` if it is absent. Returns the path, or None if kept.

    Returning ``None`` for "already present" rather than rewriting is the whole point:
    this runs on every launch, and a key that changes on every launch is worse than the
    placeholder it replaced.
    """
    env_path = Path(root) / ".env"
    if env_path.exists():
        return None

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        render_production_env(generate_secret_key()), encoding="utf-8", newline="\n"
    )
    # Best effort: the file holds the token-signing key.
    with contextlib.suppress(OSError):
        env_path.chmod(0o600)
    return env_path


def secret_key_value(text: str) -> str:
    """The active ``SECRET_KEY=`` value in an env file, ignoring comments."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("SECRET_KEY="):
            return stripped.split("=", 1)[1].strip()
    return ""


def env_problems(env_path: Path) -> list[str]:
    """Why an existing ``.env`` is not safe to run in production (empty = fine).

    Used by the installer's self-check and by tests. It reads the file as text rather
    than importing ``Settings``, so it can be run against a file that is not the one this
    process loaded.

    The key is judged by ``config._secret_problems`` — the same function the production
    startup gate uses. A second implementation of "is this a good secret" is how the two
    come to disagree, and the whole point of the gate is that it is the one that decides.
    """
    from app.core.config import _secret_problems

    if not env_path.is_file():
        return ["no .env file"]
    text = env_path.read_text(encoding="utf-8", errors="replace")
    # Only active lines: the generated file's own comments talk about placeholders and
    # development mode, and matching those would report the file that fixes the problem.
    active = [
        line.strip().lower()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    problems: list[str] = []
    if any(line == "app_env=dev" for line in active):
        problems.append("APP_ENV=dev")
    if any(line == "debug=true" for line in active):
        problems.append("DEBUG=true")
    problems.extend(_secret_problems(secret_key_value(text)))
    return problems

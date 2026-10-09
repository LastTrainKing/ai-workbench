"""Where a native build finds its files — frozen and from a source checkout alike.

The entry point used to compute its own paths from `__file__`:

    HERE = Path(__file__).resolve().parent
    BACKEND = HERE.parent / "backend"

That is correct in a checkout and wrong in every packaged build, and wrong in a way that
does not announce itself. PyInstaller puts the Python modules and the data files under a
temporary bundle directory (`sys._MEIPASS`, `_internal` in a one-folder build), while
`sys.executable` is the `.exe` the user double-clicked. So `HERE.parent / "backend"` either
does not exist or, worse, resolves to a directory that happens to exist and is the wrong
one — a build that starts and reads the wrong files.

Two questions are being answered here, and they are different questions:

* **Where are the read-only files?** QML, the backend package, the migrations, `VERSION`.
  These live inside the bundle and must never be written to.
* **Where is the user's data?** The database, uploads, logs, the vector index. These must
  live **outside** the bundle. That is not tidiness: a program directory that holds the
  user's database cannot be uninstalled without either deleting their data or leaving a
  directory behind, and the first attempt at this shipped exactly that and then tried to
  move the data aside with a rename that failed.

`configure()` has to run before anything from `app.*` is imported, because
`app.core.config` resolves the data directory once, at import time. A later assignment is a
no-op, which is the sort of bug that looks like "the setting is ignored".
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: The product's data directory name under the user's profile. One name, so the installer,
#: the uninstaller and this module cannot disagree about which directory is the user's.
DATA_DIR_NAME = "AI Workbench Data"

#: The environment variable `app.core.config` reads first. Set by `configure()`.
DATA_DIR_ENV = "AI_WORKBENCH_DATA_DIR"


def is_frozen() -> bool:
    """True inside a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """The read-only directory holding the bundled files.

    Frozen: `sys._MEIPASS`, which PyInstaller points at the bundle (`_internal` for a
    one-folder build, the extraction directory for one-file). From source: the
    `desktop-native` directory, so `backend/` and `qml/` resolve the way they do today.
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return Path(meipass)
        # A one-file build without `_MEIPASS` is not a state to guess at; the directory
        # next to the executable is the only other place the files could be.
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def backend_dir() -> Path:
    """Where the backend package lives on disk.

    Two layouts, and they are not the same shape. A checkout has `<repo>/backend` next to
    `desktop-native/`; a frozen build has the modules inside the bundle, where PyInstaller's
    own importer finds them and a `sys.path` entry does nothing. Both are handled here so
    the entry point does not have to know which one it is in.
    """
    if is_frozen():
        return bundle_root() / "backend"
    return bundle_root().parent / "backend"


def backend_importable() -> bool:
    """Whether `import app` would succeed — the question `backend_dir().is_dir()` answers
    wrongly in a frozen build, where the package is inside the archive and the directory
    does not exist."""
    import importlib.util

    try:
        return importlib.util.find_spec("app") is not None
    except (ImportError, ValueError):
        return False


def qml_dir() -> Path:
    """The QML sources the engine loads."""
    return bundle_root() / "qml"


def version() -> str:
    """The product version, from the one `VERSION` file.

    Returns a visibly wrong value when the file is missing rather than a plausible one. An
    install that cannot find its own version file should look broken, not look like
    `1.0.0`.

    Three candidates, because the file sits in a different place in each layout: inside the
    bundle in a packaged build, at the repository root in a checkout (where `bundle_root()`
    is `desktop-native/`), and beside the executable for an unpacked build.
    """
    candidates = [
        bundle_root() / "VERSION",
        bundle_root().parent / "VERSION",
        Path(sys.executable).resolve().parent / "VERSION",
    ]
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "0.0.0-unknown"


def default_data_dir() -> Path:
    """Where this user's data goes when nothing says otherwise.

    `%LOCALAPPDATA%` on Windows, `~/.local/share` elsewhere. Both are per-user and outside
    the program directory, which is what makes an uninstall two independent decisions
    instead of a choice between the program and the data.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        base = os.environ.get("XDG_DATA_HOME", "").strip()
        root = Path(base) if base else Path.home() / ".local" / "share"
    return root / DATA_DIR_NAME


def checkout_runtime_root() -> Path:
    """Where a **source checkout** keeps its data, mirroring `app.core.config`.

    `backend/app/core/config.py` resolves `<repo>/.runtime` when nothing overrides it, and
    every tool in this repository — the verifiers, the tests, the launcher — already points
    there. So the checkout case must not be "improved" here: an entry point that silently
    relocated a developer's data directory would look like the tests had lost their
    database.
    """
    return bundle_root().parent / ".runtime"


def data_dir() -> Path:
    """The data directory in force, resolved the same way the backend resolves it."""
    explicit = os.environ.get(DATA_DIR_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if is_frozen():
        # Inside a bundle the backend's own fallback would be `<bundle>/.runtime`, which is
        # in the program directory — see the module docstring.
        return default_data_dir()
    return checkout_runtime_root()


def configure(*, create: bool = True) -> dict[str, object]:
    """Point the process at the right files and the right data directory.

    Must be called **before** importing anything from `app.*`: `app.core.config` resolves
    the data directory once at import time, so setting the variable afterwards changes
    nothing and the program quietly uses the wrong one.

    Returns a report rather than raising. The caller decides what an unusable environment
    means — a verification run wants to record it as `BLOCKED` and continue, while a real
    launch wants to say so and stop.
    """
    root = bundle_root()
    backend = backend_dir()

    # The service layer is imported, not called over HTTP. Putting the backend directory
    # on the path is what makes that possible; there is no second copy of the code.
    for candidate in (str(backend), str(root)):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)

    explicit = os.environ.get(DATA_DIR_ENV, "").strip()
    # Only a packaged build forces the location. A checkout keeps the backend's own
    # resolution, so this entry point cannot disagree with the rest of the repository
    # about where a developer's data is.
    forced = is_frozen() and not explicit
    if forced:
        os.environ[DATA_DIR_ENV] = str(default_data_dir())

    target = data_dir()
    created = False
    error = ""
    if create:
        try:
            target.mkdir(parents=True, exist_ok=True)
            created = True
        except OSError as exc:
            error = f"{type(exc).__name__}: {exc}"

    return {
        "frozen": is_frozen(),
        "bundle_root": str(root),
        "backend_dir": str(backend),
        "backend_importable": backend_importable(),
        "qml_dir": str(qml_dir()),
        "qml_present": qml_dir().is_dir(),
        "data_dir": str(target),
        "data_dir_source": "environment"
        if explicit
        else ("default" if forced else "checkout"),
        "data_dir_created": created,
        "data_dir_error": error,
        "version": version(),
        "executable": sys.executable,
    }


__all__ = [
    "DATA_DIR_ENV",
    "DATA_DIR_NAME",
    "backend_dir",
    "backend_importable",
    "bundle_root",
    "checkout_runtime_root",
    "configure",
    "data_dir",
    "default_data_dir",
    "is_frozen",
    "qml_dir",
    "version",
]

# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone native desktop build.

**Why a spec file and not a command line.** A command line is a set of flags someone has to
reconstruct correctly every time; a spec is the build, checked in, reviewed and diffed. The
two things that matter here — which data files travel with the executable, and where they
land relative to it — are exactly the things a mistyped flag gets silently wrong.

**One folder, not one file.** A one-file build extracts the whole runtime to a temporary
directory on every launch: several hundred megabytes of Qt and Python written and deleted
each time the user opens the app, and a `sys._MEIPASS` that changes on every run, which is
hostile to anything that wants a stable path. One folder starts immediately and has a
layout that can be inspected — which is also what makes the acceptance check able to look
at it.

**Where the data files go, and why it matters.** PyInstaller sets `sys._MEIPASS` to
`_internal/` in a one-folder build, and the frozen backend resolves its own root as
`Path(__file__).resolve().parents[2]` — which is `_internal` too. So every data file is
placed relative to `_internal`, not next to the `.exe`:

    _internal/VERSION          read by app.core.config._read_version
    _internal/alembic.ini      read by app.core.schema_guard.ALEMBIC_INI
    _internal/alembic/         the migrations themselves
    _internal/qml/             the interface, loaded by awbnative.resources.qml_dir

Putting them next to the `.exe` instead produces a build that starts and then reports its
version as `0.0.0-unknown` and cannot find a single migration — a failure that looks like a
broken application rather than a misplaced file.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

REPO = Path(SPECPATH).resolve().parent
DESKTOP = REPO / "desktop-native"
BACKEND = REPO / "backend"

# --------------------------------------------------------------------------- #
# Data files — everything read at runtime that is not a Python module
# --------------------------------------------------------------------------- #
datas: list[tuple[str, str]] = [
    (str(REPO / "VERSION"), "."),
    (str(BACKEND / "alembic.ini"), "."),
    (str(BACKEND / "alembic"), "alembic"),
    (str(DESKTOP / "qml"), "qml"),
    # The application icon, so the *window* can show it. The `icon=` argument below embeds
    # it in the executable, which is what Explorer, the shortcuts and the installer use —
    # but the window icon comes from `QGuiApplication::windowIcon()` at runtime, and that
    # needs the file. Without this line the executable's icon is right and the window's is
    # the Windows placeholder, which is exactly what shipped.
    (str(REPO / "assets" / "ai-workbench.ico"), "assets"),
]

# --------------------------------------------------------------------------- #
# Imports PyInstaller cannot see
# --------------------------------------------------------------------------- #
# The backend reaches most of its dependencies through string names — SQLAlchemy dialects,
# Alembic's migration scripts, the async drivers — so a static walk of the import graph
# finds the top-level packages and misses the modules that only exist at runtime. The
# failure mode is an ImportError on the user's machine that never appeared in development.
hiddenimports: list[str] = []
hiddenimports += collect_submodules("app")
hiddenimports += [
    "alembic",
    "alembic.config",
    "alembic.command",
    "alembic.script",
    "alembic.runtime.migration",
    "sqlalchemy.dialects.postgresql",
    "sqlalchemy.dialects.postgresql.asyncpg",
    "asyncpg",
    "pydantic_settings",
    "passlib.hash",
    "bcrypt",
    "redis",
    "redis.asyncio",
    "qdrant_client",
    "qdrant_client.http",
    "qdrant_client.local.qdrant_local",
    "httpx",
    "httpcore",
    "anyio",
    # `alembic/env.py` calls `fileConfig` from `logging.config`, and nothing imports it
    # statically, so PyInstaller left it out. The symptom was not a missing-logging warning:
    # it was `ModuleNotFoundError: No module named 'logging.config'` raised while running the
    # migrations, reported to the user as "无法连接数据库" — a database problem that was
    # actually a packaging problem. Found by starting the installed application against an
    # empty data directory and letting it build the schema, which is the only way this class
    # of gap shows up.
    "logging.config",
    "logging.handlers",
    # The Qt binding layer, named explicitly. PySide6's hook collects these on its own, and
    # a previous version of this spec excluded `shiboken6.Shiboken` by mistake and shipped
    # a build that could not import PySide6 at all. Listing them here means a future
    # exclusion of the same name shows up as a missing-module warning at build time rather
    # than as a fatal dialog on the user's machine.
    "shiboken6",
    "shiboken6.Shiboken",
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtQml",
    "PySide6.QtQuick",
]

# --------------------------------------------------------------------------- #
# Exclusions — things that would be bundled, and must not be
# --------------------------------------------------------------------------- #
excludes = [
    # The test suite is not part of the product, and it carries fixtures that look like
    # credentials. Bundling it would ship both the tests and the markers.
    "tests",
    "pytest",
    "pytest_asyncio",
    "_pytest",
    # GUI toolkits this application does not use. PySide6 pulls in the whole Qt surface if
    # asked for QtQml, and every unused module is tens of megabytes.
    "tkinter",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia",
    "PySide6.QtQuick3D",
    "PySide6.QtTest",
    # **`shiboken6.Shiboken` was in this list, and it must never be again.** It is not an
    # unused module: it is the C++ binding layer every PySide6 class is built on, and
    # excluding it produced a build that failed at `import PySide6` with
    # "No module named 'shiboken6.Shiboken'". The exclusion looked reasonable — the name
    # reads like an internal detail of a package we already bundle — and that is exactly
    # why it is worth a comment rather than a line. `verify_native_package.py` now checks
    # for the extension file by name, so the same mistake cannot ship twice.
    #
    # Notebook and plotting stacks that some dependency mentions but nothing imports.
    "matplotlib",
    "numpy.f2py",
    "pandas",
    "IPython",
    "notebook",
    "docutils",
]

a = Analysis(  # noqa: F821 - injected by PyInstaller
    [str(DESKTOP / "main.py")],
    pathex=[str(BACKEND), str(DESKTOP)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)  # noqa: F821

# --------------------------------------------------------------------------- #
# Pin the OpenSSL libraries to the ones `_ssl.pyd` was built against
# --------------------------------------------------------------------------- #
# The first packaged build reached the database over a plain connection and failed every TLS
# handshake with `SSLError: [SSL] system lib`. The cause was here: `_ssl.pyd` was bundled
# byte-identical to the interpreter's, but `libssl-3-x64.dll` and `libcrypto-3-x64.dll` were
# **not** — PyInstaller had collected OpenSSL from somewhere else on the machine, and a
# mismatched OpenSSL pair against a fixed `_ssl.pyd` produces exactly that: contexts are
# created (the basic symbols resolve) and handshakes fail at the OpenSSL level.
#
# So the two libraries are taken from the interpreter's own `DLLs` directory and any other
# copy is removed. `_ssl.pyd` and its OpenSSL must travel together; the interpreter is the
# only place that pairing is guaranteed.
_PYTHON_DLLS = Path(sys.base_prefix) / "DLLs"  # noqa: F821
_OPENSSL_DLLS = ("libssl-3-x64.dll", "libcrypto-3-x64.dll")
for _name in _OPENSSL_DLLS:
    _src = _PYTHON_DLLS / _name
    if not _src.is_file():
        continue
    a.binaries = [  # noqa: F821
        entry for entry in a.binaries if Path(entry[0]).name.lower() != _name.lower()
    ]
    a.binaries.append((_name, str(_src), "BINARY"))

# A guard on the guard, in the same spirit as the Shiboken check below: verify the pinned
# copies are the ones that ended up in the product, so a future change to the collection
# order cannot quietly reintroduce the mismatch.
_bundled_openssl = {
    Path(name).name.lower(): path for name, path, _kind in a.binaries  # noqa: F821
}
for _name in _OPENSSL_DLLS:
    _src = _PYTHON_DLLS / _name
    _got = _bundled_openssl.get(_name.lower())
    if _src.is_file() and (_got is None or Path(_got).resolve() != _src.resolve()):
        raise SystemExit(
            f"构建中止：{_name} 不是解释器自带的那一份（当前取到 {_got}）。\n"
            "OpenSSL 与 _ssl.pyd 必须成对；版本不一致时 TLS 握手会失败，\n"
            "而普通连接仍然可用 —— 这个组合很容易被当成「数据库有问题」。"
        )

# --------------------------------------------------------------------------- #
# A guard on the guard
# --------------------------------------------------------------------------- #
# `shiboken6.Shiboken` was once in `excludes`, and the build "succeeded": PyInstaller had
# no reason to complain, and the failure only appeared when a user launched the executable
# and got "No module named 'shiboken6.Shiboken'". Checking the analysis result here turns a
# silent packaging mistake into a build that stops.
#
# The check is on `a.binaries` rather than on the exclusion list, because the exclusion
# list is not what matters: whether the file is in the product is. (PyInstaller 6.22's
# `Analysis` has no `excludedimports` attribute; an earlier version of this guard read it
# and broke the build.)
_binding = [
    name
    for name, _path, _kind in a.binaries  # noqa: F821
    if name.replace("\\", "/").endswith("shiboken6/Shiboken.pyd")
]
if not _binding:
    raise SystemExit(
        "构建中止：shiboken6/Shiboken.pyd 没有进入产物。\n"
        "PySide6 的每一个类都建立在它之上；缺少它时产物会在 import PySide6 时崩溃，\n"
        "而且是以「窗口子系统的致命错误对话框」的形式卡住，不会打印回溯。\n"
        "请检查 spec 的 excludes 是否又排除了 shiboken6.Shiboken。"
    )

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI-Workbench-Native",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # The application's own icon, and **the only place it is set**. Both shortcuts and the
    # registry's `DisplayIcon` point at this executable with index 0, so they show whatever
    # is embedded here. Without this line PyInstaller embeds its own default, which is what
    # shipped: an icon that says "this is a PyInstaller program" on the desktop.
    icon=str(REPO / "assets" / "ai-workbench.ico"),
    # A GUI subsystem executable: no console window flashes up behind the app. The cost is
    # that stdout goes nowhere, which is why `--self-check` can write its report to a file.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AI-Workbench-Native",
)

if sys.platform != "win32":  # pragma: no cover - the target is Windows
    print("note: this spec targets Windows; the bundle will use the host's Qt libraries")

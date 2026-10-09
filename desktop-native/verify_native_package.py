"""Acceptance for the packaged native build: ten checks, and the state of each.

    .venv\\Scripts\\python.exe desktop-native\\verify_native_package.py

Every check reports one of three states, and the third is the reason this is not a boolean:

    PASS     the thing works
    FAIL     the thing is present and broken — a defect in this build
    BLOCKED  the thing is absent from this machine — PostgreSQL, Redis or Qdrant is not
             running, and no amount of rebuilding will change that

Collapsing `BLOCKED` into `FAIL` would make the result depend on which services happen to
be up on the machine running the check rather than on whether the package works, and the
honest verdict for "cannot be determined here" is not "failed".

The checks are ordered from static to dynamic: the first six inspect the built directory,
the last four **run the built executable**. A verifier that only inspects files can be
satisfied by a directory full of correctly named things that do not start, which is exactly
the build this is meant to catch.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"

#: Files that must never appear in a package. `.env` holds the deployment's secrets; a
#: database file is the user's data; `data-dir.txt` is an install-time marker that belongs
#: to the installer, not the product.
FORBIDDEN_NAMES = {".env", "data-dir.txt"}
#: Credential *containers*. Deliberately not `.pem`: `certifi/cacert.pem` and gRPC's
#: `roots.pem` are public CA trust stores that every TLS library ships, and the first run of
#: this check failed on them. A private key is identified by its content, not its extension —
#: `SECRET_PATTERNS` below looks for the `BEGIN … PRIVATE KEY` header, which a `.pem` that
#: matters will have.
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".key", ".p12", ".pfx", ".jks"}
#: Key material that is never a certificate bundle, whatever it is called.
FORBIDDEN_KEY_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}

#: Byte patterns that mean a credential was baked into the build. Deliberately specific:
#: a loose `sk-...` pattern matches inside binaries often enough to make the check noise,
#: and a noisy check is one people learn to ignore.
SECRET_PATTERNS = [
    (re.compile(rb"sk-[A-Za-z0-9]{24,}"), "an API key shaped like sk-…"),
    (re.compile(rb"sk-bd1b631d"), "the round-4 verification key"),
    (re.compile(rb"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"), "a private key"),
    (re.compile(rb'"api_key"\s*:\s*"[A-Za-z0-9_\-]{20,}"'), "an api_key value in JSON"),
]

#: Extensions scanned for secrets in full. The executable is included because PyInstaller
#: embeds the compiled Python archive inside it, so a key in a module would be there.
TEXT_SUFFIXES = {
    ".py",
    ".pyi",
    ".json",
    ".txt",
    ".ini",
    ".cfg",
    ".toml",
    ".md",
    ".qml",
    ".js",
    ".mako",
    ".yaml",
    ".yml",
    ".env",
    ".spec",
    ".bat",
    ".ps1",
    # Certificate and key files are scanned rather than banned by name: a public CA bundle
    # is not a credential, and a private key announces itself in its content.
    ".pem",
    ".crt",
    ".cnf",
}


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[int, str, str, str]] = []

    def add(self, index: int, label: str, state: str, detail: str = "") -> None:
        self.rows.append((index, label, state, detail))

    @property
    def failed(self) -> list[tuple[int, str, str, str]]:
        return [r for r in self.rows if r[2] == FAIL]

    @property
    def blocked(self) -> list[tuple[int, str, str, str]]:
        return [r for r in self.rows if r[2] == BLOCKED]


# --------------------------------------------------------------------------- #
# Static checks — what is in the package
# --------------------------------------------------------------------------- #
def check_executable(app: Path) -> tuple[str, str]:
    exe = app / "AI-Workbench-Native.exe"
    if not exe.is_file():
        return FAIL, f"missing {exe}"
    size = exe.stat().st_size
    if size < 1_000_000:
        return FAIL, f"the executable is only {size} bytes"
    return PASS, f"{exe.name}, {size / 1_048_576:.1f} MiB"


def check_qt_runtime(app: Path) -> tuple[str, str]:
    """The Qt shared libraries, the binding layer, and the platform plugin.

    `qwindows.dll` is the one that is easy to lose and impossible to work around: without
    it the application starts, finds no platform plugin, and exits with a message that
    reads like a display problem.

    `shiboken6/Shiboken.pyd` is checked **by name** because this build's own spec once
    excluded it, producing a package that died at `import PySide6` with "No module named
    'shiboken6.Shiboken'". A file that has already gone missing once gets a check that
    names it.
    """
    internal = app / "_internal"
    required = [
        "PySide6/Qt6Core.dll",
        "PySide6/Qt6Gui.dll",
        "PySide6/Qt6Qml.dll",
        "PySide6/Qt6Quick.dll",
        "shiboken6/Shiboken.pyd",
    ]
    missing = [name for name in required if not (internal / name).is_file()]
    if missing:
        return FAIL, "missing " + ", ".join(missing)

    platforms = list(internal.glob("PySide6/plugins/platforms/*.dll"))
    if not any(p.name.startswith("qwindows") for p in platforms):
        return (
            FAIL,
            f"no qwindows platform plugin (found {[p.name for p in platforms] or 'none'})",
        )
    if not any(p.name.startswith("qoffscreen") for p in platforms):
        # Not fatal for a desktop launch, but `--self-check` needs it and the acceptance
        # run below would then be unable to prove anything.
        return FAIL, "no qoffscreen platform plugin; the headless self-check cannot run"
    return PASS, f"Qt libraries, Shiboken binding, {len(platforms)} platform plugins"


def check_qml_resources(app: Path) -> tuple[str, str]:
    """Our interface, and the Qt QML modules it imports."""
    internal = app / "_internal"
    ours = sorted((HERE / "qml").rglob("*.qml"))
    if not ours:
        return FAIL, "no QML sources in the repository to compare against"
    missing = [
        str(path.relative_to(HERE / "qml"))
        for path in ours
        if not (internal / "qml" / path.relative_to(HERE / "qml")).is_file()
    ]
    if missing:
        return FAIL, "missing interface files: " + ", ".join(missing)

    for module in (
        "QtQuick",
        "QtQml",
        "QtQuick/Controls",
        "QtQuick/Layouts",
        "QtQuick/Window",
    ):
        if not (internal / "PySide6/qml" / module).is_dir():
            return FAIL, f"missing Qt QML module {module}"
    return PASS, f"{len(ours)} interface files and the Qt QML modules"


def check_migrations(app: Path) -> tuple[str, str]:
    """The migrations and the Alembic config that points at them.

    A packaged build that cannot find its migrations cannot create a database on a machine
    that has never run the product — which is the only machine a first run happens on.
    """
    internal = app / "_internal"
    if not (internal / "alembic.ini").is_file():
        return FAIL, "alembic.ini is not in the bundle"
    if not (internal / "alembic/env.py").is_file():
        return FAIL, "alembic/env.py is not in the bundle"

    repo_versions = sorted((REPO / "backend/alembic/versions").glob("*.py"))
    built_versions = sorted((internal / "alembic/versions").glob("*.py"))
    if len(built_versions) != len(repo_versions):
        return (
            FAIL,
            f"{len(built_versions)} migrations bundled, {len(repo_versions)} in the repository",
        )
    return PASS, f"{len(built_versions)} migrations"


def check_version(app: Path) -> tuple[str, str]:
    internal = app / "_internal"
    try:
        built = (internal / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return FAIL, "VERSION is not in the bundle"
    try:
        expected = (REPO / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return FAIL, "the repository has no VERSION file to compare against"
    if built != expected:
        return FAIL, f"bundle says {built!r}, repository says {expected!r}"
    return PASS, built


def check_no_secrets(app: Path) -> tuple[str, str]:
    """Nothing in the package is a credential, or the user's data, or a leftover marker."""
    problems: list[str] = []
    scanned = 0
    for path in app.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name in FORBIDDEN_NAMES or name in FORBIDDEN_KEY_NAMES:
            problems.append(f"forbidden file {path.relative_to(app)}")
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            problems.append(f"forbidden file {path.relative_to(app)}")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.suffix.lower() != ".exe":
            continue
        try:
            if path.stat().st_size > 64 * 1024 * 1024:
                continue
            blob = path.read_bytes()
        except OSError as exc:
            problems.append(f"unreadable {path.relative_to(app)}: {exc}")
            continue
        scanned += 1
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(blob):
                problems.append(f"{label} in {path.relative_to(app)}")

    for directory in app.rglob("*"):
        if directory.is_dir() and directory.name in {".runtime", "runtime"}:
            problems.append(
                f"a data directory was bundled: {directory.relative_to(app)}"
            )

    if problems:
        return FAIL, "; ".join(problems[:5])
    return PASS, f"{scanned} text files and the executable scanned, nothing found"


# --------------------------------------------------------------------------- #
# Dynamic checks — what the package does when it runs
# --------------------------------------------------------------------------- #
def _run_self_check(
    app: Path,
    *,
    data_dir: Path | None = None,
    sanitized: bool = False,
    timeout: float = 120.0,
) -> tuple[int, dict | None, str, float]:
    """Run the packaged executable's self-check and read its report.

    The timeout is short on purpose. A windowed executable that fails during import does not
    print a traceback and exit — the bootloader puts up a modal "Fatal Error" dialog and
    waits, so a broken build *hangs* instead of failing. Two minutes is far longer than a
    healthy start needs and far shorter than a person's patience for a stuck check.
    """
    exe = app / "AI-Workbench-Native.exe"
    report = Path(tempfile.mkdtemp(prefix="awbnative-report-")) / "report.json"

    env = os.environ.copy()
    if data_dir is not None:
        env["AI_WORKBENCH_DATA_DIR"] = str(data_dir)
    if sanitized:
        # Prove the package carries its own interpreter: remove every hint of the host's
        # Python, and reduce PATH to the system directories.
        for key in (
            "PYTHONHOME",
            "PYTHONPATH",
            "VIRTUAL_ENV",
            "PYTHONSTARTUP",
            "PYTHONEXECUTABLE",
        ):
            env.pop(key, None)
        system_root = env.get("SystemRoot", r"C:\Windows")
        env["PATH"] = ";".join(
            [f"{system_root}\\system32", system_root, f"{system_root}\\system32\\Wbem"]
        )

    started = time.monotonic()
    try:
        completed = subprocess.run(
            [str(exe), "--self-check", "--report", str(report)],
            check=False,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(app),
        )
    except subprocess.TimeoutExpired:
        return (
            124,
            None,
            (
                f"the executable did not finish within {timeout:.0f}s — a windowed build "
                "that fails during import shows a modal dialog and waits rather than exiting"
            ),
            time.monotonic() - started,
        )

    elapsed = time.monotonic() - started
    payload = None
    if report.is_file():
        try:
            payload = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return (
                completed.returncode,
                None,
                f"the report is unreadable: {exc}",
                elapsed,
            )

    detail = (completed.stderr or completed.stdout or "").strip().splitlines()
    return completed.returncode, payload, (detail[-1] if detail else ""), elapsed


#: The report from the launch check, kept so the environment section does not pay for a
#: second start of the application. A verifier that starts the app five times to report
#: five things takes five times as long to tell you the same thing.
_LAUNCH_REPORT: dict | None = None


def check_launches(app: Path) -> tuple[str, str]:
    """The executable starts, builds its window, and reports its own UI checks.

    The environment results are **not** part of this verdict — they are reported separately
    below. This check answers one question: does the application work?
    """
    global _LAUNCH_REPORT
    code, payload, detail, elapsed = _run_self_check(app)
    if payload is None:
        return FAIL, f"no report (exit {code}, {elapsed:.1f}s) {detail}".strip()
    _LAUNCH_REPORT = payload
    checks = payload.get("checks") or []
    bad = [c for c in checks if not c.get("ok")]
    if bad:
        return FAIL, "; ".join(f"{c['label']}: {c.get('detail', '')}" for c in bad)
    if not checks:
        return FAIL, "the report contains no UI checks"
    return PASS, f"{len(checks)} UI checks passed in {elapsed:.1f}s (exit {code})"


def check_first_run_creates_data_dir(app: Path) -> tuple[str, str]:
    """A machine that has never run the product must get a data directory.

    The directory is deliberately *not* created beforehand: an application that only works
    when someone has already made its data directory works on the developer's machine and
    nowhere else.
    """
    with tempfile.TemporaryDirectory(prefix="awbnative-firstrun-") as tmp:
        target = Path(tmp) / "AI Workbench Data"
        if target.exists():
            return FAIL, "the harness created the directory it was meant to test"
        code, payload, detail, _elapsed = _run_self_check(app, data_dir=target)
        if payload is None:
            return FAIL, f"no report (exit {code}) {detail}".strip()
        if not target.is_dir():
            return FAIL, "the data directory was not created"
        reported = str(payload.get("data_dir") or "")
        if Path(reported) != target:
            return FAIL, f"the run used {reported!r} instead of {str(target)!r}"
        return PASS, f"created {target.name} and used it"


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    entries: dict[str, tuple[int, int]] = {}
    for path in root.rglob("*"):
        if path.is_file():
            try:
                stat = path.stat()
            except OSError:
                continue
            entries[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
    return entries


def check_install_directory_is_read_only(app: Path) -> tuple[str, str]:
    """Running the application must not write into its own directory.

    This is the check that catches the class of defect this project has already shipped
    once: a key file and a data directory written next to the program, which made the
    uninstaller unable to remove the install directory and made the installed copy behave
    differently from the development one. It is asserted by comparing a full snapshot of
    the package before and after a real run.
    """
    with tempfile.TemporaryDirectory(prefix="awbnative-isolation-") as tmp:
        before = _snapshot(app)
        code, payload, detail, _elapsed = _run_self_check(
            app, data_dir=Path(tmp) / "data"
        )
        if payload is None:
            return FAIL, f"no report (exit {code}) {detail}".strip()
        after = _snapshot(app)

    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    if added or removed or changed:
        parts = []
        if added:
            parts.append(f"added {added[:3]}")
        if removed:
            parts.append(f"removed {removed[:3]}")
        if changed:
            parts.append(f"modified {changed[:3]}")
        return FAIL, "the run wrote into the install directory: " + "; ".join(parts)
    return PASS, f"{len(before)} files unchanged"


def check_runs_without_host_python(app: Path) -> tuple[str, str]:
    """No host Python on PATH, no PYTHONHOME, no VIRTUAL_ENV — and it still starts."""
    code, payload, detail, _elapsed = _run_self_check(app, sanitized=True)
    if payload is None:
        return FAIL, f"no report without a host Python (exit {code}) {detail}".strip()
    executable = str(payload.get("executable") or "")
    expected = str(app / "AI-Workbench-Native.exe")
    if Path(executable).name != Path(expected).name:
        return FAIL, f"the process ran as {executable!r}, not the packaged executable"
    return PASS, f"ran as {Path(executable).name} with a sanitized environment"


# --------------------------------------------------------------------------- #
# The environment, reported but not scored
# --------------------------------------------------------------------------- #
def environment_rows(app: Path) -> list[tuple[str, str]]:
    """The state of PostgreSQL, Redis and Qdrant, from the launch run's own report.

    Reused rather than re-measured: the answer cannot change between two starts seconds
    apart, and starting the application again to ask the same question is time the check
    does not have to spend.
    """
    payload = _LAUNCH_REPORT
    if payload is None:
        code, payload, detail, _elapsed = _run_self_check(app)
        if payload is None:
            return [
                ("环境", f"{BLOCKED} — 无法取得报告（exit {code}）{detail}".strip())
            ]
    rows = []
    for entry in payload.get("environment") or []:
        state = entry.get("state", "?")
        label = {"ok": PASS, "blocked": BLOCKED, "failed": FAIL}.get(
            state, state.upper()
        )
        rows.append((entry.get("name", "?"), f"{label} — {entry.get('detail', '')}"))
    return rows


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
CHECKS = [
    ("可执行文件已生成", check_executable),
    ("Qt 运行时与平台插件已打包", check_qt_runtime),
    ("QML 界面与 Qt QML 模块已打包", check_qml_resources),
    ("Alembic 迁移已打包", check_migrations),
    ("VERSION 与仓库一致", check_version),
    ("包内没有密钥与用户数据", check_no_secrets),
    ("打包后的应用能启动并自检通过", check_launches),
    ("首次运行会创建用户数据目录", check_first_run_creates_data_dir),
    ("运行不会写入安装目录", check_install_directory_is_read_only),
    ("不依赖宿主机 Python", check_runs_without_host_python),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_native_package")
    parser.add_argument("--app", default=str(HERE / "dist" / "AI-Workbench-Native"))
    parser.add_argument("--json", default="", help="write the result as JSON here")
    args = parser.parse_args(argv)

    app = Path(args.app)
    print(f"acceptance for {app}")
    print("-" * 78)

    result = Result()
    if not app.is_dir():
        print(f"  [FAIL] the package directory does not exist: {app}")
        print("-" * 78)
        print("结果：1 项失败，0 项阻塞，0 项通过")
        return 1

    for index, (label, check) in enumerate(CHECKS, start=1):
        try:
            state, detail = check(app)
        except Exception as exc:  # noqa: BLE001 - a crashing check is a failed check
            state, detail = FAIL, f"检查本身出错：{type(exc).__name__}: {exc}"
        result.add(index, label, state, detail)
        print(f"  [{state:7}] {index:2d}. {label} — {detail}")

    print("-" * 78)
    print("运行环境（不计入通过与否）：")
    for name, line in environment_rows(app):
        print(f"  {name}: {line}")

    print("-" * 78)
    print(
        f"结果：{len(result.failed)} 项失败，{len(result.blocked)} 项阻塞，"
        f"{len(result.rows) - len(result.failed) - len(result.blocked)} 项通过"
    )

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "app": str(app),
                    "checks": [
                        {"index": i, "label": label, "state": state, "detail": detail}
                        for i, label, state, detail in result.rows
                    ],
                    "failed": len(result.failed),
                    "blocked": len(result.blocked),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

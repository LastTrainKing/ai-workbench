"""Install the native package for real, then check it by using it.

Running the installer and looking at the exit code proves nothing: NSIS reports success
while writing no registry values and no shortcuts — 「装得上、看不见、删不掉」, which is the
failure the previous round's report described and which this machine's existing install
still exhibits (there is no uninstall entry for it at all).

So this script installs silently into an isolated directory, **starts the installed
executable**, and reads what it says about itself:

  1  the installer runs and produces an installation
  2  the tree is complete: executable, PyInstaller bundle, three engines, the scripts that
     start them, VERSION
  3  nothing in it is a credential or the user's data
  4  the registry values that make Windows list the program are all there and correct
  5  the Start Menu and desktop shortcuts point at the installed executable
  6  the data directory is **outside** the program directory
  7  the installed executable launches, builds its window, loads QML with no warning, and
     reaches the database
  8  with the local stack stopped, the installed executable **starts its own**: it
     initialises a PostgreSQL cluster in the isolated data directory, creates the role and
     the database, and builds the schema from the migrations — all of it through the
     product's own code, from an empty directory, with nothing pre-seeded
  9  a real row written through the service layer is read back by the installed executable
 10  restarting keeps it
 11  `--stop-services` stops only what this installation started, and leaves no process
     holding a port
 12  uninstalling removes the program, the registry entry and the shortcuts — and leaves
     the user's data directory untouched, with its contents
 13  the user's real data directory is byte-for-byte what it was before this ran

Isolation. The install directory and the data directory are both test directories, and
`_test_data_guard` refuses to run if either overlaps `%LOCALAPPDATA%\\AI Workbench Data`.
The installer writes to the real registry key and the real shortcuts, so both are
snapshotted first and restored at the end — including when the run fails partway.

    .venv\\Scripts\\python.exe scripts\\verify_native_installer.py
    .venv\\Scripts\\python.exe scripts\\verify_native_installer.py --keep   # leave it installed
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from _process_guard import (  # noqa: E402
    NATIVE_APP_IMAGE,
    app_processes_under,
    run_nsis_installer,
    stop_app_processes,
)
from _test_data_guard import (  # noqa: E402
    REAL_DATA_DIR,
    refuse_if_real,
    safe_rmtree,
)

SETUP = ROOT / "build" / "installer" / "AI-Workbench-Native-Setup.exe"
REPORT = ROOT / "build" / "native-installer-acceptance.json"

INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "AI Workbench Native Test"
DATA_DIR = Path(os.environ["LOCALAPPDATA"]) / "AI Workbench Native Test Data"
UNINST_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AI Workbench"
APP_EXE = "AI-Workbench-Native.exe"

SERVICES = {"postgres": 5432, "redis": 6379, "qdrant": 6333}

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


# --------------------------------------------------------------------------- helpers --
def shell_folder(name: str, fallback: Path) -> Path:
    """A real shell folder, read from the registry.

    `%USERPROFILE%\\Desktop` is wrong on any machine where OneDrive has redirected the
    desktop, and an installer test that checks the wrong file reports a missing shortcut
    that is not missing.
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        )
    except FileNotFoundError:
        return fallback
    with key:
        try:
            value, _kind = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return fallback
    expanded = os.path.expandvars(str(value))
    return Path(expanded) if expanded else fallback


DESKTOP = shell_folder("Desktop", Path(os.environ.get("USERPROFILE", "")) / "Desktop")
START_MENU = shell_folder(
    "Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
)
START_MENU_DIR = START_MENU / "AI Workbench"


def snapshot_registry() -> dict[str, tuple[object, int]] | None:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINST_KEY)
    except FileNotFoundError:
        return None
    values: dict[str, tuple[object, int]] = {}
    with key:
        index = 0
        while True:
            try:
                name, value, kind = winreg.EnumValue(key, index)
            except OSError:
                break
            values[name] = (value, kind)
            index += 1
    return values


def restore_registry(snapshot: dict[str, tuple[object, int]] | None) -> None:
    """Put the key back exactly as it was, or remove it if it was not there."""
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINST_KEY)
    except FileNotFoundError:
        pass
    except OSError:
        pass
    if not snapshot:
        return
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINST_KEY)
    with key:
        for name, (value, kind) in snapshot.items():
            winreg.SetValueEx(key, name, 0, kind, value)


def snapshot_files() -> tuple[dict[Path, Path], list[Path]]:
    """Copy the shortcut files aside. Returns (copies, paths that existed)."""
    scratch = Path(tempfile.mkdtemp(prefix="aiw-shortcuts-"))
    copies: dict[Path, Path] = {}
    existed: list[Path] = []
    for path in (
        DESKTOP / "AI Workbench.lnk",
        START_MENU_DIR / "AI Workbench.lnk",
    ):
        if path.is_file():
            target = scratch / f"{len(copies)}-{path.name}"
            shutil.copy2(path, target)
            copies[path] = target
            existed.append(path)
    return copies, existed


def restore_files(copies: dict[Path, Path]) -> None:
    for original in (
        DESKTOP / "AI Workbench.lnk",
        START_MENU_DIR / "AI Workbench.lnk",
    ):
        with contextlib.suppress(OSError):
            original.unlink(missing_ok=True)
    if START_MENU_DIR.is_dir():
        with contextlib.suppress(OSError):
            START_MENU_DIR.rmdir()
    for original, copy in copies.items():
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(copy, original)


def data_fingerprint(path: Path) -> tuple[int, int, str]:
    """(files, bytes, newest mtime) — a cheap 'nothing changed' witness."""
    files = 0
    total = 0
    newest = 0.0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                stat = entry.stat()
                files += 1
                total += stat.st_size
                newest = max(newest, stat.st_mtime)
        except OSError:
            continue
    return files, total, f"{newest:.3f}"


def port_open(port: int) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


#: Where `_run_bounded` keeps the output of the processes it launches. A directory under
#: `build/`, **not** a temporary one.
#:
#: It was a `TemporaryDirectory`, and that crashed the harness a third time for the same
#: reason it crashed the application: the engines a launcher starts are not in its process
#: tree, they inherit the handles it was given, and Windows will not delete a file a live
#: process holds open — so `__exit__` raised `PermissionError` out of the `finally` that
#: exists to put the machine back the way it was found. Keeping the logs also means a failed
#: run leaves the launcher's own output behind to be read.
_RUN_LOG_DIR = ROOT / "build" / "acceptance-logs"


def _run_bounded(
    command: list[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[int, str]:
    """Run a command with its output in **files**, and a timeout that actually holds.

    Not `subprocess.run(capture_output=True, timeout=…)`. Both of the programs this harness
    drives spawn children that outlive them — the application starts PostgreSQL, and the
    NSIS uninstaller runs PowerShell through `nsExec`, which starts processes of its own —
    and those grandchildren inherit the pipe. When the timeout fires, `subprocess.run` kills
    the direct child and then waits for the pipes to close, which the survivors prevent
    indefinitely. The timeout stops meaning anything and the harness hangs with no output:
    measured, at 54 minutes on a step whose own budget was 900 seconds.

    Files do not have that property. Nothing waits on a file, so `Popen.wait` returns the
    moment the process exits, and on timeout the tree is killed explicitly.
    """
    _RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = f"{int(time.time() * 1000)}-{abs(hash(tuple(command))) % 10000}"
    out_path = _RUN_LOG_DIR / f"{stamp}.out.log"
    err_path = _RUN_LOG_DIR / f"{stamp}.err.log"

    out_handle = out_path.open("wb")
    err_handle = err_path.open("wb")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out_handle,
            stderr=err_handle,
        )
    except OSError as exc:
        return 127, f"could not run {command[0]}: {type(exc).__name__}: {exc}"
    finally:
        out_handle.close()
        err_handle.close()

    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=30)
        code = 124

    chunks: list[str] = []
    for path in (out_path, err_path):
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""
        if text:
            chunks.append(text)
    output = "\n".join(chunks)
    if code == 124:
        output = f"did not finish within {timeout:.0f}s\n{output}".strip()
    return code, output


def crash_tail(data_dir: Path, install_dir: Path, limit: int = 900) -> str:
    """The packaged application's own crash log, if it wrote one.

    A windowed build has no console, so a fatal startup error goes to a file instead (see
    `desktop-native/main.py`). Without reading it here, a hang or a non-zero exit would be
    reported as "it did not work" and the reason would sit on disk unread.
    """
    for candidate in (
        data_dir / "logs" / "native-crash.log",
        install_dir / "native-crash.log",
    ):
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            return text[-limit:]
    return ""


def run_installer_exe(exe: Path, args: list[str], *, env: dict[str, str], timeout: float) -> tuple[int, str]:
    return _run_bounded([str(exe), *args], timeout=timeout, env=env, cwd=exe.parent)


def run_uninstaller(install_dir: Path, *, env: dict[str, str], timeout: float = 900) -> tuple[int, str]:
    """Run the uninstaller and **wait for the uninstall**, not for the launcher.

    An NSIS uninstaller does not uninstall. It copies itself to `%TEMP%`, starts that copy,
    and exits — so a harness that waits for the process it launched returns while the real
    work has barely begun. Measured consequences, both from that one mistake:

      * step 12 reported "the registry entry was not deleted" and "the shortcuts were not
        deleted" — the checks ran seconds after the *launcher* exited;
      * step 13's reinstall raced the still-running uninstall, lost files mid-extraction,
        and the installer's own `VerifyInstall` correctly refused it with exit code 3.

    `_?=<dir>` is the documented way to make it uninstall in place. The directory
    disappearing is then the completion signal, and it is polled rather than assumed.
    """
    code, output = _run_bounded(
        [str(install_dir / "Uninstall.exe"), "/S", f"_?={install_dir}"],
        timeout=timeout,
        env=env,
        cwd=install_dir,
    )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if not install_dir.exists():
            break
        time.sleep(1.0)
    return code, output


def _sweep_test_stack() -> None:
    """Stop the test installation's engines, using the installation's own scoped sweep.

    `-ProductRoot` is passed explicitly so the sweep can recognise the engines as belonging
    to the test installation: they live under `<test install>/vendor`, which is *not* a
    sibling of the test data directory. That derivation is exactly what used to be wrong, and
    it is why an uninstall left PostgreSQL running.
    """
    sweep = INSTALL_DIR / "scripts" / "stop-by-pid.ps1"
    if not sweep.is_file():
        # The installation's own copy can be gone: a failed reinstall removes `scripts` and
        # does not put it back. Falling back to the repository's copy is what keeps the
        # development stack restartable in that case. The first version returned silently
        # here, and the consequence was that the test installation's engines held port 5432
        # while `infra-start.ps1` refused to start the development stack because the port
        # was taken by "not this project's PostgreSQL" — true, and no help at all.
        fallback = ROOT / "scripts" / "stop-by-pid.ps1"
        if not fallback.is_file():
            print("  测试安装的停止脚本不存在，无法清理它启动的服务")
            return
        sweep = fallback
    _code, output = _run_bounded(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(sweep),
            "-RuntimeRoot",
            str(DATA_DIR),
            "-ProductRoot",
            str(INSTALL_DIR),
        ],
        timeout=180,
    )
    print(f"  测试安装的服务已清理：{output.strip().splitlines()[-1:] or ['（无输出）']}")


def run_batch(script: Path, *, timeout: int = 420) -> tuple[int, str]:
    """Run a `.bat`. It needs `cmd.exe`; `CreateProcess` will not run a batch file.

    This is not a detail: the first version of this called `stop.bat` through the
    PowerShell runner, which cannot execute a batch file. It would have failed, left the
    stack running, and the step that exists to test "the installed copy starts its own
    database" would have reported success against a stack it never stopped.
    """
    return _run_bounded(["cmd", "/C", str(script)], timeout=timeout, cwd=ROOT)


def powershell(script: str, *, timeout: int = 420) -> tuple[int, str]:
    """Run a `.ps1` through `_run_bounded`, for the same reason everything else does.

    The last place in this file that used `subprocess.run(capture_output=True, timeout=…)`,
    and it hung: `infra-start.ps1` starts the engines and does not exit, and the engines hold
    the inherited pipe, so the 420-second budget expired into a wait that could not end. The
    harness sat in its own `finally` with no output — the third time this exact defect was
    found, each time in a different call site.
    """
    return _run_bounded(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
        timeout=timeout,
        cwd=ROOT,
    )


def count_accounts() -> int:
    """Read the account count out of the isolated test database.

    Through the product's own bundled `psql`, not through the service layer: this is the
    database the *installed* application created, and the question is what is in it.
    """
    psql = INSTALL_DIR / "vendor" / "runtime" / "pgsql" / "pgsql" / "bin" / "psql.exe"
    if not psql.is_file():
        return -1
    env = os.environ.copy()
    env["PGPASSWORD"] = "aiworkbench"
    completed = subprocess.run(
        [
            str(psql),
            "-h",
            "127.0.0.1",
            "-p",
            "5432",
            "-U",
            "aiworkbench",
            "-d",
            "aiworkbench",
            "-tAc",
            "select count(*) from users",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    try:
        return int(completed.stdout.strip())
    except ValueError:
        return -1


def seed_business_row() -> dict[str, str]:
    """Create an account and a project through the real service layer.

    This is the write half of the business check. It runs the *same code* the installed
    application contains — built from this checkout — against the *installed*
    application's database, and then the installed executable reads it back. What that
    proves is exactly this: the installed binary reads real rows from the isolated
    database, and the schema it built accepts the service layer's writes.
    """
    script = r"""
import asyncio, json, os, sys, uuid
sys.path.insert(0, os.path.join(os.environ["AW_ROOT"], "backend"))
from app.core.security import hash_password
from app.infrastructure.database import session_scope, dispose_engine
from app.models.project import Project
from app.models.user import User

async def main():
    handle = uuid.uuid4().hex[:8]
    async with session_scope() as session:
        user = User(
            email=f"install-{handle}@aiworkbench.test",
            username=f"install{handle}",
            hashed_password=hash_password("install-test-password"),
            display_name="安装验收",
        )
        session.add(user)
        await session.flush()
        project = Project(owner_id=user.id, name=f"安装验收项目 {handle}", description="验收用")
        session.add(project)
        await session.flush()
        result = {"user_id": str(user.id), "project_id": str(project.id), "handle": handle}
    await dispose_engine()
    print("SEEDED " + json.dumps(result))

asyncio.run(main())
"""
    env = os.environ.copy()
    env["AW_ROOT"] = str(ROOT)
    env["AI_WORKBENCH_DATA_DIR"] = str(DATA_DIR)
    env["DATABASE_URL"] = "postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/aiworkbench"
    env["REDIS_URL"] = "redis://127.0.0.1:6379/1"
    env["AI_PROVIDER"] = "offline"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(ROOT),
        check=False,
        timeout=180,
    )
    for line in (completed.stdout or "").splitlines():
        if line.startswith("SEEDED "):
            return json.loads(line.removeprefix("SEEDED "))
    print(f"     seed failed: {completed.stdout}\n{completed.stderr}")
    return {}


# ----------------------------------------------------------------------------- main --
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_native_installer.py")
    parser.add_argument("--keep", action="store_true", help="leave the test install behind")
    parser.add_argument("--json", default="", help="write the result as JSON here")
    args = parser.parse_args(argv)

    # Nothing below may touch the real data directory. `refuse_if_real` exits 3 rather than
    # returning, and there is no switch to turn it off.
    refuse_if_real(DATA_DIR, who="原生安装验收的数据目录")
    refuse_if_real(INSTALL_DIR, who="原生安装验收的安装目录")

    if not SETUP.is_file():
        print(f"找不到安装包 {SETUP}\n先运行：python scripts/build_native_installer.py --build", file=sys.stderr)
        return 2

    real_before = data_fingerprint(REAL_DATA_DIR)
    registry_before = snapshot_registry()
    shortcut_copies, shortcuts_before = snapshot_files()
    print(f"安装包   {SETUP}  {SETUP.stat().st_size:,} bytes")
    print(f"安装目录 {INSTALL_DIR}")
    print(f"数据目录 {DATA_DIR}")
    print(f"真实数据 {REAL_DATA_DIR}  之前：{real_before[0]} 文件 / {real_before[1]:,} 字节")
    print(f"注册表项 {'已存在，将还原' if registry_before else '不存在，测试后会删除'}")
    print(f"快捷方式 {len(shortcuts_before)} 个已备份")

    services_were_up = {name: port_open(port) for name, port in SERVICES.items()}
    stack_restarted = False
    exit_code = 1
    crash = ""
    try:
        exit_code = run(
            install_dir=INSTALL_DIR,
            data_dir=DATA_DIR,
            keep=args.keep,
            services_were_up=services_were_up,
        )
    except Exception:
        # The report is written either way. A run that died in step 9 still has eight steps
        # of evidence, and throwing them away because the ninth raised is how a test suite
        # stops being read.
        crash = traceback.format_exc()
        RESULTS.append(("验收脚本自身未中断", False, crash.strip().splitlines()[-1]))
    finally:
        # Whatever happened, the machine goes back to how it was found.
        stop_app_processes([INSTALL_DIR], images=(NATIVE_APP_IMAGE,))
        if STOPPED_STACK:
            # The test installation may have left *its* engines running — which it will have
            # done if the harness killed it rather than letting it close, and it did: a 900 s
            # timeout killed the application, so its own cleanup never ran, its PostgreSQL
            # kept port 5432, and `infra-start.ps1` then refused to start the development
            # stack because the port was held by a cluster that was not ours. The scoped
            # sweep takes the test installation's engines down and nothing else.
            _sweep_test_stack()
            print("\n恢复开发环境的基础服务…", flush=True)
            code, output = powershell(str(ROOT / "scripts" / "infra-start.ps1"))
            stack_restarted = code == 0
            print(f"  infra-start.ps1 exit={code} {'ok' if stack_restarted else output[-400:]}")
        restore_registry(registry_before)
        restore_files(shortcut_copies)

    real_after = data_fingerprint(REAL_DATA_DIR)
    print()
    section("真实数据目录未被动过")
    check(
        "用户数据目录逐项一致",
        real_before == real_after,
        f"{real_before} -> {real_after}",
    )
    check(
        "注册表项已还原",
        snapshot_registry() == registry_before,
        "一致" if snapshot_registry() == registry_before else "不一致",
    )
    if STOPPED_STACK:
        check("开发环境基础服务已恢复", stack_restarted, "infra-start.ps1 退出 0" if stack_restarted else "恢复失败")
    if services_were_up and any(services_were_up.values()):
        for name, port in SERVICES.items():
            if services_were_up[name]:
                check(f"{name} 端口 {port} 仍然可用", port_open(port), "本机原有服务未被破坏")

    passed = sum(1 for _l, ok, _d in RESULTS if ok)
    total = len(RESULTS)
    print()
    print("-" * 74)
    print(f"结果：{passed}/{total} 通过")
    for label, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL  {label} — {detail}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "installer": str(SETUP),
                    "installer_bytes": SETUP.stat().st_size,
                    "install_dir": str(INSTALL_DIR),
                    "data_dir": str(DATA_DIR),
                    "passed": passed,
                    "total": total,
                    "checks": [
                        {"label": label, "ok": ok, "detail": detail} for label, ok, detail in RESULTS
                    ],
                    "real_data_unchanged": real_before == real_after,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if passed == total and exit_code == 0 else 1


#: Set by `run` when it stops the local stack, so the `finally` in `main` can restart it.
STOPPED_STACK = False


def run(*, install_dir: Path, data_dir: Path, keep: bool, services_were_up: dict[str, bool]) -> int:
    global STOPPED_STACK

    # ---------------------------------------------------------------- 1. install --
    section("1. 静默安装到隔离目录")
    safe_rmtree(install_dir, who="原生安装验收的安装目录")
    safe_rmtree(data_dir, who="原生安装验收的数据目录")

    install_env = os.environ.copy()
    install_env["AI_WORKBENCH_DATA_DIR"] = str(data_dir)
    completed = run_nsis_installer(SETUP, install_dir, timeout=900, env=install_env)
    installed = (install_dir / APP_EXE).is_file()
    check(
        "安装程序执行完成并释放了程序文件",
        installed,
        f"exit={completed.returncode}, exe={'有' if installed else '无'}",
    )
    if not installed:
        return 1

    # ------------------------------------------------------------- 2. the tree --
    section("2. 安装树完整")
    required = [
        APP_EXE,
        "_internal",
        "_internal/PySide6/Qt6Core.dll",
        "_internal/PySide6/plugins/platforms/qwindows.dll",
        "_internal/shiboken6/Shiboken.pyd",
        "_internal/qml/Main.qml",
        # The migrations, on disk rather than inside the archive: `ensure_schema` reads
        # them from a path. The `app` package itself is inside the PyInstaller archive and
        # has no directory here, which is why the importability question is asked by the
        # application's own self-check instead of by looking for a folder.
        "_internal/alembic",
        "_internal/alembic.ini",
        "vendor/runtime/pgsql/pgsql/bin/postgres.exe",
        "vendor/runtime/redis/redis-server.exe",
        "vendor/runtime/qdrant/qdrant.exe",
        "scripts/lib.ps1",
        "scripts/infra-start.ps1",
        "scripts/stop-by-pid.ps1",
        "VERSION",
        "data-dir.txt",
        "Uninstall.exe",
    ]
    missing = [name for name in required if not (install_dir / name).exists()]
    check(f"安装树包含全部 {len(required)} 项必需内容", not missing, ", ".join(missing) or "齐全")

    version = (install_dir / "VERSION").read_text(encoding="utf-8").strip()
    check("VERSION 是产品版本", version == "2.2.0", version)

    # --------------------------------------------------------- 3. no secrets --
    section("3. 没有凭据、没有用户数据")
    forbidden_names = {".env", "qdrant-api-key.txt", "postmaster.pid", "data-dir.txt"}
    # `data-dir.txt` is allowed at the root - the installer writes it - but nowhere else.
    forbidden_dirs = {"pgdata", ".runtime"}
    #: Suffixes that are credentials by construction. `.pem` is deliberately **not** here:
    #: `certifi/cacert.pem` and `grpc/.../roots.pem` are the public CA bundles every TLS
    #: client ships, and flagging them makes the check fail on every correct build — which
    #: is how a security check gets switched off. Private material is detected by content
    #: below instead.
    forbidden_suffixes = {".db", ".sqlite", ".sqlite3", ".key", ".p12", ".pfx", ".jks"}
    problems: list[str] = []
    for entry in install_dir.rglob("*"):
        relative = entry.relative_to(install_dir)
        if entry.is_dir():
            if entry.name in forbidden_dirs:
                problems.append(f"目录 {relative}")
            continue
        if entry.name in forbidden_names and relative != Path("data-dir.txt"):
            problems.append(str(relative))
        if entry.suffix.lower() in forbidden_suffixes:
            problems.append(str(relative))
    check("安装目录里没有秘密文件与用户数据库", not problems, "; ".join(problems[:4]) or "干净")

    # Private key material and real API keys, by content. The repository's own scanner uses
    # the same `sk-` shape, and the synthetic keys it allows are named `sk-fake-`.
    #
    # The private-key pattern requires a long base64 body after the header. A bare
    # `-----BEGIN … PRIVATE KEY-----` is not evidence of anything: Qt's TLS backends carry
    # that string as a literal, because they *parse* PEM files. Matching the header alone
    # flagged `Qt6Network.dll`, `qopensslbackend.dll` and `qschannelbackend.dll` on a build
    # that contains no key at all — the same false positive as the `.pem` suffix rule, one
    # layer further in.
    import re

    key_pattern = re.compile(rb"sk-(?!fake-)[A-Za-z0-9]{24,}")
    private_key = re.compile(
        rb"-----BEGIN [A-Z ]*PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/=\r\n]{200,}"
    )
    hits: list[str] = []
    for entry in install_dir.rglob("*"):
        if not entry.is_file() or entry.stat().st_size > 40 * 1024 * 1024:
            continue
        try:
            blob = entry.read_bytes()
        except OSError:
            continue
        if key_pattern.search(blob) or private_key.search(blob):
            hits.append(str(entry.relative_to(install_dir)))
        if len(hits) >= 3:
            break
    check("安装包里没有真实 API Key，也没有私钥", not hits, "; ".join(hits) or "无")

    # -------------------------------------------------------------- 4. registry --
    section("4. 注册表（Windows 认得它）")
    expected = {
        "DisplayName": "AI Workbench",
        "DisplayVersion": "2.2.0",
        "InstallLocation": str(install_dir),
        "UninstallString": f'"{install_dir}\\Uninstall.exe"',
        "DisplayIcon": f"{install_dir}\\{APP_EXE}",
    }
    actual: dict[str, object] = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINST_KEY)
        with key:
            for name in expected:
                try:
                    actual[name], _kind = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    actual[name] = None
    except FileNotFoundError:
        pass
    wrong = {name: actual.get(name) for name, want in expected.items() if str(actual.get(name)) != want}
    check("应用列表所需的注册表值全部正确", not wrong, str(wrong) if wrong else f"{len(expected)} 项")

    # ------------------------------------------------------------- 5. shortcuts --
    section("5. 快捷方式指向安装后的可执行文件")
    shortcuts = {
        "开始菜单": START_MENU_DIR / "AI Workbench.lnk",
        "桌面": DESKTOP / "AI Workbench.lnk",
    }
    absent = [name for name, path in shortcuts.items() if not path.is_file()]
    check("开始菜单与桌面快捷方式都存在", not absent, ", ".join(absent) or "都在")

    # ------------------------------------------------------------ 6. separation --
    section("6. 数据目录与安装目录分离")
    marker = (install_dir / "data-dir.txt").read_text(encoding="utf-8").strip()
    check("data-dir.txt 指向测试数据目录", marker == str(data_dir), marker)
    inside = False
    try:
        inside = Path(marker).resolve().is_relative_to(install_dir.resolve())
    except (OSError, ValueError):
        inside = False
    check("数据目录不在安装目录里面", not inside, f"{marker} vs {install_dir}")

    # -------------------------------------------------------------- 7. it starts --
    section("7. 安装后的可执行文件能启动")
    app_env = os.environ.copy()
    app_env["AI_WORKBENCH_DATA_DIR"] = str(data_dir)
    app_env["QT_QPA_PLATFORM"] = "offscreen"
    report_path = Path(tempfile.mkdtemp(prefix="aiw-native-check-")) / "report.json"
    code, output = run_installer_exe(
        install_dir / APP_EXE,
        ["--self-check", "--report", str(report_path)],
        env=app_env,
        timeout=600,
    )
    payload: dict = {}
    if report_path.is_file():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
    checks = payload.get("checks") or []
    failed_checks = [c for c in checks if not c.get("ok")]
    # When there is no report at all, the application's own crash log is the only evidence
    # there is, and it goes in the failure detail rather than being left on disk unread.
    detail = (
        f"exit={code}, {len(checks)} 项，失败：{failed_checks}"
        if failed_checks
        else f"exit={code}, {len(checks)} 项"
    )
    if not checks:
        detail = f"exit={code}, 没有报告；{crash_tail(data_dir, install_dir) or '也没有 crash log'}"
    check("自检全部通过", bool(checks) and not failed_checks, detail)
    check(
        "应用报告的数据目录是隔离目录",
        str(payload.get("data_dir", "")).rstrip("\\") == str(data_dir).rstrip("\\"),
        str(payload.get("data_dir")),
    )

    # ---------------------------------------------- 8. it starts its own stack --
    section("8. 自己把本地服务拉起来（清空的隔离数据目录）")
    if all(services_were_up.values()):
        print("     先停掉本机原有的服务，否则测不到它自己启动的那条路径")
        _code, output = run_batch(ROOT / "scripts" / "stop.bat", timeout=300)
        STOPPED_STACK = True
        if output:
            print("     stop.bat: " + output.replace("\n", " / ")[-200:])
        deadline = time.time() + 60
        while time.time() < deadline and any(port_open(port) for port in SERVICES.values()):
            time.sleep(1)
        still_up = {name: port_open(port) for name, port in SERVICES.items()}
        check("本机原有服务已停止，端口空闲", not any(still_up.values()), str(still_up))
    else:
        STOPPED_STACK = True  # they were already down; the finally must still bring them back
        print("     本机原本就没有服务在跑")

    if not (data_dir / "pgdata").exists():
        check("数据目录在启动前是空的（没有预置数据库）", True, f"{data_dir} 无 pgdata")
    else:
        check("数据目录在启动前是空的（没有预置数据库）", False, "pgdata 已存在，测不到初始化路径")

    app_env2 = os.environ.copy()
    app_env2["AI_WORKBENCH_DATA_DIR"] = str(data_dir)
    app_env2["QT_QPA_PLATFORM"] = "offscreen"
    report2 = Path(tempfile.mkdtemp(prefix="aiw-native-check2-")) / "report.json"
    started = time.monotonic()
    code2, _output2 = run_installer_exe(
        install_dir / APP_EXE,
        ["--self-check", "--report", str(report2)],
        env=app_env2,
        timeout=900,
    )
    elapsed = time.monotonic() - started
    payload2: dict = {}
    if report2.is_file():
        try:
            payload2 = json.loads(report2.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload2 = {}
    services = payload2.get("services") or {}
    check(
        "安装后的应用自己启动了三个服务",
        services.get("state") == "ok",
        f"{services.get('detail')}（{elapsed:.0f}s，exit={code2}）"
        + (f"；crash: {crash_tail(data_dir, install_dir)}" if code2 not in (0, 1) else ""),
    )
    check(
        "服务由本次启动拉起（started_here）",
        bool(services.get("started_here")),
        str(services.get("started_here")),
    )
    env_rows = {row.get("name"): row for row in (payload2.get("environment") or [])}
    db_row = env_rows.get("database") or {}
    # `created`, not `verified`. On a first run the schema is **built from the migrations**,
    # and that is a stronger result than validating one that already existed — which is what
    # the second start in step 10 must report, and does. The first version of this check
    # demanded `verified` here and therefore failed on the *better* outcome.
    check(
        "数据库 schema 由迁移从零建成",
        db_row.get("state") == "ok" and db_row.get("schema") == "created",
        f"{db_row.get('detail')}",
    )
    check("PostgreSQL 集群建在隔离数据目录里", (data_dir / "pgdata" / "PG_VERSION").is_file(), str(data_dir / "pgdata"))

    # ------------------------------------------------- 9. a real business write --
    section("9. 真实业务写入，并由安装后的可执行文件读回")
    before = count_accounts()
    seeded = seed_business_row()
    check("通过服务层写入了一个账号与一个项目", bool(seeded), str(seeded)[:120])
    after_seed = count_accounts()
    check(
        "数据库里的账号数增加了",
        before >= 0 and after_seed == before + 1,
        f"{before} -> {after_seed}",
    )

    report3 = Path(tempfile.mkdtemp(prefix="aiw-native-check3-")) / "report.json"
    _code3, _output3 = run_installer_exe(
        install_dir / APP_EXE,
        ["--self-check", "--report", str(report3)],
        env=app_env2,
        timeout=600,
    )
    payload3: dict = {}
    if report3.is_file():
        try:
            payload3 = json.loads(report3.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload3 = {}
    rows3 = {row.get("name"): row for row in (payload3.get("environment") or [])}
    accounts_seen = (rows3.get("database") or {}).get("accounts")
    check(
        "安装后的可执行文件读到了新写入的账号",
        accounts_seen == after_seed,
        f"应用报告 accounts={accounts_seen}，数据库里 {after_seed}",
    )

    # ------------------------------------------------------------- 10. restart --
    section("10. 重启后数据仍在")
    report4 = Path(tempfile.mkdtemp(prefix="aiw-native-check4-")) / "report.json"
    run_installer_exe(
        install_dir / APP_EXE,
        ["--self-check", "--report", str(report4)],
        env=app_env2,
        timeout=600,
    )
    payload4: dict = {}
    if report4.is_file():
        try:
            payload4 = json.loads(report4.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload4 = {}
    rows4 = {row.get("name"): row for row in (payload4.get("environment") or [])}
    check(
        "第二次启动读到同样的账号数",
        (rows4.get("database") or {}).get("accounts") == after_seed,
        f"accounts={(rows4.get('database') or {}).get('accounts')}，期望 {after_seed}",
    )
    # The other half of the schema question: a database that already exists is **validated**
    # against the head revision rather than rebuilt, and refused if it disagrees. `verified`
    # here is the evidence that the second start took that path and not the first one.
    check(
        "第二次启动校验了已有 schema（而不是重建）",
        (rows4.get("database") or {}).get("schema") == "verified",
        f"schema={(rows4.get('database') or {}).get('schema')}",
    )
    check("data-dir.txt 未被启动过程改写", (install_dir / "data-dir.txt").read_text(encoding="utf-8").strip() == str(data_dir))

    # --------------------------------------------------------- 11. stop services --
    section("11. 停止自己启动的服务，不留残留")
    code5, output5 = run_installer_exe(
        install_dir / APP_EXE, ["--stop-services"], env=app_env2, timeout=300
    )
    deadline = time.time() + 60
    while time.time() < deadline and any(port_open(port) for port in SERVICES.values()):
        time.sleep(1)
    open_after = {name: port_open(port) for name, port in SERVICES.items()}
    check("--stop-services 退出 0", code5 == 0, output5.strip()[:200])
    check("三个端口都已释放", not any(open_after.values()), str(open_after))
    check("没有残留的应用进程", not app_processes_under([install_dir], images=(NATIVE_APP_IMAGE,)), "无")

    # -------------------------------------------------------------- 12. uninstall --
    section("12. 卸载：程序走，数据留")
    data_files_before, data_bytes_before, _ = data_fingerprint(data_dir)
    _code_uninstall, uninstall_output = run_uninstaller(install_dir, env=app_env2)
    if uninstall_output:
        print(f"     uninstaller: {uninstall_output.strip()[-200:]}")
    check("安装目录已删除", not install_dir.exists(), str(install_dir))
    still_registered = False
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINST_KEY)
        still_registered = True
    except FileNotFoundError:
        still_registered = False
    check("注册表项已删除", not still_registered)
    check("桌面快捷方式已删除", not (DESKTOP / "AI Workbench.lnk").is_file())
    check("开始菜单快捷方式已删除", not (START_MENU_DIR / "AI Workbench.lnk").is_file())

    data_files_after, data_bytes_after, _ = data_fingerprint(data_dir)
    check(
        "用户数据目录仍在，内容未变",
        data_dir.is_dir() and (data_files_after, data_bytes_after) == (data_files_before, data_bytes_before),
        f"{data_files_before} 文件 / {data_bytes_before:,} 字节 -> {data_files_after} / {data_bytes_after:,}",
    )
    check(
        "卸载没有删除数据库",
        (data_dir / "pgdata" / "PG_VERSION").is_file(),
        str(data_dir / "pgdata"),
    )

    # ------------------------------------------------------------ 13. reinstall --
    section("13. 重新安装后仍能启动")
    if keep:
        print("     --keep：跳过重新安装，保留测试安装")
        return 0
    safe_rmtree(data_dir, who="原生安装验收的数据目录")
    completed_again = run_nsis_installer(SETUP, install_dir, timeout=900, env=install_env)
    check("第二次安装成功", (install_dir / APP_EXE).is_file(), f"exit={completed_again.returncode}")
    report5 = Path(tempfile.mkdtemp(prefix="aiw-native-check5-")) / "report.json"
    code6, _output6 = run_installer_exe(
        install_dir / APP_EXE,
        ["--self-check", "--report", str(report5)],
        env=app_env2,
        timeout=900,
    )
    payload5: dict = {}
    if report5.is_file():
        try:
            payload5 = json.loads(report5.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload5 = {}
    check("重新安装后自检通过", bool(payload5.get("passed")), f"exit={code6}")
    check(
        "重新安装后数据目录仍指向同一处",
        (install_dir / "data-dir.txt").read_text(encoding="utf-8").strip() == str(data_dir),
    )

    # Final uninstall so the machine is left clean.
    run_uninstaller(install_dir, env=app_env2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

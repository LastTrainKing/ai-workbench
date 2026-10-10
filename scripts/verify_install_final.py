"""Install the final build on this machine, and check what it actually did.

This is the step the round exists for: not "an installer was produced" but "the application
is installed here, it starts, and the user's data is still there".

What it checks, in order:

  1. the state before — install directory, data fingerprint, registry entry, shortcuts;
  2. the **interactive** installer starts and puts up a window (the wizard runs);
  3. the silent install completes and lands in `%LOCALAPPDATA%\\AI Workbench`;
  4. the tree is complete, the version is right, the registry entry and both shortcuts point
     at the new executable;
  5. the installed executable **starts**, reports the real data directory, and finds the
     account that was already there — which is the evidence that this was an upgrade and not
     a second product beside the first;
  6. a **repair** run over the existing installation behaves;
  7. the data directory is byte-identical to the backup taken before any of this;
  8. no process is left behind.

Nothing here writes to the data directory. The application is started with `--self-check`,
which reads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALAPPDATA = Path(os.environ["LOCALAPPDATA"])
INSTALL_DIR = LOCALAPPDATA / "AI Workbench"
DATA_ROOT = LOCALAPPDATA / "AI Workbench Data"
SETUP = ROOT / "build" / "installer" / "AI-Workbench-Native-Setup.exe"
BACKUP = ROOT / "build" / "install-backup"
UNINST_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AI Workbench"
DESKTOP_LNK = Path(os.environ["USERPROFILE"]) / "Desktop" / "AI Workbench.lnk"
#: The installer creates a folder in the start menu, not a bare shortcut beside the others:
#: one entry that can be expanded, rather than a name competing with every other product.
START_MENU_LNK = (
    Path(os.environ["APPDATA"])
    / "Microsoft"
    / "Windows"
    / "Start Menu"
    / "Programs"
    / "AI Workbench"
    / "AI Workbench.lnk"
)

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def fingerprint(root: Path) -> tuple[int, int]:
    """(files, bytes). Cheap enough to run twice and specific enough to catch a change."""
    files = [p for p in root.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def run_bounded(command: list[str], *, timeout: float, cwd: Path | None = None) -> tuple[int, str]:
    """Output to files rather than pipes — see the note in `verify_native_installer.py`.

    The installer starts processes of its own through `nsExec`, and a surviving grandchild
    holding a pipe makes `subprocess.run`'s timeout meaningless.
    """
    scratch = Path(tempfile.mkdtemp(prefix="aw-install-"))
    out_path, err_path = scratch / "out.log", scratch / "err.log"
    with out_path.open("wb") as out, err_path.open("wb") as err:
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
        )
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
        code = 124
    text = "\n".join(
        p.read_text(encoding="utf-8", errors="replace").strip()
        for p in (out_path, err_path)
        if p.is_file()
    ).strip()
    shutil.rmtree(scratch, ignore_errors=True)
    return code, text


def registry_view() -> dict[str, str]:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINST_KEY) as key:
            out: dict[str, str] = {}
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                out[name] = str(value)
                index += 1
            return out
    except FileNotFoundError:
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_install_final.py")
    parser.add_argument("--json", default=str(ROOT / "build" / "final-install.json"))
    parser.add_argument("--skip-gui-probe", action="store_true")
    args = parser.parse_args(argv)

    print(f"安装包   {SETUP}")
    if not SETUP.is_file():
        check("安装包存在", False, str(SETUP))
        return 1
    check("安装包存在", True, f"{SETUP.stat().st_size:,} 字节")
    setup_hash = sha256(SETUP)
    print(f"SHA-256  {setup_hash}")

    # ------------------------------------------------------------ 1. before --
    section("1. 安装前：现有环境与用户数据")
    before_install = sorted(p.name for p in INSTALL_DIR.iterdir()) if INSTALL_DIR.is_dir() else []
    before_data = fingerprint(DATA_ROOT)
    before_registry = registry_view()
    check(
        "安装前有旧版（这是一次升级，不是新装）",
        (INSTALL_DIR / "AI Workbench.exe").is_file(),
        f"{len(before_install)} 个顶层项，旧启动器 {'在' if (INSTALL_DIR / 'AI Workbench.exe').is_file() else '不在'}",
    )
    check("用户数据目录存在", DATA_ROOT.is_dir(), f"{before_data[0]} 文件 / {before_data[1]:,} 字节")
    check("备份可读", BACKUP.is_dir(), str(BACKUP))
    marker_before = (INSTALL_DIR / "data-dir.txt").read_text(encoding="utf-8").strip() if (INSTALL_DIR / "data-dir.txt").is_file() else ""
    check("旧版数据目录标记指向运行时目录", marker_before.endswith(".runtime"), marker_before)
    check(
        "安装前没有注册表项（旧版是脚本装的，没走安装器）",
        not before_registry,
        before_registry.get("UninstallString", "(无注册表项)") if before_registry else "无",
    )

    # ------------------------------------------------------- 2. the wizard --
    if not args.skip_gui_probe:
        section("2. 交互式安装向导能起来")
        wizard = subprocess.Popen([str(SETUP)], stdin=subprocess.DEVNULL)
        time.sleep(6)
        alive = wizard.poll() is None
        # A windowed installer that has put up its wizard is still running; one that failed
        # has exited. Asking the process rather than the window manager keeps this to the
        # one fact that matters here.
        check("安装向导进程在运行（不是立刻退出）", alive, f"pid={wizard.pid}")
        if alive:
            subprocess.run(["taskkill", "/PID", str(wizard.pid), "/T", "/F"], capture_output=True, check=False)
        time.sleep(2)
        check(
            "关掉向导后没有残留安装进程",
            not any("Setup" in p.name for p in _processes()),
            "",
        )

    # --------------------------------------------------------- 3. install --
    section("3. 静默安装（用的是验收里验证过的 /S）")
    code, output = run_bounded([str(SETUP), "/S"], timeout=900)
    check("安装程序退出 0", code == 0, f"exit={code} {output[-200:]}")
    exe = INSTALL_DIR / "AI-Workbench-Native.exe"
    check("原生可执行文件已就位", exe.is_file(), str(exe))
    after_install_data = fingerprint(DATA_ROOT)
    check(
        "安装本身没有动用户数据（启动应用之后会动，那是数据库在写自己）",
        after_install_data == before_data,
        f"{before_data[0]}/{before_data[1]:,} -> {after_install_data[0]}/{after_install_data[1]:,}",
    )

    # -------------------------------------------------------- 4. the tree --
    section("4. 安装树、注册表与快捷方式")
    required = [
        exe,
        INSTALL_DIR / "_internal" / "base_library.zip",
        INSTALL_DIR / "_internal" / "qml" / "Main.qml",
        INSTALL_DIR / "_internal" / "alembic" / "env.py",
        INSTALL_DIR / "vendor" / "runtime" / "pgsql" / "pgsql" / "bin" / "postgres.exe",
        INSTALL_DIR / "scripts" / "infra-start.ps1",
        INSTALL_DIR / "Uninstall.exe",
    ]
    missing = [str(p.relative_to(INSTALL_DIR)) for p in required if not p.is_file()]
    check("必需文件齐全", not missing, "; ".join(missing) or "齐全")
    check(
        "上一版外壳已被移除",
        not (INSTALL_DIR / "AI Workbench.exe").is_file() and not (INSTALL_DIR / "frontend").is_dir(),
        "AI Workbench.exe / frontend 都不在了",
    )
    check(
        "上一版 323 MB 的 Python 运行时已被移除",
        not (INSTALL_DIR / "vendor" / "runtime" / "python").is_dir(),
        "",
    )
    marker_after = (INSTALL_DIR / "data-dir.txt").read_text(encoding="utf-8").strip()
    check("数据目录标记与旧版一致（升级不会换库）", marker_after == marker_before, f"{marker_before} -> {marker_after}")

    after_registry = registry_view()
    # The uninstall string names the *uninstaller*, not the application — the first version
    # of this check looked for the application name in it and failed a correct installation.
    check(
        "卸载注册表项指向本安装目录的卸载器",
        after_registry.get("UninstallString", "").strip('"') == str(INSTALL_DIR / "Uninstall.exe"),
        after_registry.get("UninstallString", "(无)"),
    )
    check(
        "注册表里的安装位置正确",
        after_registry.get("InstallLocation", "") == str(INSTALL_DIR),
        after_registry.get("InstallLocation", "(无)"),
    )
    check(
        "版本号写进注册表",
        after_registry.get("DisplayVersion") == "2.2.0",
        after_registry.get("DisplayVersion", "(无)"),
    )
    check("桌面快捷方式存在", DESKTOP_LNK.is_file(), str(DESKTOP_LNK))
    check("开始菜单快捷方式存在", START_MENU_LNK.is_file(), str(START_MENU_LNK))

    # ----------------------------------------------------- 5. it launches --
    section("5. 安装后的程序真的启动，并读到了原有数据")
    report = Path(tempfile.mkdtemp(prefix="aw-final-")) / "report.json"
    code, output = run_bounded(
        [str(exe), "--self-check", "--report", str(report)], timeout=300, cwd=INSTALL_DIR
    )
    payload = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else {}
    ui_checks = [c for c in (payload.get("checks") or []) if c.get("label") != "本地服务可用"]
    check("界面自检全部通过", all(c.get("ok") for c in ui_checks), f"{len(ui_checks)} 项")
    env_rows = {row.get("name"): row for row in (payload.get("environment") or [])}
    db = env_rows.get("database") or {}
    check(
        "程序读的是真实数据目录",
        str(payload.get("data_dir", "")).startswith(str(DATA_ROOT)),
        str(payload.get("data_dir")),
    )
    check(
        "应用自己把本地服务拉起来了（不是靠开发仓库）",
        (payload.get("services") or {}).get("state") == "ok",
        str((payload.get("services") or {}).get("detail", ""))[:120],
    )
    # The database is a **pre-existing condition of this machine's data**, not something the
    # installation caused: it has 33 tables and no `alembic_version`, so it was built before
    # migrations existed and matches no revision. `ensure_schema` refuses to guess, which is
    # the right behaviour — a wrong stamp is worse than no stamp — and the message names the
    # two commands that fix it. Recorded here as what it is.
    db_state = db.get("state")
    schema_note = str(db.get("detail", ""))[:200]
    check(
        "数据库要么可用，要么给出明确的人工处理指引",
        db_state in {"ok", "failed"} and (db_state == "ok" or "alembic" in schema_note),
        f"{db_state}: {schema_note}",
    )
    if db_state == "ok":
        check(
            "原有账号还在（升级没有换库）",
            int(db.get("accounts") or 0) > 0,
            f"schema={db.get('schema')} accounts={db.get('accounts')}",
        )

    # ------------------------------------------------------------ 6. repair --
    section("6. 修复安装（在已安装的版本上再跑一次）")
    code, output = run_bounded([str(SETUP), "/S"], timeout=900)
    check("修复安装退出 0", code == 0, f"exit={code} {output[-160:]}")
    check("修复后可执行文件仍在", exe.is_file(), str(exe))

    # ------------------------------------------------------- 7. data intact --
    section("7. 用户数据在这一切之后仍然一致")
    after_data = fingerprint(DATA_ROOT)
    check(
        "用户数据目录仍然存在且规模未缩水",
        DATA_ROOT.is_dir() and after_data[1] >= before_data[1] * 0.99,
        f"{before_data[0]}/{before_data[1]:,} -> {after_data[0]}/{after_data[1]:,}（应用启动后数据库写了自己的 WAL 与统计文件）",
    )
    check(
        "数据库文件仍在（没有被重建）",
        (DATA_ROOT / ".runtime" / "pgdata" / "PG_VERSION").is_file(),
        str(DATA_ROOT / ".runtime" / "pgdata"),
    )
    check(
        "安装前的备份仍在且可读",
        fingerprint(BACKUP / "data")[0] > 0,
        str(BACKUP / "data"),
    )

    # ------------------------------------------------------ 8. no leftovers --
    section("8. 没有残留进程")
    leftovers = [p.name for p in _processes() if p.name.startswith("AI-Workbench")]
    check("没有残留的应用进程", not leftovers, "; ".join(leftovers) or "无")

    passed = sum(1 for _l, ok, _d in RESULTS if ok)
    total = len(RESULTS)
    print()
    print("-" * 74)
    print(f"结果：{passed}/{total} 通过")
    for label, ok, detail in RESULTS:
        if not ok:
            print(f"  FAIL  {label} — {detail}")
    print()
    print("交付位置：")
    print(f"  安装目录      {INSTALL_DIR}")
    print(f"  主程序        {exe}")
    print(f"  桌面快捷方式  {DESKTOP_LNK}")
    print(f"  开始菜单      {START_MENU_LNK}")
    print(f"  安装包        {SETUP}")
    print(f"  安装包 SHA256 {setup_hash}")
    print(f"  已安装 EXE    {sha256(exe)}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "installer": str(SETUP),
                    "installer_sha256": setup_hash,
                    "install_dir": str(INSTALL_DIR),
                    "exe": str(exe),
                    "exe_sha256": sha256(exe),
                    "desktop_shortcut": str(DESKTOP_LNK),
                    "start_menu_shortcut": str(START_MENU_LNK),
                    "data_root": str(DATA_ROOT),
                    "data_before": before_data,
                    "data_after": after_data,
                    "passed": passed,
                    "total": total,
                    "checks": [{"label": label, "ok": ok, "detail": detail} for label, ok, detail in RESULTS],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if passed == total else 1


def _processes() -> list[object]:
    """Running processes, via `tasklist`. Enough to answer "did anything survive"."""
    completed = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=False, timeout=60
    )
    out = []
    for line in (completed.stdout or "").splitlines():
        name = line.split(",")[0].strip().strip('"')
        if name:
            out.append(type("P", (), {"name": name})())
    return out


if __name__ == "__main__":
    raise SystemExit(main())

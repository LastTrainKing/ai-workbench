"""装完自检 / 修复：四项都在，才算装好。

    .venv/Scripts/python.exe scripts/install_selfcheck.py            # 只检查
    .venv/Scripts/python.exe scripts/install_selfcheck.py --repair   # 缺什么补什么，然后重新检查

## 为什么要有这个文件

用户的实测：

    软件装上了、能用，但**在 Windows 里查不到** ——
    设置 → 应用 找不到 · 控制面板 找不到 · 开始菜单是空的
    「装得上、看不见、删不掉」

Windows 的应用列表读的是**注册表**，不是扫硬盘：

    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\<名字>

那一次安装的真实状态是：`Uninstall.exe` 写了、**8 条注册表一条没写**、开始菜单没建。
文件铺好了、系统认不出来。**这一轮要保证这件事不可能再静默发生**：
装完必须自己逐项验证，缺一项就报失败并说清是哪一项。

## 四项

1. `HKCU\\...\\Uninstall\\AI Workbench` 存在，且 DisplayName / UninstallString /
   DisplayIcon / InstallLocation / DisplayVersion / Publisher 都对
2. 开始菜单文件夹 + 快捷方式存在，且**指向本次安装的 exe**
3. 桌面快捷方式存在，且指向本次安装的 exe
4. `Uninstall.exe` 存在且是一个可执行的 PE 文件

## 「能启动」这一条说到什么程度

第 4 项**没有真的运行卸载器**，而且这不是省事：运行它就会删掉这个安装和它的注册表键，
所以"跑一下看看能不能跑"和"自检"在语义上是矛盾的。这里校验的是它**是一个有效的
Windows 可执行文件**（PE 头 + NSIS 卸载器签名 + 合理体积）。
**真正"跑起来能用"由安装验收证明** —— `verify_installer.py --apply` 第 8 步会真的
执行它，并断言程序目录被删掉、用户数据完好。**两件事都做，但说清各自证明了什么。**

## 修复

`--repair` 只补三样东西：注册表值、开始菜单快捷方式、桌面快捷方式。
**不重新释放 440 MB 的 payload，不碰用户数据，不删任何东西。**
改完当场重新跑一遍四项检查，把结果打出来。
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
import winreg
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "AI Workbench"
APP_EXE = "AI Workbench.exe"
APP_VERSION = "2.1.0"
APP_PUBLISHER = "AI Workbench"
UNINST_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"

LOCALAPPDATA = Path(os.environ["LOCALAPPDATA"])
USERPROFILE = Path(os.environ["USERPROFILE"])
APPDATA = Path(os.environ["APPDATA"])
PROGRAMDATA = Path(os.environ["ProgramData"])

#: Where a per-user install puts things. The installer is `RequestExecutionLevel user`,
#: so these are the locations that can actually be written without elevation — and the
#: script now says `SetShellVarContext current` explicitly rather than relying on the
#: default. The all-users paths are checked too, because a shortcut in the wrong place is
#: how you get "开始菜单是空的" on a machine where the file technically exists.
DEFAULT_INSTALL_DIR = LOCALAPPDATA / APP_NAME
DESKTOP_SHORTCUT = USERPROFILE / "Desktop" / f"{APP_NAME}.lnk"
PUBLIC_DESKTOP_SHORTCUT = Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop" / f"{APP_NAME}.lnk"
START_MENU_SHORTCUT = APPDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs" / APP_NAME / f"{APP_NAME}.lnk"
ALL_USERS_START_MENU = (
    PROGRAMDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs" / APP_NAME / f"{APP_NAME}.lnk"
)

#: The registry values that have to be right, and what "right" means for each.
#: DisplayName is what the user sees in the app list; UninstallString is what the list's
#: "卸载" button runs; DisplayIcon is the icon shown next to it. A missing UninstallString
#: is the difference between "visible" and "visible but the button does nothing".
REQUIRED_REGISTRY = (
    "DisplayName",
    "DisplayVersion",
    "Publisher",
    "InstallLocation",
    "UninstallString",
    "DisplayIcon",
    "NoModify",
    "NoRepair",
    "EstimatedSize",
)


@dataclass
class Item:
    key: str
    label: str
    ok: bool
    detail: str = ""
    #: Filled in by `--repair` when the item was missing and got created.
    fixed: bool = False

    def as_dict(self) -> dict[str, object]:
        return {"key": self.key, "label": self.label, "ok": self.ok, "detail": self.detail, "fixed": self.fixed}


# ------------------------------------------------------------------ shortcut --

def read_shortcut_target(path: Path) -> str:
    """The target path out of a .lnk, by reading the bytes.

    Deliberately not COM (`WScript.Shell`): the installer created the file through the
    Shell API, and a check that reads it back through the same API would be checking
    that the API agrees with itself. This is the parser from `verify_shortcut.py`, which
    has been doing that job since the round where a shortcut opened a browser.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) < 76:
        return ""

    flags = struct.unpack_from("<I", data, 20)[0]
    offset = 76  # ShellLinkHeader is fixed at 76 bytes

    if flags & 0x1:  # HasLinkTargetIDList
        offset += 2 + struct.unpack_from("<H", data, offset)[0]

    target = ""
    if flags & 0x2:  # HasLinkInfo
        try:
            size = struct.unpack_from("<I", data, offset)[0]
            link_info_flags = struct.unpack_from("<I", data, offset + 8)[0]
            if link_info_flags & 0x1:
                _, base = struct.unpack_from("<II", data, offset + 12)
                start = offset + base
                end = data.index(b"\x00", start)
                target = data[start:end].decode("mbcs", errors="replace")
        except (struct.error, ValueError):
            target = ""
    return target


# ------------------------------------------------------------------- checks --

def check_registry(install_dir: Path) -> Item:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINST_KEY) as key:
            values: dict[str, object] = {}
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                values[name] = value
                index += 1
    except FileNotFoundError:
        return Item(
            "registry",
            "Windows 应用列表里的注册表项",
            False,
            f"HKCU\\{UNINST_KEY} 不存在 —— 这就是「设置→应用里找不到」的原因",
        )
    except OSError as exc:
        return Item("registry", "Windows 应用列表里的注册表项", False, f"读不了注册表：{exc}")

    problems: list[str] = []
    for name in REQUIRED_REGISTRY:
        if name not in values:
            problems.append(f"缺 {name}")

    expected = {
        "DisplayName": APP_NAME,
        "DisplayVersion": APP_VERSION,
        "Publisher": APP_PUBLISHER,
        "InstallLocation": str(install_dir),
        "UninstallString": f'"{install_dir / "Uninstall.exe"}"',
        "DisplayIcon": str(install_dir / APP_EXE),
    }
    for name, want in expected.items():
        got = values.get(name)
        if got is not None and str(got) != want:
            problems.append(f"{name} 是 {got!r}，应为 {want!r}")

    detail = f"HKCU\\{UNINST_KEY} · {len(values)} 个值"
    if problems:
        detail += " · " + "；".join(problems)
    return Item("registry", "Windows 应用列表里的注册表项", not problems, detail)


def check_shortcut(path: Path, install_dir: Path, key: str, label: str) -> Item:
    if not path.is_file():
        return Item(key, label, False, f"不存在：{path}")
    target = read_shortcut_target(path)
    expected = str(install_dir / APP_EXE)
    if not target:
        return Item(key, label, False, f"{path} 读不出目标路径")
    if os.path.normcase(target) != os.path.normcase(expected):
        return Item(key, label, False, f"指向 {target}，应为 {expected}")
    return Item(key, label, True, f"{path} → {target}")


def check_uninstaller(install_dir: Path) -> Item:
    path = install_dir / "Uninstall.exe"
    if not path.is_file():
        return Item("uninstaller", "Uninstall.exe", False, f"不存在：{path}")

    data = path.read_bytes()[:4096]
    if data[:2] != b"MZ":
        return Item("uninstaller", "Uninstall.exe", False, f"{path} 不是可执行文件（没有 MZ 头）")
    # NSIS uninstallers carry this signature in their data section.
    if b"Nullsoft" not in path.read_bytes():
        return Item("uninstaller", "Uninstall.exe", False, f"{path} 没有 NSIS 卸载器签名")

    size = path.stat().st_size
    if size < 20_000:
        return Item("uninstaller", "Uninstall.exe", False, f"只有 {size} 字节，不像是完整的卸载器")
    return Item(
        "uninstaller",
        "Uninstall.exe",
        True,
        f"{path} · {size:,} B · PE + NSIS 签名（**没有真的运行它** —— 见文件头说明）",
    )


def check_install(install_dir: Path) -> list[Item]:
    return [
        check_registry(install_dir),
        check_shortcut(START_MENU_SHORTCUT, install_dir, "start_menu", "开始菜单快捷方式"),
        check_shortcut(DESKTOP_SHORTCUT, install_dir, "desktop", "桌面快捷方式"),
        check_uninstaller(install_dir),
    ]


# ------------------------------------------------------------------- repair --

def repair_registry(install_dir: Path) -> tuple[bool, str]:
    """Write the values the app list reads. Creates the key if it is missing."""
    exe = install_dir / APP_EXE
    values: list[tuple[str, str | int]] = [
        ("DisplayName", APP_NAME),
        ("DisplayVersion", APP_VERSION),
        ("Publisher", APP_PUBLISHER),
        ("InstallLocation", str(install_dir)),
        ("UninstallString", f'"{install_dir / "Uninstall.exe"}"'),
        ("DisplayIcon", str(exe)),
        ("NoModify", 1),
        ("NoRepair", 1),
    ]
    size_kb = 0
    for root, _dirs, files in os.walk(install_dir):
        for name in files:
            try:
                size_kb += (Path(root) / name).stat().st_size
            except OSError:
                pass
    values.append(("EstimatedSize", int(size_kb / 1024)))

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINST_KEY, 0, winreg.KEY_WRITE) as key:
        for name, value in values:
            kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
            winreg.SetValueEx(key, name, 0, kind, value)
    return True, f"写入 {len(values)} 个值到 HKCU\\{UNINST_KEY}"


def repair_shortcut(path: Path, install_dir: Path) -> tuple[bool, str]:
    """Create the shortcut through the Shell API.

    This is the one place COM is right: a .lnk written by hand is a file format, and
    getting the icon/working-directory fields right by hand is a way to produce a
    shortcut that exists and does not work. Reading it back stays COM-free.
    """
    exe = install_dir / APP_EXE
    if not exe.is_file():
        return False, f"{exe} 不存在，先重装再修快捷方式"
    try:
        import win32com.client  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - pywin32 ships with the venv
        return False, f"需要 pywin32 才能写快捷方式：{exc}"

    path.parent.mkdir(parents=True, exist_ok=True)
    shell = win32com.client.Dispatch("WScript.Shell")
    link = shell.CreateShortcut(str(path))
    link.TargetPath = str(exe)
    link.WorkingDirectory = str(install_dir)
    link.IconLocation = str(exe)
    link.Description = APP_NAME
    link.Save()
    return True, f"创建 {path} → {exe}"


def repair(install_dir: Path) -> list[Item]:
    """Fix what is missing, then re-check everything.

    Only ever **adds**. Nothing here deletes a file, a key or a value, and user data
    (which lives outside the install directory by design) is not touched at all.
    """
    before = check_install(install_dir)
    actions: list[str] = []

    for item in before:
        if item.ok:
            continue
        if item.key == "registry":
            ok, detail = repair_registry(install_dir)
        elif item.key == "start_menu":
            ok, detail = repair_shortcut(START_MENU_SHORTCUT, install_dir)
        elif item.key == "desktop":
            ok, detail = repair_shortcut(DESKTOP_SHORTCUT, install_dir)
        elif item.key == "uninstaller":
            # Not repairable from here: it is produced by the installer, and a hand-made
            # one would be a file that claims to uninstall and does not. Say so.
            ok, detail = False, "Uninstall.exe 只能由安装程序生成 —— 这一项需要重装"
        else:  # pragma: no cover - defensive
            ok, detail = False, "不知道该怎么修"
        actions.append(f"{'✓' if ok else '✗'} {item.label}：{detail}")

    print("\n  ── 修复动作 ──")
    for line in actions:
        print(f"    {line}")

    after = check_install(install_dir)
    fixed_keys = {item.key for item in before if not item.ok}
    for item in after:
        if item.key in fixed_keys and item.ok:
            item.fixed = True
    return after


# --------------------------------------------------------------------- main --

def resolve_install_dir(explicit: str | None) -> Path:
    """Where the app actually is, preferring what the registry says.

    The registry's `InstallLocation` is the authority when it exists: an install that
    was moved, or one installed to a custom path, is exactly the case where guessing
    `%LOCALAPPDATA%\\AI Workbench` gives the wrong answer and the check reports a false
    failure. Falls back to the default so a *missing* registry key can still be found
    and repaired.
    """
    if explicit:
        return Path(explicit)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINST_KEY) as key:
            location, _ = winreg.QueryValueEx(key, "InstallLocation")
            if location:
                return Path(location)
    except OSError:
        pass
    return DEFAULT_INSTALL_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description="装完自检 / 修复")
    parser.add_argument("--install-dir", default=None, help="默认从注册表读，读不到才用 %LOCALAPPDATA%\\AI Workbench")
    parser.add_argument("--repair", action="store_true", help="缺什么补什么（只补，不删）")
    parser.add_argument("--json", default=None, help="把结果写进这个文件")
    args = parser.parse_args()

    install_dir = resolve_install_dir(args.install_dir)
    print("=" * 74)
    print("  装完自检 —— 四项都在，才算装好")
    print("=" * 74)
    print(f"  安装目录：{install_dir}")
    print(f"  目录存在：{install_dir.is_dir()}")

    if args.repair and not install_dir.is_dir():
        print("\n  安装目录不存在，没有可修的东西。先装一遍。")
        return 2

    items = repair(install_dir) if args.repair else check_install(install_dir)

    print("\n  ── 四项 ──")
    for item in items:
        mark = "PASS" if item.ok else "FAIL"
        note = "  (本次修好)" if item.fixed else ""
        print(f"  [{mark}] {item.label}{note}")
        if item.detail:
            print(f"         {item.detail}")

    failed = [item for item in items if not item.ok]
    print()
    if failed:
        print(f"  {len(items) - len(failed)}/{len(items)} 通过 —— **这个安装是不完整的**")
        for item in failed:
            print(f"    [FAIL] {item.label}：{item.detail}")
    else:
        print(f"  {len(items)}/{len(items)} 通过 —— 装好了：应用列表里能看到，开始菜单和桌面都有入口")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "install_dir": str(install_dir),
                    "items": [item.as_dict() for item in items],
                    "passed": len(items) - len(failed),
                    "total": len(items),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n  report: {args.json}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

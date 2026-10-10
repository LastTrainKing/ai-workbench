"""测试脚本不许碰用户的真实数据目录。

## 为什么有这个文件

用户的数据（数据库、资料库、记忆、档案、产出物）住在：

    %LOCALAPPDATA%\\AI Workbench Data

而两个验收脚本曾经把它当成"自己的临时目录"无条件删掉：

    scripts/verify_installer.py   DATA_ASIDE = %LOCALAPPDATA%\\AI Workbench Data
    scripts/measure_startup.py    DATA_DIR   = 同一个目录
                                  shutil.rmtree(..., ignore_errors=True)

**第 14 轮已经真的发生过一次：50 MB 数据没了。** `ignore_errors=True` 还让失败静默，
所以"冷启动测量"可能是热启动，量出来的数字是假的。

## 这里的规矩

1. 测试数据只住在 `TEST_DATA_DIR`，和真实目录是两个目录。
2. 删任何东西之前，路径必须**证明**是测试目录 —— 相等、包含、被包含，三种都拒绝。
3. **没有 `--force` / `--force-real-data` 这类参数，永远不会有。**
   需要动真实数据的人应该自己去资源管理器里删，不该由测试脚本代劳。
4. 拒绝时 `exit 3`（既不是 0 也不是 1：不是"通过"，也不是"失败"，是"没跑"）。

## 测试安装怎么做到真正隔离

安装器把数据目录**写死**成 `%LOCALAPPDATA%\\AI Workbench Data`（不随 `/D=` 变），
所以光把程序装到 `AI Workbench InstallTest` 还不够 —— 应用照样用真实数据目录。

`point_install_at_test_data()` 解决这一点：装完之后改写那份副本自己的
`data-dir.txt`，指到 `TEST_DATA_DIR`。启动器（`scripts/lib.ps1`）和后端
（`app/core/config.py`）读的都是这一行，所以两边一起转过去。
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

LOCALAPPDATA = Path(os.environ["LOCALAPPDATA"])

#: 用户的真实数据。**只读，永远不删。**
REAL_DATA_DIR = LOCALAPPDATA / "AI Workbench Data"

#: 测试专用的数据目录。删的是它。
TEST_DATA_DIR = LOCALAPPDATA / "AI Workbench Data TEST"

#: 测试专用的安装目录。
TEST_INSTALL_DIR = LOCALAPPDATA / "AI Workbench InstallTest"


def _canonical(path: os.PathLike[str] | str) -> Path:
    """绝对、解析过、大小写不敏感 —— 用来比较，不用来删除。

    `resolve()` 会解开符号链接和 `..`，所以 `Data\\..\\Data` 和
    `Data` 会归一到同一个值；`normcase` 让 Windows 上大小写不同也算同一个。
    """
    return Path(os.path.normcase(str(Path(path).resolve())))


def _overlaps(a: Path, b: Path) -> bool:
    """a 与 b 相等、a 含 b、或 b 含 a —— 三种都算重叠。"""
    return a == b or a in b.parents or b in a.parents


def refuse_if_real(path: os.PathLike[str] | str, *, who: str) -> Path:
    """`path` 与真实数据目录有任何重叠就拒绝运行。返回规范化后的路径。

    这不是一个可以调参的检查：没有 `force` 参数，没有环境变量开关。
    调用方唯一的出路是把路径改成 `TEST_DATA_DIR`。
    """
    target = _canonical(path)
    real = _canonical(REAL_DATA_DIR)
    if _overlaps(target, real):
        print(file=sys.stderr)
        print("=" * 74, file=sys.stderr)
        print(f"  拒绝运行：{who}", file=sys.stderr)
        print("=" * 74, file=sys.stderr)
        print(f"  它要动的路径：{target}", file=sys.stderr)
        print(f"  用户的真实数据：{real}", file=sys.stderr)
        print(file=sys.stderr)
        print("  这两个路径重叠，继续下去会删掉用户的数据库、记忆、资料库和产出物。", file=sys.stderr)
        print("  第 14 轮已经这样丢过一次 50 MB 数据。", file=sys.stderr)
        print(file=sys.stderr)
        print(f"  测试请改用：{TEST_DATA_DIR}", file=sys.stderr)
        print("  真实数据要删，请你自己在资源管理器里删 —— 测试脚本不代劳。", file=sys.stderr)
        print("=" * 74, file=sys.stderr)
        raise SystemExit(3)
    return target


def safe_rmtree(path: os.PathLike[str] | str, *, who: str) -> bool:
    """只删测试目录。真实目录会在这里被挡下，整个进程退出 3。

    `ignore_errors` 是故意不要的：删不掉要看得见。删不干净意味着"冷启动"其实是
    热启动，而那种测量结果是假的 —— 静默失败在这里比报错更坏。
    """
    target = refuse_if_real(path, who=who)
    if not target.exists():
        return False
    shutil.rmtree(target)
    return True


def point_install_at_test_data(install_dir: Path) -> Path:
    """把测试安装的数据目录指到 `TEST_DATA_DIR`，返回它指向的 `.runtime`。

    安装器写死的那一行在 `data-dir.txt` 里，而启动器和后端都读它，所以改写这一份
    副本的文件就足够让整条链转过去 —— 不需要改产品，也不需要环境变量传递。
    """
    runtime = TEST_DATA_DIR / ".runtime"
    marker = Path(install_dir) / "data-dir.txt"
    if not marker.parent.is_dir():
        raise SystemExit(f"  没有安装目录，装完了再调用：{install_dir}")
    marker.write_text(str(runtime), encoding="utf-8")
    return runtime


def isolated_data_dir(
    requested: os.PathLike[str] | str | None = None,
    *,
    who: str = "验证运行",
) -> Path:
    """The data directory a verification run hands to the application. **Never the real one.**

    ## Why this function exists

    Every verifier that starts the application has to give it somewhere to write. The
    convenient way to do that is to say nothing and let the application resolve its own
    directory — and for an installed copy that resolution is
    `%LOCALAPPDATA%\\AI Workbench Data`, the user's real data. `verify_native_package.py`
    did exactly that at two of its call sites, and `runtime.ensure()` appended
    `logs\\native-startup.log` into the real directory. Nothing was lost, and it was still
    wrong: a verification run had written to the one place it is not allowed to write, and
    it did so silently.

    So the "let the application decide" option is gone. Asking for nothing gets a fresh
    temporary directory; asking for a path gets that path **or a refusal**. There is no
    third answer, and no argument that produces one.

    `requested` is checked with `refuse_if_real`, so a caller cannot reach the real
    directory by passing it explicitly either — not with `..`, not with a different case,
    not by pointing at a parent or a child of it.
    """
    if requested is None:
        return Path(tempfile.mkdtemp(prefix="aiworkbench-verify-data-"))
    return refuse_if_real(requested, who=who)


def dispose_isolated_data_dir(path: os.PathLike[str] | str) -> bool:
    """Remove a directory `isolated_data_dir` produced, at process exit.

    Registered rather than called inline because the application is what fills the
    directory and it may still hold a file open when the last check returns. A file held by
    a live process cannot be deleted on Windows, so an inline removal would raise out of
    whatever `finally` happened to be running — which is a failure that looks like a
    verification problem and is not one.
    """
    target = _canonical(path)
    if target == _canonical(REAL_DATA_DIR):
        return False
    atexit.register(shutil.rmtree, target, True)
    return True


__all__ = [
    "REAL_DATA_DIR",
    "TEST_DATA_DIR",
    "TEST_INSTALL_DIR",
    "dispose_isolated_data_dir",
    "isolated_data_dir",
    "point_install_at_test_data",
    "refuse_if_real",
    "safe_rmtree",
]

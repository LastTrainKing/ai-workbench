"""Delete the B-class items in CLEANUP_PLAN.md, and record what actually happened.

Written as a script rather than a sequence of shell commands for one reason: the plan says
"record the files that fail", and a shell pipeline that deletes a tree has nowhere to put
that. Here every failure is captured with its exception type and full path, and the run ends
with counts.

Deletion is not retried and nothing is forced. If a file is held, that is the answer, and the
report says so.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMP = pathlib.Path(os.environ["TEMP"])

#: `(label, path, mode)`. `mode` is "tree" for a directory, "glob" for a pattern under build/.
TARGETS: list[tuple[str, pathlib.Path, str]] = [
    ("安装包中间载荷（原生）", ROOT / "build" / "native-installer-payload", "tree"),
    ("安装包中间载荷（旧 Web）", ROOT / "build" / "installer-payload", "tree"),
    ("开发栈：文档快照", ROOT / ".runtime" / "snapshots", "tree"),
    ("开发栈：向量库", ROOT / ".runtime" / "qdrant", "tree"),
    ("开发栈：数据库集群", ROOT / ".runtime" / "pgdata", "tree"),
    ("开发栈：Redis", ROOT / ".runtime" / "redis", "tree"),
    ("开发栈：工作区", ROOT / ".runtime" / "workspace", "tree"),
    ("开发栈：文件存储", ROOT / ".runtime" / "storage", "tree"),
    ("开发栈：备份", ROOT / ".runtime" / "backups", "tree"),
    ("开发栈：日志目录", ROOT / ".runtime" / "logs", "tree"),
    ("开发栈：进程号", ROOT / ".runtime" / "pids", "tree"),
    ("开发栈：临时检查目录", ROOT / ".runtime" / "test-storage", "tree"),
    ("开发栈：下载检查", ROOT / ".runtime" / "download-check", "tree"),
    ("开发栈：启动检查", ROOT / ".runtime" / "open-launch-check", "tree"),
    ("开发栈：偏好检查", ROOT / ".runtime" / "pref-check", "tree"),
    ("开发栈：恢复检查", ROOT / ".runtime" / "recovery-check", "tree"),
    ("开发栈：最终自检", ROOT / ".runtime" / "finalcheck", "tree"),
    ("PyInstaller 中间目录", ROOT / "desktop-native" / "build", "tree"),
    ("安装验收输出日志", ROOT / "build" / "acceptance-logs", "tree"),
    ("release-freeze 中间件", ROOT / "build" / "release-freeze", "tree"),
    ("桌面过期截图", pathlib.Path.home() / "Desktop" / "screenshots", "tree"),
]

#: Loose files under `build/` that are one-off logs or scratch output. Evidence JSONs are
#: listed in CLEANUP_PLAN.md section A and are deliberately absent here.
LOOSE_BUILD_FILES = [
    "_after_upgrade.txt",
    "_before_upgrade.txt",
    "_fe19.txt",
    "_fe20.txt",
    "_mig_tables.txt",
    "_old15.py",
    "_pkg.txt",
    "_reg19.txt",
    "_reg19b.txt",
    "_reg20.txt",
    "backend-suite.log",
    "final-install.log",
    "install-selfcheck-final.json",
    "installer-report.json",
    "installer-static-check.json",
    "model-call-baseline.json",
    "mutations.log",
    "native-acceptance.log",
    "native-build.log",
    "native-installer-build.log",
    "onboarding-verification.json",
    "package-verify.log",
    "payload-report.json",
    "pgsql-trim-report.json",
    "rebuild-native-installer.log",
    "shell-verify.log",
    "startup-cold-warm.json",
    "windows-app-list-registry.txt",
]

#: Loose files directly in `.runtime/` — one-off build and start logs, and their scratch
#: companions. The dev stack's config files are removed with the rest of the directory.
LOOSE_RUNTIME_FILES = [
    "_run_acceptance_unelevated.ps1",
    "agent-policy.json.migrated",
    "ai-config.json",
    "api-start.log",
    "backend-tests.log",
    "build-all.log",
    "build-fe.log",
    "build-installer.log",
    "build-tauri.log",
    "build-zip.log",
    "chart_data.json",
    "cold-start.json",
    "cold-start.prefix.json.bak",
    "cold-start.txt",
    "demo1-second-half.json",
    "demo1-second-half.txt",
    "demo2-record.json",
    "demo2.txt",
    "demo3-record.json",
    "demo3-run1.txt",
    "demo3.txt",
    "fe-build.log",
    "infra.log",
    "installer-acceptance.log",
    "installer-build.log",
    "lock-evidence-before-fix.txt",
    "lock-evidence.txt",
    "payload-install-test.log",
    "rb-fe.log",
    "rb-installer.log",
    "rb-tauri.log",
    "rebuild.log",
    "rebuild.sh",
    "redact_secret.py",
    "redis.conf",
    "selfcheck.json",
    "start11.log",
    "start12.log",
    "start13.log",
    "start14.log",
    "start15.log",
    "tauri-build.log",
    "tauri-build2.log",
]

TEMP_PREFIXES = ("aw-final-", "awbnative-", "aiw-", "awb-", "ai-workbench")


def measure(path: pathlib.Path) -> tuple[int, int]:
    if path.is_file():
        try:
            return 1, path.stat().st_size
        except OSError:
            return 0, 0
    files = size = 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            try:
                size += (pathlib.Path(root) / name).stat().st_size
                files += 1
            except OSError:
                pass
    return files, size


def remove(path: pathlib.Path) -> tuple[bool, str]:
    """Delete a tree or file. Returns `(ok, detail)`; never raises."""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        return True, ""
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


def sweep(path: pathlib.Path, failures: list[dict]) -> tuple[int, int]:
    """Second pass over whatever `shutil.rmtree` left behind, one file at a time.

    `rmtree` gives up on the first failure and leaves the rest. Walking afterwards finds
    out whether it was one held file or the whole tree.
    """
    files = size = 0
    for root, dirs, names in os.walk(path, topdown=False):
        for name in names:
            p = pathlib.Path(root) / name
            files += 1
            try:
                size += p.stat().st_size
            except OSError:
                pass
            ok, detail = remove(p)
            if not ok:
                failures.append({"path": str(p), "error": detail})
        for name in dirs:
            p = pathlib.Path(root) / name
            try:
                p.rmdir()
            except OSError:
                pass
    try:
        path.rmdir()
    except OSError:
        pass
    return files, size


def main() -> int:
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    failures: list[dict] = []
    results: list[dict] = []

    print(f"清理开始 {started}")
    print()

    for label, path, _mode in TARGETS:
        if not path.exists():
            results.append({"label": label, "path": str(path), "state": "不存在", "files": 0, "bytes": 0})
            print(f"  --   {label:28} 不存在")
            continue
        files, size = measure(path)
        ok, detail = remove(path)
        if not ok:
            left_f, left_b = sweep(path, failures)
            detail = f"{detail}（二次遍历后剩 {left_f} 文件 / {left_b:,} 字节）"
            ok = not path.exists()
        state = "已删除" if ok else "失败"
        results.append(
            {"label": label, "path": str(path), "state": state, "files": files, "bytes": size, "detail": detail}
        )
        print(f"  {'OK' if ok else 'FAIL'} {label:28} {files:>6} 文件 {size/1048576:>8.1f} MB  {detail}")

    print()
    for name in LOOSE_BUILD_FILES:
        p = ROOT / "build" / name
        if not p.exists():
            continue
        size = p.stat().st_size
        ok, detail = remove(p)
        results.append({"label": f"build/{name}", "path": str(p), "state": "已删除" if ok else "失败",
                        "files": 1, "bytes": size, "detail": detail})
        if not ok:
            failures.append({"path": str(p), "error": detail})
        print(f"  {'OK' if ok else 'FAIL'} build/{name}")

    for name in LOOSE_RUNTIME_FILES:
        p = ROOT / ".runtime" / name
        if not p.exists():
            continue
        size = p.stat().st_size
        ok, detail = remove(p)
        results.append({"label": f".runtime/{name}", "path": str(p), "state": "已删除" if ok else "失败",
                        "files": 1, "bytes": size, "detail": detail})
        if not ok:
            failures.append({"path": str(p), "error": detail})
        print(f"  {'OK' if ok else 'FAIL'} .runtime/{name}")

    print()
    temp_n = temp_b = 0
    for p in list(TEMP.iterdir()):
        if not any(p.name.startswith(x) for x in TEMP_PREFIXES):
            continue
        files, size = measure(p)
        ok, detail = remove(p)
        if not ok:
            sweep(p, failures)
            ok = not p.exists()
        temp_n += 1
        temp_b += size
        if not ok:
            failures.append({"path": str(p), "error": detail})
    print(f"  {'OK' if not failures else '部分失败'} %TEMP 下 {temp_n} 项 / {temp_b/1048576:.1f} MB")

    # The dev runtime directory itself, once its contents are gone.
    rt = ROOT / ".runtime"
    if rt.is_dir():
        files, size = measure(rt)
        ok, detail = remove(rt)
        if not ok:
            sweep(rt, failures)
            ok = not rt.exists()
        results.append({"label": "开发栈运行时（空壳）", "path": str(rt), "state": "已删除" if ok else "失败",
                        "files": files, "bytes": size, "detail": detail})
        print(f"  {'OK' if ok else 'FAIL'} .runtime 空壳 {files} 文件")

    total_files = sum(r["files"] for r in results) + temp_n
    total_bytes = sum(r["bytes"] for r in results) + temp_b

    payload = {
        "started": started,
        "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
        "items": results,
        "temp_items": temp_n,
        "temp_bytes": temp_b,
        "removed_files": total_files,
        "removed_bytes": total_bytes,
        "failures": failures,
    }
    out = ROOT / "build" / "cleanup-report.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print()
    print(f"删除 {total_files} 个文件 / {total_bytes/1048576:.1f} MB")
    print(f"失败 {len(failures)} 项")
    for f in failures[:20]:
        print(f"   ! {f['path']}  <- {f['error']}")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

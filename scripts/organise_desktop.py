"""File the desktop's project litter into the project — archive, never delete.

The desktop had accumulated the project's own by-products: three release ZIPs, a
392 MB runtime backup, an agent workspace with 1,993 files in it, and a deck the
agent had written straight onto the desktop because the workspace default pointed
there. None of that belongs on the one screen the user looks at all day.

Three rules, and they are the whole design:

**Nothing is deleted.** Every item is *moved*, and a manifest with the reverse
mapping is written next to it, so the operation is one command away from being
undone. A cleanup that destroys something is not a cleanup, it is a risk.

**Nothing that belongs to the user is touched.** Shortcuts, `desktop.ini`, the
wallpaper URL and the game launchers are theirs; the project's job is to take its
own things away, not to tidy someone else's desk.

**Dry run first.** `--apply` is required to move anything, and the default prints
exactly what would happen.

Run:  python scripts/organise_desktop.py            # plan only
      python scripts/organise_desktop.py --apply    # do it
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = Path.home() / "Desktop"
MANIFEST = ROOT / "archive" / "desktop-organisation-manifest.json"

#: (source name on the desktop, destination relative to the project, why)
PLAN: tuple[tuple[str, str, str], ...] = (
    ("AI-Workbench-2.0.0-final.zip", "archive/releases", "发布 ZIP，不是源码"),
    ("ai-workbench-1.0.0-final.zip", "archive/releases", "过期发布 ZIP"),
    ("ai-workbench-1.1.0-final.zip", "archive/releases", "过期发布 ZIP"),
    ("ai-workbench-backups", "archive/runtime-backups", "runtime 备份，392 MB"),
    ("AI Workbench Workspace", "archive/workspace", "旧的 agent 工作区，含 1,864 个 .venv 文件"),
    ("答辩PPT.pptx", "samples/demo-output", "Demo 1 第一版产出样本（70,538 B / 11 页）"),
)

#: Files inside the old workspace worth keeping as evidence before it is archived.
EXTRACT: tuple[tuple[str, str], ...] = (
    ("毕业设计答辩PPT.pptx", "samples/demo-output/答辩PPT-第二版-2页-FAILED.pptx"),
)

#: Never touched. These belong to the user, and the project has no business here.
KEEP_PATTERNS: tuple[str, ...] = (
    ".lnk",
    ".url",
    "desktop.ini",
    "ai-workbench",  # the project itself
)


def inventory(folder: Path) -> list[dict[str, Any]]:
    """Every top-level entry, with size and file count. Read-only."""
    rows: list[dict[str, Any]] = []
    for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if entry.is_dir():
            files = sum(1 for _ in entry.rglob("*") if _.is_file())
            size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
        else:
            files = 1
            size = entry.stat().st_size
        rows.append(
            {
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "size_bytes": size,
                "file_count": files,
            }
        )
    return rows


def is_ours(name: str) -> bool:
    """True when the entry is the project's, not the user's."""
    if name in {source for source, _, _ in PLAN}:
        return True
    return False


def human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit == "B" else f"{size / 1:,.1f} {unit}"
        size /= 1024
    return f"{size} B"


def _clear_readonly(func, path, _exc):  # noqa: ANN001
    """Retry a failed delete after clearing the read-only bit.

    Git object files are written read-only, and on Windows that makes `unlink`
    raise. Without this, removing a copied directory fails part-way through.
    """
    import os
    import stat

    os.chmod(path, stat.S_IWRITE)
    func(path)


def _file_count(path: Path) -> int:
    return sum(1 for p in path.rglob("*") if p.is_file())


def _total_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def safe_move(source: Path, target: Path) -> tuple[bool, str]:
    """Copy, verify, then remove. Never the other way round.

    `shutil.move` looks like the obvious tool and is not safe here: on Windows an
    `os.rename` of a directory can fail with "access denied" (an open handle, a
    read-only child), and `shutil.move` then falls back to copying and *deleting the
    source* — a fallback that removes files it has not yet proven it copied. That is
    a destructive default, and it fired here.

    The order below is the whole point: the destination is verified against the
    source first, and only then is the source removed. If verification fails, both
    copies exist and nothing has been lost.
    """
    if source.is_file():
        shutil.copy2(source, target)
        if not target.is_file() or target.stat().st_size != source.stat().st_size:
            return False, "复制后大小不一致，源文件保留"
        source.unlink()
        return True, ""

    shutil.copytree(source, target, dirs_exist_ok=False)
    src_files, dst_files = _file_count(source), _file_count(target)
    if src_files != dst_files:
        return False, f"文件数不一致（源 {src_files} / 目标 {dst_files}），两边都保留"
    src_bytes, dst_bytes = _total_bytes(source), _total_bytes(target)
    if src_bytes != dst_bytes:
        return False, f"字节数不一致（源 {src_bytes} / 目标 {dst_bytes}），两边都保留"

    shutil.rmtree(source, onexc=_clear_readonly)
    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually move the files")
    args = parser.parse_args()

    before = inventory(DESKTOP)

    print("=" * 78)
    print("  桌面整理 —— 归档，不删除")
    print("=" * 78)
    print(f"\n  整理前桌面共 {len(before)} 个顶层条目：")
    for row in before:
        tag = "项目" if is_ours(row["name"]) else "用户"
        print(
            f"    [{tag}] {row['name']:<38} {human(row['size_bytes']):>12}  "
            f"{row['file_count']:>6} 个文件"
        )

    print("\n  计划：")
    moves: list[tuple[Path, Path, str]] = []
    for source_name, destination, why in PLAN:
        source = DESKTOP / source_name
        if not source.exists():
            print(f"    [跳过] {source_name}（已不存在）")
            continue
        target = ROOT / destination / source_name
        print(f"    {source_name}\n        → {target.relative_to(ROOT)}\n        因为：{why}")
        moves.append((source, target, why))

    extracts: list[tuple[Path, Path]] = []
    for source_name, destination in EXTRACT:
        source = DESKTOP / "AI Workbench Workspace" / source_name
        if source.exists():
            extracts.append((source, ROOT / destination))
            print(f"    [证据] {source_name}\n        → {destination}")

    if not args.apply:
        print("\n  这是计划，没有移动任何东西。加 --apply 执行。")
        return 0

    # --- make sure the destinations exist ------------------------------------
    for _, target, _ in moves:
        target.parent.mkdir(parents=True, exist_ok=True)
    for _, target in extracts:
        target.parent.mkdir(parents=True, exist_ok=True)

    # --- extract the evidence first, so it survives the archive move ---------
    for source, target in extracts:
        if target.exists():
            print(f"    [跳过] {target.name} 已存在")
            continue
        shutil.copy2(source, target)
        print(f"    [复制] {source.name} → {target.relative_to(ROOT)}  ({target.stat().st_size:,} B)")

    # --- move, one item at a time, verifying each ---------------------------
    done: list[dict[str, Any]] = []
    for source, target, why in moves:
        if target.exists():
            print(f"    [冲突] {target} 已存在，未覆盖，跳过 {source.name}")
            continue
        files_before = (
            sum(1 for _ in source.rglob("*") if _.is_file()) if source.is_dir() else 1
        )
        ok, reason = safe_move(source, target)
        files_after = (
            sum(1 for _ in target.rglob("*") if _.is_file()) if target.is_dir() else 1
        )
        print(
            f"    [{'OK' if ok else 'X'}] {source.name} → {target.relative_to(ROOT)}  "
            f"({files_before} → {files_after} 个文件)"
            + (f"  {reason}" if reason else "")
        )
        done.append(
            {
                "source": str(source),
                "destination": str(target),
                "file_count": files_after,
                "why": why,
                "verified": ok,
                "note": reason,
            }
        )

    after = inventory(DESKTOP)
    manifest = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "desktop": str(DESKTOP),
        "moved": done,
        "extracted": [{"from": str(s), "to": str(t)} for s, t in extracts],
        "before": before,
        "after": after,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n  整理后桌面共 {len(after)} 个顶层条目：")
    for row in after:
        tag = "项目" if is_ours(row["name"]) else "用户"
        print(
            f"    [{tag}] {row['name']:<38} {human(row['size_bytes']):>12}  "
            f"{row['file_count']:>6} 个文件"
        )

    leftover = [row["name"] for row in after if is_ours(row["name"])]
    print(f"\n  清单已写入 {MANIFEST.relative_to(ROOT)}")
    print(f"  桌面上残留的项目条目：{leftover or '（无）'}")
    print("  没有删除任何文件。")
    return 1 if leftover else 0


if __name__ == "__main__":
    raise SystemExit(main())

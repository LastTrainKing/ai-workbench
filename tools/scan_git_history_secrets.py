"""扫描**全部 Git 历史**中的凭据，而不只是当前工作树。

## 为什么必须扫历史

`scan_for_secrets.py` 只走 `git ls-files` —— 已跟踪文件的当前内容。这答不了 §4.2 真正
要问的问题："有没有真实凭据**曾经**进过这个仓库"。一个密钥被提交、然后在下一次提交里
删掉，当前树是干净的，而它仍然躺在历史里，任何克隆的人都能拿到。**当前树干净不等于
仓库安全。**

## 它做什么

1. 遍历历史里的**每一个 blob**（`git rev-list --objects --all` → `git cat-file --batch`），
   不是只遍历每个提交的 diff —— diff 会漏掉那些从未被修改过的文件。
2. 两遍判断，分开报告：
   * **形状匹配**：`sk-` 开头的 32 位十六进制、AWS、PEM、Bearer、GitHub token 等。
     命中不等于泄漏 —— 测试夹具和文档里的假值也长这样。
   * **逐字比对**：把本机运行时配置里的真实凭据读进来，在历史里精确查找。
     这一遍命中才是真问题。
3. **绝不打印凭据的值。** 命中的报告只给对象 id、路径、行号和脱敏片段。

## 用法

    python tools/scan_git_history_secrets.py

退出码非 0 表示发现了真实凭据。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from pathlib import Path

from _runtime_dir import credential_files, runtime_dir

REPO = Path(__file__).resolve().parents[1]

#: 形状模式。**命中不等于泄漏** —— 测试夹具、文档示例、占位符都长这样。
SHAPES: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("DeepSeek/OpenAI 风格密钥", re.compile(rb"sk-[A-Za-z0-9]{20,}")),
    ("AWS access key", re.compile(rb"AKIA[0-9A-Z]{16}")),
    ("PEM 私钥", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("Bearer token", re.compile(rb"Bearer\s+[A-Za-z0-9._\-]{24,}")),
    ("JWT", re.compile(rb"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
)

#: 明显不是真凭据的写法。命中这些形状就跳过，免得报告被测试夹具淹没。
BENIGN = (
    b"sk-fake", b"sk-test", b"sk-your", b"sk-xxx", b"sk-placeholder",
    b"AKIAIOSFODNN7EXAMPLE", b"your-", b"example", b"placeholder", b"changeme",
)

#: 本机运行时配置里可能放着真实凭据的位置（只在本地读，绝不打印值）。
LOCAL_CREDENTIAL_FILES = (
    ".runtime/ai-config.json",
    ".env",
)


def _run(args: list[str]) -> str:
    """跑一个 git 命令，返回 **str**。

    返回值类型必须明确。第一版是 `str(_run(...))` 而 `_run` 返回 bytes —— `str(b"a
b")`
    得到的是字面量 `b'a
b'`（转义的 `
`，不是换行），于是按换行切分只得到一行，40 位
    id 的过滤全部落空。结果是**扫描器扫了 0 个对象，却报"干净"**。

    那正是这个工具要防的东西：一个什么都没测到的绿色结论。所以现在不再有 `text` 开关，
    调用方也不需要猜。
    """
    return subprocess.run(
        args, cwd=REPO, capture_output=True, check=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout


#: `.env` 里**键名像密钥**的那些。只取这些键的值。
#:
#: 第一版是按"长串"捞的（`[A-Za-z0-9_\\-]{40,}`），结果捞到的是注释分隔线 —— `.env` 里有
#: 一条 60 个连字符的分隔线，而源码注释里到处是同样的分隔线，于是 1406 处"真实凭据泄漏"，
#: 全是假阳性。**一个报 1406 个假阳性的扫描器和报 0 个的一样没用**：前者让人不再看结论，
#: 后者让人以为没问题。
SECRET_KEY_NAMES = re.compile(rb"^[ \t]*([A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)[A-Z0-9_]*)=",
                              re.MULTILINE)

#: 值必须至少这么长才算凭据。短值几乎一定是占位符或普通配置。
MIN_CREDENTIAL_LENGTH = 16


def real_credentials(runtime: pathlib.Path | None) -> list[tuple[str, bytes]]:
    """本机真实凭据，用于逐字比对。**值只在这里出现，不进入任何输出。**

    来源按"这是什么"取值，而不是按"它长什么样"：

    * `<repo>/.env` —— 只取键名像密钥的那些键的值，空值跳过
    * 本机运行时目录里的每一个 `ai-config*.json` —— 只取 `api_key` / `embedding_api_key`

    **运行时目录由 `_runtime_dir` 解析，与当前树那个扫描器共用同一份实现。**
    这里原先只认 `<repo>/.runtime`，于是在安装版机器上比对样本是空的，而它照样打印一个
    读起来像"干净"的零 —— 和第一个扫描器当初的毛病一模一样。
    """
    found: list[tuple[str, bytes]] = []

    env_path = REPO / ".env"
    if env_path.is_file():
        try:
            raw = env_path.read_bytes()
        except OSError:
            raw = b""
        for line in raw.splitlines():
            match = SECRET_KEY_NAMES.match(line)
            if not match:
                continue
            value = line.split(b"=", 1)[1].strip().strip(b'"').strip(b"'")
            if len(value) < MIN_CREDENTIAL_LENGTH:
                continue
            found.append((".env", value))

    for label, path in credential_files(runtime):
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(config, dict):
            for key in ("api_key", "embedding_api_key"):
                value = config.get(key)
                if isinstance(value, str) and len(value) >= MIN_CREDENTIAL_LENGTH:
                    found.append((label, value.encode()))

    # 去重：同一个值从两个地方读到只报一次。
    unique: dict[bytes, str] = {}
    for source, value in found:
        unique.setdefault(value, source)
    return [(source, value) for value, source in unique.items()]


def history_blobs() -> list[str]:
    """历史里的每一个对象 id（blob 与 tree 都算，读的时候按类型过滤）。"""
    out = _run(["git", "rev-list", "--objects", "--all"])
    ids = {line.split(" ", 1)[0] for line in out.splitlines() if line.strip()}
    return sorted(i for i in ids if len(i) == 40)


def blob_paths() -> dict[str, str]:
    """blob id → 首次出现的路径，用于报告可读。"""
    out = _run(["git", "rev-list", "--objects", "--all"])
    mapping: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2 and len(parts[0]) == 40:
            mapping.setdefault(parts[0], parts[1])
    return mapping


def read_blobs(ids: list[str]) -> dict[str, bytes]:
    """批量读内容。一次 `cat-file --batch`，不是每个对象起一个进程。"""
    if not ids:
        return {}
    proc = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=REPO,
        input="\n".join(ids).encode(),
        capture_output=True,
        check=True,
    )
    out = proc.stdout
    contents: dict[str, bytes] = {}
    pos = 0
    while pos < len(out):
        end = out.find(b"\n", pos)
        if end < 0:
            break
        header = out[pos:end].decode("utf-8", "replace").split()
        pos = end + 1
        if len(header) < 3:
            break
        sha, kind, size = header[0], header[1], int(header[2])
        body = out[pos : pos + size]
        pos += size + 1
        if kind == "blob":
            contents[sha] = body
    return contents


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scan_git_history_secrets.py")
    parser.add_argument("--runtime-dir", default="",
                        help="本机运行时目录（放 ai-config 的地方）；不给就自己找")
    args = parser.parse_args(argv)
    print("=== 全部 Git 历史秘密扫描 ===")
    print(f"  仓库: {REPO}")
    head = _run(["git", "rev-parse", "HEAD"]).strip()
    commits = _run(["git", "rev-list", "--count", "--all"]).strip()
    print(f"  HEAD: {head[:12]}  历史提交数: {commits}")

    ids = history_blobs()
    paths = blob_paths()
    contents = read_blobs(ids)
    total_bytes = sum(len(b) for b in contents.values())
    print(f"  扫描 blob 数: {len(contents)}  合计 {total_bytes:,} 字节")

    # **扫到 0 个对象不是"干净"，是"没扫到东西"。** 第一版正是在这里报了一个绿色的
    # 结论：解析 bug 让 blob 列表为空，而脚本照样打印"历史与当前树均未发现真实凭据"。
    # 一个什么都没测到的通过结论，比没有结论更危险。
    if len(contents) < 10:
        print(f"  [中止] 只扫到 {len(contents)} 个对象，明显不是这个仓库的真实规模。")
        print("         结果不可信 —— 这是扫描器自身的问题，不是仓库的问题。")
        return 2

    runtime = runtime_dir(args.runtime_dir or None, repo=REPO)
    credentials = real_credentials(runtime)
    if runtime is None:
        print("  本机运行时目录: 没找到")
        print("    **第二遍（逐字比对本机凭据）本次没有跑。** 下面的结论只来自形状匹配。")
    else:
        print(f"  本机运行时目录: {runtime}")
        if credentials:
            print(f"  本机凭据比对样本: {len(credentials)} 个（值不打印）")
        else:
            print("  本机凭据比对样本: 0 个 —— 这个目录里没有存凭据的配置，第二遍空跑")

    # ---- 第一遍：真实凭据逐字比对 ----
    real_hits: list[str] = []
    for sha, body in contents.items():
        for source, value in credentials:
            if value in body:
                real_hits.append(f"{sha[:12]} {paths.get(sha, '?')}（与 {source} 中的凭据逐字相同）")

    # ---- 第二遍：形状匹配 ----
    shape_hits: dict[str, list[str]] = {}
    for sha, body in contents.items():
        for name, pattern in SHAPES:
            for match in pattern.finditer(body):
                value = match.group(0)
                if any(b in value.lower() for b in BENIGN):
                    continue
                shape_hits.setdefault(name, []).append(
                    f"{sha[:12]} {paths.get(sha, '?')} → {value[:7]}…（脱敏）"
                )

    print("\n--- 真实凭据逐字比对 ---")
    if real_hits:
        for hit in real_hits:
            print(f"  [命中] {hit}")
    else:
        print("  [干净] 历史中没有任何本机真实凭据")

    print("\n--- 形状匹配（命中不等于泄漏）---")
    if shape_hits:
        for name, hits in sorted(shape_hits.items()):
            print(f"  {name}: {len(hits)} 处")
            for hit in hits[:3]:
                print(f"      {hit}")
    else:
        print("  无命中")

    print("\n=== 结论 ===")
    if real_hits:
        print(f"  真实凭据泄漏 {len(real_hits)} 处 —— **阻断发布**，且需要轮换该凭据")
        return 1
    print("  历史与当前树均未发现真实凭据")
    return 0


if __name__ == "__main__":
    sys.exit(main())

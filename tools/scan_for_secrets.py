"""Scan exactly the files that are about to be shipped, for anything that looks like a key.

Two passes, and the second one is the important one:

1. Patterns — vendor key shapes (`sk-…`, `AKIA…`, PEM blocks), and assignments like
   `password = "…"` where the value is not obviously a placeholder.
2. **The user's actual credentials, compared literally.** The API key that is stored in the
   data directory is read and searched for in every file that is going into the package. A
   pattern scan can miss a key that does not look like one; this cannot. The value is never
   printed — only whether it was found, and where.

Nothing here is skipped because a count came back zero.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]


def runtime_dir(explicit: str | None = None) -> pathlib.Path | None:
    """Where this machine keeps its runtime directory, or `None` if there is not one.

    **This used to be a literal path** — `C:\\Users\\user\\AppData\\Local\\AI Workbench Data\\.runtime`,
    the directory on the machine that built the package. On any other machine it does not
    exist, so `real_credentials()` returned an empty dict, and the run printed
    `本机真实凭据: 0 项` — which reads exactly like a clean result while the second pass was
    comparing against nothing.

    That is the third time this repository has had a scanner report "nothing found" for
    "nothing looked at", so the fix is deliberately in two parts: find the directory the way
    the application does, **and say out loud when there is nothing to compare**. The second
    half is the one that matters; the first half only makes the second half rare.

    Resolution order, matching `app.core.config`:

    1. `--runtime-dir DIR` — taken as the runtime directory itself.
    2. `AI_WORKBENCH_DATA_DIR` — taken as the *data* directory, so `.runtime` is appended.
    3. `%LOCALAPPDATA%\\AI Workbench Data\\.runtime` — the installed application's location.
    4. `<repo>/.runtime` — a development checkout.
    """
    if explicit:
        return pathlib.Path(explicit)
    data = os.environ.get("AI_WORKBENCH_DATA_DIR", "").strip()
    if data:
        return pathlib.Path(data) / ".runtime"
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        candidate = pathlib.Path(local) / "AI Workbench Data" / ".runtime"
        if candidate.is_dir():
            return candidate
    dev = REPO / ".runtime"
    return dev if dev.is_dir() else None


PATTERNS = [
    ("供应商密钥", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("AWS key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("PEM 私钥", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Bearer 令牌", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{24,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
]

#: Values that are plainly not credentials.
PLACEHOLDER = (
    "xxx", "your", "example", "dummy", "fake", "placeholder", "sample", "test",
    "0000000000", "1234567890", "0123456789abcdef", "aaaaaaaa", "change-me", "change_me",
    "changeme", "replace-me", "replace_me", "here", "todo", "none", "null", "empty",
)

ASSIGNMENT = re.compile(
    r"(?i)\b(api_?key|secret|password|passwd|token|access_?key|private_?key)\b\s*[:=]\s*"
    r"[\"']?([^\s\"'#,\n]{8,})"
)


def looks_placeholder(value: str) -> bool:
    lowered = value.lower()
    if any(token in lowered for token in PLACEHOLDER):
        return True
    body = lowered.removeprefix("sk-")
    return bool(body) and len(set(body)) <= 2


def real_credentials(data_dir: pathlib.Path | None) -> dict[str, str]:
    """Whatever is actually stored on this machine, keyed by what it is.

    Returns an empty dict both when there are no credentials *and* when there is no data
    directory to look in. The caller is responsible for telling those two apart — see
    `main`, which prints which one it was.
    """
    found: dict[str, str] = {}
    config_dir = (data_dir / "ai-config") if data_dir else None
    if config_dir is not None and config_dir.is_dir():
        for path in config_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for field in ("api_key", "embedding_api_key"):
                value = str(data.get(field) or "").strip()
                if len(value) >= 16:
                    found[f"{path.stem[:8]}:{field}"] = value
    env_file = REPO / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            name, _, value = line.partition("=")
            value = value.strip().strip("\"'")
            if len(value) >= 12 and any(
                token in name.upper() for token in ("KEY", "TOKEN", "SECRET", "PASSWORD")
            ):
                found[f".env:{name.strip()}"] = value
    return found


def main(argv: list[str] | None = None) -> int:
    """With no argument, scan the tracked files. With `--root DIR`, scan that tree.

    The second form is the one that matters at delivery time: it scans the copy that was
    actually unpacked from the archive, which is the only version of the question "what is
    being handed over" that has a truthful answer.

    `--runtime-dir DIR` says where this machine's credentials live. It is optional, but on a
    machine where the directory cannot be found the second pass does not run — and the
    output says so rather than reporting a zero that looks like a pass.
    """
    argv = list(argv if argv is not None else sys.argv[1:])
    root = REPO
    explicit_runtime: str | None = None
    unrecognised: list[str] = []
    index = 0
    while index < len(argv):
        flag = argv[index]
        if flag in {"--root", "--runtime-dir"} and index + 1 < len(argv):
            if flag == "--root":
                root = pathlib.Path(argv[index + 1]).resolve()
            else:
                explicit_runtime = argv[index + 1]
            index += 2
            continue
        unrecognised.append(flag)
        index += 1
    if unrecognised:
        print(f"  无法识别的参数: {' '.join(unrecognised)}", file=sys.stderr)
        print("  用法: scan_for_secrets.py [--root DIR] [--runtime-dir DIR]", file=sys.stderr)
        return 2

    if root != REPO:
        files = [path for path in root.rglob("*") if path.is_file()]
        print(f"  扫描目录: {root}")
    else:
        try:
            listing = subprocess.run(
                ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True
            ).stdout.decode("utf-8")
        except (OSError, subprocess.CalledProcessError) as exc:
            # A traceback here says "the tool is broken"; the actual problem is "this is not
            # a git checkout", and the reader can act on that.
            print(f"  无法列出被跟踪的文件（{type(exc).__name__}）：{REPO} 不是 git 检出。", file=sys.stderr)
            print("  请用 --root DIR 指定要扫描的目录。", file=sys.stderr)
            return 2
        files = [REPO / name for name in listing.split("\0") if name]

    data_dir = runtime_dir(explicit_runtime)
    credentials = real_credentials(data_dir)
    print(f"  待打包文件: {len(files)}")
    if data_dir is None:
        print("  本机运行时目录: 没找到")
        print("    **第二遍（逐字比对本机凭据）本次没有跑。** 下面的结论只来自形状匹配。")
        print("    安装版在 %LOCALAPPDATA%\\AI Workbench Data\\.runtime；")
        print("    开发检出在 <repo>/.runtime。也可以用 --runtime-dir 指定。")
    else:
        print(f"  本机运行时目录: {data_dir}")
        if credentials:
            print(f"  本机真实凭据: {len(credentials)} 项（值不打印）")
            for name in credentials:
                print(f"    - {name}（{len(credentials[name])} 字符）")
        else:
            print("  本机真实凭据: 0 项 —— 但这个目录里没有 `ai-config/`，第二遍空跑")

    findings: list[str] = []
    for path in files:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        relative = path.relative_to(root).as_posix()

        for label, pattern in PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(0)
                if looks_placeholder(value):
                    continue
                findings.append(f"{relative}: {label} -> {value[:12]}…")

        for line_number, line in enumerate(text.splitlines(), start=1):
            if "allowlist secret" in line.lower():
                continue
            match = ASSIGNMENT.search(line)
            if match and not looks_placeholder(match.group(2)):
                findings.append(f"{relative}:{line_number}: 赋值 {match.group(1)}")

        for name, value in credentials.items():
            if value and value in text:
                findings.append(f"{relative}: **包含本机真实凭据 {name}**")

    print()
    if findings:
        print(f"  ✗ 命中 {len(findings)} 处，需逐条判断：")
        for item in findings[:40]:
            print(f"      {item}")
        if len(findings) > 40:
            print(f"      …另有 {len(findings) - 40} 处")
        return 1

    if credentials:
        print("  ✓ 没有命中：没有供应商密钥形状、没有 PEM 私钥，也不包含本机任何真实凭据")
    else:
        print("  ✓ 形状匹配没有命中：没有供应商密钥形状、没有 PEM 私钥")
        print("    **「不包含本机真实凭据」这一条本次没有验证** —— 见上面的运行时目录那行。")
    print("    （命中为零也逐文件检查过了，不是跳过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

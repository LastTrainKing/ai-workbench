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
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
DATA = pathlib.Path(r"C:\Users\user\AppData\Local\AI Workbench Data\.runtime")

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


def real_credentials() -> dict[str, str]:
    """Whatever is actually stored on this machine, keyed by what it is."""
    found: dict[str, str] = {}
    config_dir = DATA / "ai-config"
    if config_dir.is_dir():
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
    """
    argv = argv if argv is not None else sys.argv[1:]
    root = REPO
    if argv and argv[0] == "--root":
        root = pathlib.Path(argv[1]).resolve()
        files = [path for path in root.rglob("*") if path.is_file()]
        print(f"  扫描目录: {root}")
    else:
        listing = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True
        ).stdout.decode("utf-8")
        files = [REPO / name for name in listing.split("\0") if name]

    credentials = real_credentials()
    print(f"  待打包文件: {len(files)}")
    print(f"  本机真实凭据: {len(credentials)} 项（值不打印）")
    for name in credentials:
        print(f"    - {name}（{len(credentials[name])} 字符）")

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

    print("  ✓ 没有命中：没有供应商密钥形状、没有 PEM 私钥，也不包含本机任何真实凭据")
    print("    （命中为零也逐文件检查过了，不是跳过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

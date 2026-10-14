"""Every relative link in the documentation must point at something that exists.

A broken link in a README is the first thing a visitor hits and the last thing anyone checks,
because nothing fails when one rots. This is that check, run in CI so it cannot rot quietly.

It found two on its first run: `docs/archive/PROJECT_DELIVERY.md` pointed at
`docs/resume-project.md` and `docs/interview-questions.md` — both written relative to the
repository root, while the file itself sits one directory down. The targets existed and the
links were broken, which is the shape that survives review.

**Inline code and fenced blocks are stripped before scanning.** A link checker that does not
do this reports code as documentation: `[?&](token|access_token|api_key|key)=` is a grep
pattern in `CODE-REVIEW.md`, and the first version of this tool called it a missing file.

    python tools/check_doc_links.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: `[text](target)` and `![alt](target)`.
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")

#: ```-fenced blocks, with or without a language tag.
FENCED = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$", re.MULTILINE | re.DOTALL)

#: `inline code` — one or more backticks, no newline inside.
INLINE = re.compile(r"`+[^`\n]*`+")

#: Not a path we can check: a URL, an anchor, or an email.
EXTERNAL = ("http://", "https://", "mailto:", "#", "//")


def markdown_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=str(REPO), capture_output=True, text=True
    ).stdout
    return [REPO / line for line in out.splitlines() if line.strip()]


def targets_in(path: Path) -> list[str]:
    """The relative link targets in one file, with code removed first."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    text = FENCED.sub("", text)
    text = INLINE.sub("", text)

    found: list[str] = []
    for raw in LINK.findall(text):
        target = raw.strip()
        if not target or target.startswith(EXTERNAL):
            continue
        # `path#anchor` and `path "title"` both appear in the wild.
        target = target.split("#", 1)[0].split('"', 1)[0].strip()
        if target:
            found.append(target)
    return found


def main() -> int:
    files = markdown_files()
    broken: list[str] = []
    checked = 0

    for path in files:
        for target in targets_in(path):
            checked += 1
            if not (path.parent / target).resolve().exists():
                broken.append(f"{path.relative_to(REPO).as_posix()}  ->  {target}")

    print(f"文档相对链接: 检查 {checked} 个，来自 {len(files)} 个 Markdown 文件")
    if broken:
        print(f"\n**指不到东西的 {len(broken)} 个：**")
        for line in broken:
            print("  " + line)
        print("\n（外部 URL 不在这里检查 —— 那需要一个网络请求，而 CI 不该依赖别人的可用性。）")
        return 1

    print("全部指向存在的文件或目录")
    return 0


if __name__ == "__main__":
    sys.exit(main())

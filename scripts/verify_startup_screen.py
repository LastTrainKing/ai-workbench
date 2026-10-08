"""启动画面必须是聊天框，不是进度页 —— 一条能重复跑的断言。

用户的验收：

    不要"Starting"那一屏挡在前面。窗口打开 → **直接是聊天框**，能看见、能打字。
    底下一行小的状态就行（"正在启动服务…"），就绪之前发送按钮置灰并说明，
    **不许拿一整屏遮住用户**。启动失败要有明确说明和重试，不能只是永远转圈。

`capture_startup_screen.py` 给出的是**那一刻长什么样**的截图；这个文件给的是
**以后每次改都还成立**的检查。两者要的是一件事的两面，缺一个都不够：

  * 只有截图 —— 下一次有人把 checklist 加回来，没人会发现；
  * 只有断言 —— 断言可以全绿而画面依然是一整屏进度条。

## 断言的是"性质"，不是"存在性"

不是"文件里有 textarea"（有 textarea 的进度页也存在），而是：

  * 那个 textarea **没有被 disabled**（能打字才叫能打字）；
  * 发送按钮**是 disabled 且带着一句说明**（不是灰着不说话，也不是能点）；
  * 有一行**小的**状态（不是整屏的 h1 + 进度条）；
  * 失败时**有重试按钮**（不是永远转圈）；
  * **没有**服务清单那种整屏 checklist。

    .venv/Scripts/python.exe scripts/verify_startup_screen.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPLASH = ROOT / "desktop" / "splash" / "index.html"

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")


def main() -> int:
    if not SPLASH.is_file():
        print(f"  找不到 {SPLASH}")
        return 2
    html = SPLASH.read_text(encoding="utf-8")

    print("=" * 74)
    print("  启动画面：是聊天框，不是进度页")
    print("=" * 74)

    # --- the composer is real and live ---------------------------------------
    textarea = re.search(r"<textarea\b[^>]*>", html, re.S)
    check("有一个真的 textarea（不是 div 装的假框）", textarea is not None)
    if textarea:
        tag = textarea.group(0)
        check("它没有被 disabled（能打字才叫能打字）", "disabled" not in tag.lower(), tag[:90])
        check("它有 data-testid，能被子测试点到", "startup-draft" in tag)

    # --- the send button is greyed **with a reason** -------------------------
    send = re.search(r"<button[^>]*id=\"send\"[^>]*>", html, re.S)
    check("发送按钮存在", send is not None)
    if send:
        check("发送按钮是 disabled 的", "disabled" in send.group(0).lower(), send.group(0)[:90])
    check(
        "灰掉的同时说了为什么（不是灰着不说话）",
        bool(re.search(r'id="why"[^>]*>', html)) and "服务" in html,
    )

    # --- one small status line, not a full screen ---------------------------
    check("有一行状态（id=statusline）", 'id="statusline"' in html)
    check("那行状态是「小的」（CSS 里是 11.5px）", "font-size: 11.5px" in html)

    # --- no full-page checklist ---------------------------------------------
    #
    # The thing that was removed. `<ol class="steps">` was the three-item checklist and
    # `.services` was the per-service pip list; both were a whole screen of progress
    # before anything the user could touch.
    check("没有整屏的服务清单（.services）", 'class="services"' not in html and "id=\"services\"" not in html)
    check("没有整屏的步骤条（id=steps）", 'id="steps"' not in html)
    check("没有整屏的大标题当遮罩（.intro h1 是 19px，不是 hero 尺寸）", "font-size: 19px" in html)

    # --- failure is not an endless spinner -----------------------------------
    check("失败时有重试按钮", bool(re.search(r'id="retry"', html)))
    check("失败时能打开日志目录", bool(re.search(r'id="logs"', html)))
    check("失败面板是有内容才显示（.show）", ".error.show" in html)

    # --- the draft is carried over -------------------------------------------
    check("打字会推给外壳（set_startup_draft）", "set_startup_draft" in html)
    check("离开页面前再推一次（不丢最后 120ms）", "beforeunload" in html)

    failed = [item for item in results if not item[1]]
    print()
    print(f"  {len(results) - len(failed)}/{len(results)} 通过")
    for label, _ok, detail in failed:
        print(f"    [FAIL] {label} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

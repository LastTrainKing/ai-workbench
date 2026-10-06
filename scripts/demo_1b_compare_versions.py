"""Demo 1, second half — does the preference actually change the next deck?

The first deck was rejected with "图表太少，几乎全是文字；页数也偏多". If the memory
system works, the second run should be planned differently — more visual, fewer pages.

This is the claim the whole product rests on, so it is checked by opening both files
and comparing them, not by asking the agent whether it took the preference into
account.

Run:  python scripts/demo_1b_compare_versions.py
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
GOAL = (
    "用我导入的毕业设计资料，做一份 15 分钟的答辩 PPT，包含系统架构、数据库设计、AI 模块三部分。"
    "必须用 document_write 生成 .pptx 文件。"
)


def deck_facts(path: Path) -> dict[str, object]:
    """Everything the comparison needs, read from the file itself."""
    from pptx import Presentation

    deck = Presentation(str(path))
    titles: list[str] = []
    images = 0
    text_chars = 0
    for slide in deck.slides:
        for shape in slide.shapes:
            if shape.shape_type == 13:
                images += 1
            if getattr(shape, "has_text_frame", False):
                text = shape.text_frame.text.strip()
                if text:
                    text_chars += len(text)
                    titles.append(text.split("\n")[0])
    return {
        "size": path.stat().st_size,
        "slides": len(deck.slides),
        "images": images,
        "text_chars": text_chars,
        "titles": titles,
    }


def main() -> int:
    client = httpx.Client(timeout=180)
    token = client.post(
        f"{BASE}/auth/login", json={"identifier": "demo", "password": "demo1234"}
    ).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # What the system currently believes about this user's presentation standards.
    memories = client.get(
        f"{BASE}/memories", headers=headers, params={"status": "active"}
    ).json()["data"]["memories"]
    relevant = [
        m
        for m in memories
        if any(word in m["content"] for word in ("图表", "页数", "演示", "PPT", "汇报"))
    ]
    print("=" * 74)
    print("  当前生效的、与演示文稿相关的偏好")
    print("=" * 74)
    for memory in relevant:
        print(f"  [{memory['kind']}] {memory['content']}")
    if not relevant:
        print("  （无）—— 记忆没有生效，第二版不会变")

    # ------------------------------------------------------------ second run --
    print()
    print("=" * 74)
    print("  用同一个目标再做一份")
    print("=" * 74)
    created = client.post(
        f"{BASE}/build-runs",
        headers=headers,
        json={"goal": GOAL, "deliverable_kind": "pptx", "title": "答辩 PPT（第二版）"},
    ).json()["data"]
    run_id = created["id"]
    started = client.post(f"{BASE}/build-runs/{run_id}/execute", headers=headers)
    print(f"  execute -> HTTP {started.status_code}  build={run_id[:8]}")

    for i in range(90):
        time.sleep(10)
        run = client.get(f"{BASE}/build-runs/{run_id}", headers=headers).json()["data"]
        if i % 3 == 0:
            print(f"    … {run['status']}  {run['steps_done']}/{run['steps_total']}")
        if run["status"] in ("COMPLETED", "FAILED", "CANCELLED", "LIMIT_REACHED", "WAITING_USER"):
            print(f"  终态 {run['status']}  {run['steps_done']}/{run['steps_total']}")
            break

    run = client.get(f"{BASE}/build-runs/{run_id}", headers=headers).json()["data"]
    second = [d for d in (run.get("deliverables") or []) if d["kind"] == "pptx"]
    if not second:
        print("  第二版没有产出 pptx —— 记忆没有改变行为，这是缺陷")
        return 1

    # ------------------------------------------------------------- compare --
    print()
    print("=" * 74)
    print("  两版对比")
    print("=" * 74)

    first_path = (
        Path(__file__).resolve().parents[1]
        / "samples"
        / "demo-output"
        / "答辩PPT-第一版-11页.pptx"
    )
    second_path = Path(second[0]["path"])

    if not first_path.is_file():
        print(f"  第一版文件不在了：{first_path}")
        return 1

    a = deck_facts(first_path)
    b = deck_facts(second_path)

    print(f"  {'':14}{'第一版':>14}{'第二版':>14}{'变化':>10}")
    for label, key, unit in [
        ("页数", "slides", ""),
        ("图表数", "images", ""),
        ("文字量", "text_chars", " 字"),
        ("文件大小", "size", " B"),
    ]:
        left, right = a[key], b[key]
        delta = right - left
        arrow = "↓" if delta < 0 else ("↑" if delta > 0 else "=")
        print(f"  {label:14}{left:>14,}{right:>14,}   {arrow}{abs(delta):,}{unit}")

    print()
    print(f"  第一版文件: {first_path.name}")
    print(f"  第二版文件: {second_path.name}")
    print()
    print("  第二版页标题:")
    for index, title in enumerate(b["titles"], 1):
        print(f"    {index:2}. {title[:56]}")

    changed = a["slides"] != b["slides"] or a["images"] != b["images"]
    print()
    if changed:
        print("  ★ 两版确实不同 —— 偏好参与了规划")
    else:
        print("  ✗ 两版相同 —— 记忆没有参与规划，这是缺陷")
    return 0 if changed else 1


if __name__ == "__main__":
    raise SystemExit(main())

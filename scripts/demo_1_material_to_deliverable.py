"""Demo 1 — from material to accepted artifact, and back into a preference.

The product's whole loop, run once, with every step recorded from the database and
the filesystem rather than from what the agent says about itself.

    1  the material it may use
    2  what it extracted from that material
    3  a goal stated in one sentence
    4  planning, execution, verification
    5  the artifact on disk, opened and read back
    6  rejection with a reason
    7  the preference that reason produced
    8  a second artifact, and the difference between the two

Run:  python scripts/demo_1_material_to_deliverable.py
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
POLL_SECONDS = 10
MAX_POLLS = 90


def line(title: str) -> None:
    print(f"\n{'=' * 74}\n  {title}\n{'=' * 74}")


def main() -> int:
    client = httpx.Client(timeout=180)
    token = client.post(
        f"{BASE}/auth/login", json={"identifier": "demo", "password": "demo1234"}
    ).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------ 1 ---
    line("① 它可以用哪些资料")
    bases = client.get(f"{BASE}/knowledge-bases", headers=headers).json()["data"]
    items = bases.get("items", bases if isinstance(bases, list) else [])
    print(f"  知识库 {len(items)} 个")
    documents = client.get(f"{BASE}/documents", headers=headers).json()["data"]
    docs = documents.get("items", [])
    for doc in docs[:6]:
        print(f"    · {doc['title']}  [{doc['status']}]")

    # ------------------------------------------------------------------ 2 ---
    line("② 它从资料里理解到了什么")
    if docs:
        detail = client.get(f"{BASE}/documents/{docs[0]['id']}", headers=headers).json()["data"]
        concepts = detail.get("concepts") or []
        print(f"  《{docs[0]['title']}》抽取概念 {len(concepts)} 个")
        for concept in concepts[:6]:
            print(f"    · {concept.get('name') if isinstance(concept, dict) else concept}")

    # ------------------------------------------------------------------ 3 ---
    line("③ 说一句目标")
    goal = "用我的资料做一份关于系统设计的答辩 PPT，明天课堂汇报，15 分钟"
    created = client.post(
        f"{BASE}/build-runs",
        headers=headers,
        json={"goal": goal, "deliverable_kind": "pptx", "title": "答辩 PPT（Demo 1）"},
    ).json()["data"]
    run_id = created["id"]
    print(f"  「{goal}」")
    print(f"  BuildRun {run_id[:8]}  状态 {created['status']}")

    # ------------------------------------------------------------------ 4 ---
    line("④ 规划 → 执行 → 校验")
    started = client.post(f"{BASE}/build-runs/{run_id}/execute", headers=headers)
    print(f"  execute -> HTTP {started.status_code}")
    if started.status_code != 200:
        print(f"  {started.text[:200]}")
        return 1

    for i in range(MAX_POLLS):
        time.sleep(POLL_SECONDS)
        run = client.get(f"{BASE}/build-runs/{run_id}", headers=headers).json()["data"]
        if i % 3 == 0:
            print(f"    … {run['status']}  {run['steps_done']}/{run['steps_total']}")
        if run["status"] in ("COMPLETED", "FAILED", "CANCELLED", "LIMIT_REACHED", "WAITING_USER"):
            print(f"  终态 {run['status']}  步骤 {run['steps_done']}/{run['steps_total']}")
            break

    run = client.get(f"{BASE}/build-runs/{run_id}", headers=headers).json()["data"]
    steps = (run.get("plan") or {}).get("steps") or []
    print(f"\n  计划 {len(steps)} 步：")
    for step in steps:
        print(f"    [{step.get('status')}] {str(step.get('description'))[:58]}")

    # ------------------------------------------------------------------ 5 ---
    line("⑤ 产出物：真实存在、可读回")
    deliverables = run.get("deliverables") or []
    if not deliverables:
        print("  （没有产出物）")
        print(f"  summary: {run.get('result_summary') or '（空）'}")
        return 1

    item = deliverables[0]
    path = Path(item["path"])
    print(f"  {item['title']}  {item['kind']}  {item['size_bytes']:,} bytes")
    print(f"  磁盘上存在: {path.is_file()}")
    print(f"  机器校验: {item['verified']}")
    for check in (item.get("verification") or {}).get("checks", []):
        print(f"    [{'OK' if check['ok'] else 'X'}] {check['name']}: {check['detail']}")

    if path.is_file() and item["kind"] == "pptx":
        from pptx import Presentation

        deck = Presentation(str(path))
        print(f"  PowerPoint 可打开: {len(deck.slides)} 页")
        for index, slide in enumerate(deck.slides, 1):
            texts = [
                shape.text_frame.text.strip().split("\n")[0]
                for shape in slide.shapes
                if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip()
            ]
            if texts:
                print(f"    第 {index} 页: {texts[0][:50]}")

    # ------------------------------------------------------------------ 6 ---
    line("⑥ 退回，并说明原因")
    empty = client.post(
        f"{BASE}/deliverables/{item['id']}/reject", headers=headers, json={"feedback": ""}
    )
    print(f"  空原因 -> HTTP {empty.status_code}（应为 422 —— 没有原因就没有学习）")

    reason = "图表太少，几乎全是文字；页数也偏多，15 分钟的汇报不需要这么多页"
    rejected = client.post(
        f"{BASE}/deliverables/{item['id']}/reject", headers=headers, json={"feedback": reason}
    )
    print(f"  带原因 -> HTTP {rejected.status_code}")
    print(f"  「{reason}」")

    # ------------------------------------------------------------------ 7 ---
    line("⑦ 它从这个原因里学到了什么")
    time.sleep(4)
    candidates = client.get(f"{BASE}/memory-candidates", headers=headers).json()["data"]
    memories = client.get(
        f"{BASE}/memories", headers=headers, params={"status": "active"}
    ).json()["data"]
    print(f"  待你确认的候选: {candidates['count']}")
    for candidate in candidates["candidates"][:3]:
        print(f"    · [{candidate['proposed_kind']}] {candidate['content'][:70]}")
        print(f"      理由：{candidate['reason'][:60]}  conf={candidate['confidence']}")
    print(f"  已生效的记忆: {memories['count']}")
    for memory in memories["memories"][:5]:
        print(f"    · [{memory['kind']}] {memory['content'][:68]}")

    # ------------------------------------------------------------------ 8 ---
    line("⑧ 首页汇总（真实数字）")
    summary = client.get(f"{BASE}/delivery/summary", headers=headers).json()["data"]
    for key, value in summary.items():
        print(f"  {key:26} {value}")

    print()
    print("=" * 74)
    print("  Demo 1 完成。上述每一项都来自数据库或磁盘，不是 agent 的自述。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

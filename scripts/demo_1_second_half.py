"""Demo 1, second half — the loop that makes the product worth having.

The first half proved it can produce a real deliverable (70,538 B / 11 slides, opened
and read back). This half is the claim that matters more:

    it produced something, the user said no and said why, and the *next* attempt was
    better because of it.

Everything here goes through the real HTTP API and the real database. Nothing is
read from what the agent says about itself: the deck is opened with python-pptx and
counted, and the run's status comes from its row.

    ① the first version, as it actually came out (11 slides, 0 charts, COMPLETED)
    ② the user rejects it, with a reason
    ③ what that reason became — a memory candidate, approved by the user
    ④ the same goal, run again
    ⑤ the two versions, side by side

Run:  python scripts/demo_1_second_half.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

BASE = "http://127.0.0.1:8000/api/v1"
POLL_SECONDS = 8
MAX_POLLS = 120
TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "LIMIT_REACHED", "WAITING_USER"}

#: The goal is passed through unchanged from the first half. Rewriting it for the
#: second attempt would mean the comparison is between two different tasks.
GOAL = (
    "用我导入的毕业设计资料，做一份 15 分钟的答辩 PPT，包含系统架构、数据库设计、AI 模块三部分。"
    "必须用 document_write 生成 .pptx 文件。"
)
REJECTION = "图表太少，文字堆得太满，页数也偏多，控制在 12 页以内"

#: The first version, as the previous round produced and verified it. Kept because
#: the whole point of the comparison is that it predates what was learned.
V1_PATH = ROOT / "samples" / "demo-output" / "答辩PPT-第一版-11页.pptx"
V1_EXPECTED = {"slides": 11, "images": 0, "size_bytes": 70538, "state": "COMPLETED"}


def line(title: str) -> None:
    print(f"\n{'=' * 78}\n  {title}\n{'=' * 78}")


def deck_facts(path: Path) -> dict[str, Any]:
    """Open the deck and count what is in it."""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    deck = Presentation(str(path))
    slides = len(deck.slides)
    images = sum(
        1
        for slide in deck.slides
        for shape in slide.shapes
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE
    )
    chars = sum(
        len(shape.text_frame.text)
        for slide in deck.slides
        for shape in slide.shapes
        if getattr(shape, "has_text_frame", False)
    )
    return {
        "path": str(path),
        "slides": slides,
        "images": images,
        "chars": chars,
        "size_bytes": path.stat().st_size,
    }


def login(client: httpx.Client) -> dict[str, str]:
    token = client.post(
        f"{BASE}/auth/login", json={"identifier": "demo", "password": "demo1234"}
    ).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def data(response: httpx.Response) -> Any:
    payload = response.json()
    if not payload.get("success", True):
        raise RuntimeError(f"HTTP {response.status_code}: {json.dumps(payload, ensure_ascii=False)[:300]}")
    return payload["data"]


def run_build(client: httpx.Client, headers: dict[str, str], *, title: str) -> dict[str, Any]:
    """Create a build run with the goal and drive it to a terminal state."""
    created = data(
        client.post(
            f"{BASE}/build-runs",
            headers=headers,
            json={"goal": GOAL, "deliverable_kind": "pptx", "title": title},
        )
    )
    run_id = created["id"]
    print(f"  BuildRun {run_id[:8]}  「{title}」  状态 {created['status']}")
    started = client.post(f"{BASE}/build-runs/{run_id}/execute", headers=headers)
    print(f"  execute → HTTP {started.status_code}")
    if started.status_code != 200:
        print(f"  {started.text[:300]}")
        return {"id": run_id, "status": "EXECUTE_FAILED"}

    for index in range(MAX_POLLS):
        time.sleep(POLL_SECONDS)
        run = data(client.get(f"{BASE}/build-runs/{run_id}", headers=headers))
        if index % 4 == 0:
            print(f"    … {run['status']}  {run['steps_done']}/{run['steps_total']}")
        if run["status"] in TERMINAL:
            print(f"  终态 {run['status']}  步骤 {run['steps_done']}/{run['steps_total']}")
            break
    run["deliverables"] = data(
        client.get(f"{BASE}/deliverables", headers=headers, params={"build_run_id": run_id})
    )["deliverables"]
    return run


def main() -> int:
    client = httpx.Client(timeout=180)
    headers = login(client)

    # ------------------------------------------------------------------ ① ---
    line("① 第一版：它上一次真的产出过什么")
    if not V1_PATH.is_file():
        print(f"  第一版样本不在：{V1_PATH}")
        return 1
    v1 = deck_facts(V1_PATH)
    print(f"  {V1_PATH.relative_to(ROOT)}")
    print(
        f"  {v1['slides']} 页 / {v1['images']} 图 / {v1['chars']} 字 / "
        f"{v1['size_bytes']:,} B"
    )
    print(f"  上一轮记录的终态：{V1_EXPECTED['state']}")
    for key, expected in V1_EXPECTED.items():
        if key == "state":
            continue
        got = v1[key]
        print(f"    [{'OK' if got == expected else 'X'}] {key}：{got}（上一轮记录 {expected}）")

    # ------------------------------------------------------------------ ② ---
    line("② 退回，并说明原因")
    pending = data(
        client.get(f"{BASE}/deliverables", headers=headers, params={"limit": 50})
    )["deliverables"]
    # The v1 row: the pptx deliverable whose size matches the deck above.
    v1_rows = [
        row
        for row in pending
        if row["kind"] == "pptx" and row["size_bytes"] == v1["size_bytes"]
    ]
    if not v1_rows:
        print("  没有找到第一版对应的产出物记录，无法走退回流程")
        return 1
    v1_row = v1_rows[0]
    print(f"  产出物 {v1_row['id'][:8]}  verified={v1_row['verified']}  accepted={v1_row['accepted']}")

    empty = client.post(
        f"{BASE}/deliverables/{v1_row['id']}/reject", headers=headers, json={"feedback": ""}
    )
    print(f"  空原因 → HTTP {empty.status_code}（应为 422：没有原因就没有学习）")
    rejected = client.post(
        f"{BASE}/deliverables/{v1_row['id']}/reject", headers=headers, json={"feedback": REJECTION}
    )
    print(f"  带原因 → HTTP {rejected.status_code}")
    print(f"  「{REJECTION}」")

    # ------------------------------------------------------------------ ③ ---
    line("③ 这个原因变成了什么")
    before = data(client.get(f"{BASE}/memory-candidates", headers=headers))
    print(f"  退回前的待确认候选：{before['count']} 条")
    known = {c["id"] for c in before["candidates"]}

    candidates: list[dict[str, Any]] = []
    for _ in range(8):
        time.sleep(4)
        after = data(client.get(f"{BASE}/memory-candidates", headers=headers))
        candidates = [c for c in after["candidates"] if c["id"] not in known]
        if candidates:
            break
    if candidates:
        print(f"  退回后新增候选 {len(candidates)} 条：")
        for candidate in candidates:
            print(f"    · [{candidate['proposed_kind']}] {candidate['content'][:80]}")
            print(f"      理由：{candidate['reason'][:70]}  置信度 {candidate['confidence']}")
    else:
        print("  没有新增候选 —— 判定器把它归为已有记忆的重复或噪声（下面如实记录）")

    approved: list[dict[str, Any]] = []
    for candidate in candidates:
        response = client.post(f"{BASE}/memory-candidates/{candidate['id']}/approve", headers=headers)
        if response.status_code == 200:
            approved.append(data(response))
            print(f"    [批准] {candidate['content'][:70]}")

    memories = data(client.get(f"{BASE}/memories", headers=headers, params={"status": "active"}))
    print(f"\n  当前生效的记忆 {memories['count']} 条：")
    for memory in memories["memories"][:6]:
        print(f"    · [{memory['kind']}] {memory['content'][:72]}")

    # ------------------------------------------------------------------ ④ ---
    line("④ 用同一个目标再做一份")
    v2_run = run_build(client, headers, title="答辩 PPT（Demo 1 第二版 · 第五轮）")
    print(f"  result_summary: {v2_run.get('result_summary') or '（空）'}")
    deliverables = v2_run.get("deliverables") or []
    if not deliverables:
        print("  没有登记产出物 —— 记录为 FAILED，不编造")
        (ROOT / ".runtime" / "demo1-second-half.json").write_text(
            json.dumps({"v1": v1, "v2_run": v2_run, "verdict": "FAILED"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return 1
    item = deliverables[0]
    print(f"  产出物 {item['title']}  {item['size_bytes']:,} B  verified={item['verified']}")
    for check in (item.get("verification") or {}).get("checks", []):
        print(f"    [{'OK' if check['ok'] else 'X'}] {check['name']}: {check['detail']}")

    v2_path = Path(item["path"])
    v2 = deck_facts(v2_path) if v2_path.is_file() else None
    if v2 is None:
        print(f"  文件不在磁盘上：{v2_path}")
        return 1

    plan = v2_run.get("plan") or {}
    print("\n  这次运行的任务硬约束：")
    print(f"    {json.dumps(plan.get('hard_constraints') or {}, ensure_ascii=False)}")
    print("  偏好处理结果：")
    for outcome in plan.get("preference_outcomes") or []:
        print(f"    [{outcome['verdict']}] 「{outcome['text'][:44]}」→ {outcome['detail'][:90]}")

    # ------------------------------------------------------------------ ⑤ ---
    line("⑤ 两版对比")
    rows = [
        ("页数", v1["slides"], v2["slides"], "10~14（15 分钟的硬约束）"),
        ("图表数", v1["images"], v2["images"], "> 0"),
        ("文字量", v1["chars"], v2["chars"], "—"),
        ("文件大小", v1["size_bytes"], v2["size_bytes"], "—"),
        ("终态", V1_EXPECTED["state"], v2_run["status"], "COMPLETED"),
    ]
    print(f"  {'':10}{'第一版':>14}{'第二版':>14}   判据")
    for label, first, second, criterion in rows:
        print(f"  {label:<10}{first!s:>14}{second!s:>14}   {criterion}")

    # The trade-off note lives on the AI run's response text — the run's own report,
    # not the delivery layer's one-line summary.
    answer = ""
    if v2_run.get("ai_run_id"):
        try:
            answer = data(
                client.get(f"{BASE}/ai-runs/{v2_run['ai_run_id']}", headers=headers)
            ).get("response_text", "")
        except (httpx.HTTPError, RuntimeError, KeyError) as exc:
            print(f"  （读取运行报告失败：{type(exc).__name__}: {exc}）")
    print("\n  取舍说明（来自运行自身的报告）：")
    for row in answer.split("\n"):
        if "取舍说明" in row or row.startswith("- "):
            print(f"    {row[:150]}")

    checks = {
        "第二版页数在 10~14（服从硬约束）": 10 <= v2["slides"] <= 14,
        "第二版图表数 > 0（偏好生效）": v2["images"] > 0,
        "第二版终态 COMPLETED": v2_run["status"] == "COMPLETED",
        "第二版产出物已登记且机器校验通过": bool(item["verified"]),
        "页数不比第一版更多（偏好方向正确）": v2["slides"] <= v1["slides"],
        "报告里有取舍说明": "取舍说明" in answer,
        "硬约束被记录在计划里": bool(plan.get("hard_constraints", {}).get("slide_min")),
        "偏好处理结果被记录在计划里": bool(plan.get("preference_outcomes")),
    }
    print()
    for name, ok in checks.items():
        print(f"    [{'OK' if ok else 'X'}] {name}")

    record = {
        "goal": GOAL,
        "rejection": REJECTION,
        "v1": {**v1, "state": V1_EXPECTED["state"], "deliverable_id": v1_row["id"]},
        "v2": {**v2, "state": v2_run["status"], "build_run_id": v2_run["id"], "deliverable": item},
        "v2_answer": answer,
        "v2_plan_constraints": plan.get("hard_constraints"),
        "v2_preference_outcomes": plan.get("preference_outcomes"),
        "candidates": candidates,
        "approved": approved,
        "checks": checks,
        "passed": all(checks.values()),
    }
    out = ROOT / ".runtime" / "demo1-second-half.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n  完整记录已写入 {out.relative_to(ROOT)}")
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

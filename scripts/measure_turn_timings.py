"""How long each outcome actually takes, measured against the running API.

The user's report:

    发「你好」                 1,533 ms    outcome=answer
    发「写个300字雨后小故事」   11,661 ms   outcome=start

    **这 11.7 秒里界面完全没动**

Two questions have to be answered with numbers rather than opinions:

  1. **What does each outcome cost?** The optimistic update makes the wait visible, but
     the wait is still there, and knowing which path is slow is what decides whether it
     can be shortened.
  2. **Does `start` still answer?** The 11.7-second turn came back `outcome=start` *and*
     carried the finished story in `reply` — the same text the user was about to receive
     as a file, so they waited for it twice. The prompt and `MAX_START_REPLY` exist to
     stop that; this prints the reply length and the outcome for every case so the claim
     can be checked instead of believed.

    .venv/Scripts/python.exe scripts/measure_turn_timings.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000/api/v1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

#: (label, sentence, what it should come back as)
CASES: list[tuple[str, str, str]] = [
    ("打招呼", "你好", "answer"),
    ("问知识", "解释一下什么是死锁", "answer"),
    ("要一段文字（没说要文件）", "写个 300 字雨后小故事", "answer"),
    ("要澄清", "帮我做一个 java 的指引指南，教教我怎么学习", "clarify"),
    ("要文件", "写个 300 字雨后小故事，做成 Word 文档给我", "start"),
]


def request(path: str, *, method: str = "GET", body: dict | None = None, token: str = "") -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))


def login() -> str:
    payload = request("/auth/login", method="POST", body={"identifier": "demo", "password": "demo1234"})
    return payload["data"]["access_token"]


def main() -> int:
    token = login()
    print(f"{'场景':<26} {'结果':<9} {'耗时':>9}  {'回复字数':>8}  {'建了 run':>8}  判定")
    print("-" * 92)
    worst = 0.0
    rows: list[tuple[str, str, float, int, bool]] = []

    for label, sentence, expected in CASES:
        conversation = request("/conversations", method="POST", body={}, token=token)
        conversation_id = conversation["data"]["id"]
        started = time.perf_counter()
        turn = request(
            f"/conversations/{conversation_id}/messages",
            method="POST",
            body={"content": sentence},
            token=token,
        )["data"]
        elapsed = time.perf_counter() - started
        worst = max(worst, elapsed)

        outcome = turn["outcome"]
        reply = (turn.get("message") or {}).get("content") or ""
        made_run = bool(turn.get("build_run_id"))
        ok = outcome == expected
        rows.append((label, outcome, elapsed, len(reply), made_run))
        print(
            f"{label:<26} {outcome:<9} {elapsed:>8.2f}s  {len(reply):>8}  "
            f"{str(made_run):>8}  {'OK' if ok else f'!! 期望 {expected}'}"
        )

    print("\n=== 判定 ===")
    by_outcome: dict[str, list[float]] = {}
    for _label, outcome, elapsed, _chars, _run in rows:
        by_outcome.setdefault(outcome, []).append(elapsed)
    for outcome, values in sorted(by_outcome.items()):
        print(f"  {outcome:<9} n={len(values)}  最快 {min(values):.2f}s  最慢 {max(values):.2f}s")

    started = [r for r in rows if r[1] == "start"]
    answers = [r for r in rows if r[1] == "answer"]
    problems = []
    if started and any(chars > 120 for *_, chars, _ in started):
        problems.append("start 的回复正文超过 120 字，正文又进了对话")
    if started and any(run is False for *_, run in started):
        problems.append("start 却没有建 run")
    if answers and any(run for *_, run in answers):
        problems.append("answer 却建了 run（两个结果同时为真）")

    for problem in problems:
        print(f"  [FAIL] {problem}")
    if not problems:
        print("  [PASS] 三种结果互斥：answer 不建 run，start 不在对话里写正文")
    print(f"\n最慢的一次 {worst:.2f}s")
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as exc:
        print(f"API 没起来？{exc}")
        raise SystemExit(2) from exc

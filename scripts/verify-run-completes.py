"""Re-run a real goal end to end and report what the run actually ended as.

This exists because the bug it checks for could not be found by reading code. The
run looked fine everywhere except its final state: every step DONE, the artifact
on disk and readable, and the verdict FAILED. The only way to see it is to run a
real task against the real API and read the result.

    .venv/Scripts/python.exe scripts/verify-run-completes.py
    .venv/Scripts/python.exe scripts/verify-run-completes.py --goal "..." --timeout 900

Exits 0 when the run reaches a terminal state and every deliverable it produced
passes its own file-level checks. Exits 1 otherwise, and says which part failed —
"the run ended PARTIAL" and "the file was unreadable" are different findings and
the output keeps them apart.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000/api/v1"

#: The goal the user actually used. Kept verbatim: it is the case that failed, and
#: a paraphrased version would not be the same test.
DEFAULT_GOAL = "java基础教程，只要指引目录，不要具体内容"

TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "LIMIT_REACHED"}


def login(client: httpx.Client, base: str) -> str:
    response = client.post(
        f"{base}/auth/login", json={"identifier": "demo", "password": "demo1234"}
    )
    response.raise_for_status()
    return response.json()["data"]["access_token"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal", default=DEFAULT_GOAL)
    parser.add_argument("--kind", default="pptx")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--base", default=BASE)
    args = parser.parse_args()

    base = args.base

    with httpx.Client(timeout=60.0) as client:
        token = login(client, base)
        headers = {"Authorization": f"Bearer {token}"}

        health = client.get(f"{base}/health/deps", headers=headers).json()["data"]
        llm = health.get("ai", {}).get("llm", {})
        print(f"模型：kind={llm.get('kind')} model={llm.get('model')} degraded={llm.get('degraded')}")
        if llm.get("degraded"):
            print("模型不可用，这次运行不能说明问题（会走离线启发式）。")
            return 1

        created = client.post(
            f"{base}/build-runs",
            headers=headers,
            json={"goal": args.goal, "deliverable_kind": args.kind},
        )
        created.raise_for_status()
        run = created.json()["data"]
        run_id = run["id"]
        print(f"创建：{run_id} status={run['status']}")

        started = client.post(f"{base}/build-runs/{run_id}/execute", headers=headers)
        started.raise_for_status()
        print(f"启动：{started.json()['data']}")

        deadline = time.time() + args.timeout
        status = ""
        while time.time() < deadline:
            time.sleep(5)
            payload = client.get(f"{base}/build-runs/{run_id}", headers=headers).json()["data"]
            status = payload["status"]
            print(
                f"  {time.strftime('%H:%M:%S')} status={status} "
                f"steps={payload['steps_done']}/{payload['steps_total']}"
            )
            if status in TERMINAL:
                break

        final = client.get(f"{base}/build-runs/{run_id}", headers=headers).json()["data"]
        print("\n" + "=" * 72)
        print(f"终态：{final['status']}")
        print(f"步骤：{final['steps_done']}/{final['steps_total']}")
        print(f"摘要：{final.get('result_summary') or '(空)'}")
        if final.get("error"):
            print(f"错误：{final['error']}")

        deliverables = final.get("deliverables") or []
        print(f"\n产出物 {len(deliverables)} 个：")
        for item in deliverables:
            verification = item.get("verification") or {}
            run_state = verification.get("run_state") or {}
            print(
                f"  - {item['title']} ({item['kind']}, {item['size_bytes']:,} B) "
                f"verified={item['verified']} accepted={item['accepted']}"
            )
            for check in verification.get("checks") or []:
                print(f"      [{'ok' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}")
            if run_state:
                print(f"      运行：{run_state.get('state')} ok={run_state.get('ok')}")
                print(f"            {run_state.get('detail')}")

        # --- verdict, with the two findings kept apart -------------------------
        problems: list[str] = []
        if final["status"] not in TERMINAL:
            problems.append(f"运行没有在 {args.timeout}s 内到达终态（当前 {final['status']}）")
        if not deliverables:
            problems.append("没有登记任何产出物")
        for item in deliverables:
            if not item["verified"]:
                failed = [
                    c["name"]
                    for c in (item.get("verification") or {}).get("checks") or []
                    if not c["ok"]
                ]
                problems.append(
                    f"产出物「{item['title']}」没有通过文件级校验（未通过的检查：{failed or '无'}）"
                )

        print("=" * 72)
        if problems:
            for problem in problems:
                print(f"[FAIL] {problem}")
            print(f"\n运行终态 = {final['status']}")
            return 1

        print(f"[PASS] 运行到达终态 {final['status']}，{len(deliverables)} 个产出物全部通过文件级校验")
        if final["status"] != "COMPLETED":
            print(f"       注意：终态不是 COMPLETED，而是 {final['status']} —— 上面写了原因。")
        return 0


if __name__ == "__main__":
    sys.exit(main())

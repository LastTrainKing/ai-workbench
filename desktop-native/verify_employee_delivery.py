"""Hire a real employee, give it a real job, and check the file it claims to have made.

This is the acceptance test the brief asks for in §12 — 用户提出需求 → AI 员工执行 →
查看成果 — driven through `ServiceBridge`, which is the same path the window uses, against
the real local stack and a real model. No mocks: the point is to find out whether the
chain holds when a language model is actually in it.

The claim under test is deliberately narrow and checkable:

    the run says COMPLETED  ⇒  a file exists at the path it recorded,
                              the file is non-empty,
                              the file is readable,
                              and it shows up in the deliverables list

Every one of those has been false at some point in this project's history, and the whole
grievance in the brief ("输出 md，但生成后不知道在哪里") is that the last three were not
checked by anything.

Run:  python desktop-native/verify_employee_delivery.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

os.environ.setdefault("AI_WORKBENCH_DATA_DIR", r"C:\Users\user\Desktop\ai-workbench\.runtime")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/aiworkbench")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "desktop-native"))

from PySide6.QtCore import QCoreApplication

app = QCoreApplication(sys.argv[:1] + ["delivery-check"])

from awbnative import resources

resources.configure()
from awbnative.bridge import ServiceBridge

RESULTS: list[tuple[str, bool, str]] = []


def call(bridge: ServiceBridge, name: str, *args: object, timeout: float = 120.0) -> object:
    """Invoke one slot and block until the runner answers."""
    box: dict[str, object] = {}

    def on_done(request_id: str, value: str) -> None:
        box["value"] = value

    def on_fail(request_id: str, message: str, code: str) -> None:
        box["error"] = f"{message} ({code})"

    bridge.completed.connect(on_done)
    bridge.failed.connect(on_fail)
    request_id = f"e2e-{name}-{int(time.time() * 1000)}"
    getattr(bridge, name)(request_id, *args)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if "value" in box or "error" in box:
            break
        time.sleep(0.05)
    bridge.completed.disconnect(on_done)
    bridge.failed.disconnect(on_fail)
    if "error" in box:
        raise RuntimeError(str(box["error"]))
    if "value" not in box:
        raise TimeoutError(f"{name} 在 {timeout}s 内没有返回")
    raw = box["value"]
    return json.loads(raw) if isinstance(raw, str) and raw[:1] in "[{" else raw


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)


TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "LIMIT_REACHED"}

GOAL = (
    "写一份简短的工作报告，主题是「本地优先（local-first）软件为什么对个人用户重要」。"
    "要求：三个要点，每个要点两到三句话，最后给一句总结。"
    "把成稿保存为 Markdown 文件，文件名用 report-local-first.md。"
)


def main() -> int:
    bridge = ServiceBridge()
    bridge.startEnvironment()
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        app.processEvents()
        if bridge.startupState in ("ready", "degraded"):
            break
        time.sleep(0.1)
    print(f"  启动状态: {bridge.startupState}", flush=True)
    check("本地服务就绪", bridge.startupState in ("ready", "degraded"), bridge.startupState)

    run_tag = uuid.uuid4().hex[:6]
    employee_name = f"端到端测试员-{run_tag}"

    print("\n=== 1. 雇佣员工 ===", flush=True)
    employee = call(
        bridge,
        "createEmployee",
        json.dumps({"role_id": "assistant", "name": employee_name}),
    )
    employee_id = str(employee["id"])
    print(f"  员工: {employee['name']}  岗位={employee.get('role_name') or employee.get('role_id')}", flush=True)
    print(f"  工具数: {len(employee.get('skills') or [])}", flush=True)
    check("员工已雇佣", bool(employee_id), employee_name)

    print("\n=== 2. 派发任务（真实模型）===", flush=True)
    print(f"  目标: {GOAL[:60]}…", flush=True)
    before = call(bridge, "loadDeliverables")
    before_ids = {str(d.get("id")) for d in before.get("items", [])}

    started = call(bridge, "startBuildRun", GOAL, employee_id)
    build_run_id = str(started.get("build_run_id") or started.get("id"))
    print(f"  运行: {build_run_id}", flush=True)
    check("任务已下发", bool(build_run_id), build_run_id)

    print("\n=== 3. 观察执行 ===", flush=True)
    t0 = time.monotonic()
    seen: list[str] = []
    run: dict = {}
    while time.monotonic() - t0 < 1500:
        app.processEvents()
        runs = call(bridge, "loadBuildRuns")
        match = next(
            (r for r in runs.get("items", []) if str(r.get("id")) == build_run_id), None
        )
        if match:
            run = match
            status = str(match.get("status"))
            if not seen or seen[-1] != status:
                seen.append(status)
                print(f"  [{time.monotonic() - t0:6.1f}s] {status}", flush=True)
            if status in TERMINAL:
                break
        time.sleep(5)
    elapsed = time.monotonic() - t0
    status = str(run.get("status"))
    print(f"  终态: {status}（{elapsed:.0f} 秒）", flush=True)
    print(f"  状态序列: {' → '.join(seen)}", flush=True)
    check("运行到达终态", status in TERMINAL, status)
    check("运行成功", status == "COMPLETED", status)

    print("\n=== 4. 成果是否真实存在 ===", flush=True)
    after = call(bridge, "loadDeliverables")
    items = after.get("items", [])
    new_items = [d for d in items if str(d.get("id")) not in before_ids]
    print(f"  新增交付物: {len(new_items)} 个（原有 {len(before_ids)} 个）", flush=True)
    check("运行产出了交付物", len(new_items) > 0, f"{len(new_items)} 个")

    for item in new_items:
        path = str(item.get("path") or "")
        print(f"\n  --- {item.get('title') or item.get('name') or '(无标题)'} ---", flush=True)
        print(f"      路径: {path}", flush=True)
        print(f"      类型: {item.get('kind')}   大小: {item.get('size_bytes')} 字节", flush=True)
        print(f"      验证: {item.get('verified')}   版本: {item.get('version')}", flush=True)

        exists = bool(path) and Path(path).is_file()
        check(f"文件真实存在 ({Path(path).name if path else '?'})", exists, path)
        if exists:
            size = Path(path).stat().st_size
            check("文件非空", size > 0, f"{size} 字节")
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            check("文件可读且是正文", len(text.strip()) > 50, f"{len(text)} 字符")
            print(f"      开头: {text.strip().splitlines()[0][:70] if text.strip() else '(空)'}", flush=True)

        detail = call(bridge, "loadDeliverableDetail", str(item.get("id")))
        verification = detail.get("verification") or detail.get("checks") or []
        print(f"      验证证据: {json.dumps(verification, ensure_ascii=False)[:200]}", flush=True)
        chain = detail.get("versions") or detail.get("version_chain") or []
        check("交付物详情可读", isinstance(detail, dict) and bool(detail), f"版本链 {len(chain)} 项")

    print("\n=== 5. 运行记录说了什么 ===", flush=True)
    for key in ("error", "error_message", "summary", "title", "deliverable_kind"):
        if run.get(key):
            print(f"      {key}: {str(run[key])[:160]}", flush=True)
    print(f"      全部字段: {', '.join(sorted(run.keys()))}", flush=True)

    print("\n=== 6. 员工状态 ===", flush=True)
    roster = call(bridge, "loadEmployees")
    mine = next(
        (e for e in roster.get("items", []) if str(e.get("id")) == employee_id), None
    )
    if mine:
        print(f"      状态: {mine.get('status')}   运行次数: {mine.get('run_count')}", flush=True)
        check("员工状态不是卡在「工作中」", mine.get("status") != "working", str(mine.get("status")))

    print("\n" + "=" * 62)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for label, ok, detail in RESULTS:
        if not ok:
            print(f"  [FAIL] {label} — {detail}")
    print(f"结果：{passed}/{len(RESULTS)} 通过")
    print("=" * 62)
    bridge.shutdown()
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())

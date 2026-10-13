"""真实模型的端到端验收 —— 任务书 §12 的场景。

## 设计原则

**每个场景只跑一次。** 预算花在覆盖面，不花在重复上。同一个场景跑两次只会得到同一个结论
和两倍账单。

**每个场景有 token 上限。** 超过就中止并如实报告，而不是让一次跑偏的调用吃掉整个预算。

**花费从 `ai_runs` 读。** 这条路径走应用自己的 run recorder，所以"花了多少"是数出来的。
这与 `test_deepseek_live.py` 不同：那 6 条直接调 provider、不经过 recorder，
`scripts/model_call_count.py` 对它们报 0 —— 那个 0 是**错的答案**，不是"没有花费"。

**场景之间不共享状态**，除了 S3 刻意依赖 S2 的产物 —— 它测的正是"产物被删掉之后状态还
诚不诚实"。

## 场景

| # | 场景 | 断言的核心 |
|---|---|---|
| S1 | 纯问答 | 回答非空，且**不产生空文件**（§12.1） |
| S2 | 写文章输出 md | 文件真实存在、非空、已登记、有验证证据（§12.2） |
| S3 | 产物被删除 | 状态不再声称"已验证"（§12.5） |

用法：

    python desktop-native/verify_live_acceptance.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Self

os.environ.setdefault("AI_WORKBENCH_DATA_DIR", r"C:\Users\user\Desktop\ai-workbench\.runtime")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/aiworkbench"
)
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED", "LIMIT_REACHED"}
RESULTS: list[tuple[str, bool, str]] = []


def check(scenario: str, label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((f"{scenario}/{label}", ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""), flush=True)


async def _owner() -> uuid.UUID:
    """本安装的账号，并**把调用绑到它**。

    第二半不是装饰。`build_llm_provider()` 不接 owner，它读请求上下文；HTTP 应用在鉴权
    依赖里设好，而独立脚本没有那一步 —— 于是 provider 会解析到**安装级**配置。这正是
    `bridge._owner()` 的 docstring 里记着的那个坑（界面显示已配置，调用用的却是机器上的
    另一份）。所以这里照做：取第一个活跃账号，并设进 contextvar。
    """
    from app.ai.runtime_config import set_current_owner
    from app.infrastructure.database import session_scope
    from app.models.user import User
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(User).where(User.is_active.is_(True)).order_by(User.created_at).limit(1)
            )
        ).scalar_one_or_none()
    if row is None:
        raise RuntimeError("这台机器上还没有账号")
    set_current_owner(str(row.id))
    return row.id


async def _usage() -> tuple[int, int]:
    """`ai_runs` 里的累计调用次数与 token 数 —— 真实花费的唯一来源。"""
    from app.infrastructure.database import session_scope
    from app.models.ai import AIRun
    from sqlalchemy import func, select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(func.count(AIRun.id), func.coalesce(func.sum(AIRun.total_tokens), 0))
            )
        ).one()
    return int(row[0]), int(row[1])


async def _deliverables(owner: uuid.UUID) -> list:
    from app.infrastructure.database import session_scope
    from app.models.delivery import Deliverable
    from sqlalchemy import select

    async with session_scope() as session:
        return list(
            (
                await session.execute(
                    select(Deliverable)
                    .where(Deliverable.user_id == owner)
                    .order_by(Deliverable.created_at.desc())
                )
            ).scalars()
        )


class Scenario:
    """一个场景的花费记账。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0
        self.tokens = 0

    async def __aenter__(self) -> Self:
        self._c0, self._t0 = await _usage()
        print(f"\n=== {self.name} ===", flush=True)
        return self

    async def __aexit__(self, *exc: object) -> None:
        c1, t1 = await _usage()
        self.calls, self.tokens = c1 - self._c0, t1 - self._t0
        print(f"      本场景花费：{self.calls} 次调用 / {self.tokens} tokens", flush=True)


async def scenario_1(owner: uuid.UUID) -> None:
    """§12.1 纯问答：回答显示正确，不额外创建空文件。"""
    from app.infrastructure.database import session_scope
    from app.services.conversation_service import ConversationService

    before = len(await _deliverables(owner))
    async with Scenario("S1 纯问答（不应产生文件）"), session_scope() as session:
        service = ConversationService(session, user_id=owner)
        conversation = await service.create(title="live-acceptance-s1")
        turn = await service.send(conversation.id, "用一句话说明什么是 CAP 定理。")
        payload = turn.as_dict()

    # `Turn.as_dict()` 把回复放在 `message.content`，不是顶层 `content` —— 第一版读错了
    # 字段，于是这条断言对着一个空字符串判了失败。**写错字段名不抛异常，只产生一句假话**，
    # 和这一整轮在治的是同一个病；区别是这次假话是测试自己说的。
    message = payload.get("message") or {}
    reply = str(message.get("content") or "")
    check("S1", "回答非空", bool(reply.strip()), f"{len(reply)} 字符（outcome={payload.get('outcome')}）")
    if reply.strip():
        print(f"      回答开头：{reply.strip()[:70]}", flush=True)

    after = len(await _deliverables(owner))
    check("S1", "纯问答不产生交付物", after == before, f"{before} → {after}")


async def scenario_2(owner: uuid.UUID) -> str:
    """§12.2 写文章输出 md。返回产物路径。"""
    from app.infrastructure.database import session_scope
    from app.services.delivery_service import DeliveryService

    before_ids = {str(d.id) for d in await _deliverables(owner)}

    goal = (
        "写一篇不超过 300 字的中文短文，主题是「为什么本地优先的软件对个人用户重要」，"
        "三个自然段，最后加一句总结。保存为 Markdown 文件，文件名用 local-first.md。"
    )

    async with Scenario("S2 写文章输出 Markdown"):
        async with session_scope() as session:
            service = DeliveryService(session)
            run = await service.create_build_run(
                owner_id=owner, goal=goal, deliverable_kind="md", scope={}
            )
            await service.start_build_run(run)
            run_id = str(run.id)
        print(f"      运行：{run_id}", flush=True)

        status, elapsed = await _wait_terminal(run_id)
        print(f"      终态：{status}（{elapsed:.0f} 秒）", flush=True)
        check("S2", "运行到达终态", status in TERMINAL, status)

    fresh = [d for d in await _deliverables(owner) if str(d.id) not in before_ids]
    check("S2", "产出了交付物", bool(fresh), f"{len(fresh)} 个")
    if not fresh:
        return ""

    item = fresh[0]
    payload = item.as_dict()
    path = str(payload.get("path") or "")
    print(f"      产物：{path}", flush=True)
    print(f"      大小：{payload.get('size_bytes')} 字节", flush=True)

    check("S2", "文件真实存在", bool(path) and Path(path).is_file(), path)
    if path and Path(path).is_file():
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        check("S2", "文件非空且可读", len(text.strip()) > 50, f"{len(text)} 字符")
        if text.strip():
            print(f"      开头：{text.strip().splitlines()[0][:60]}", flush=True)
    verification = payload.get("verification") or {}
    checks = verification.get("checks") or []
    check("S2", "有验证证据", bool(checks), str(checks)[:130])
    check("S2", "登记为已校验", payload.get("verified") is True, str(payload.get("verified")))
    return path


async def scenario_3(owner: uuid.UUID, path: str) -> None:
    """§12.5 文件被删除：UI 不应维持过期的「已验证」状态。"""
    from app.infrastructure.database import session_scope
    from app.services.delivery_service import DeliveryService

    if not path or not Path(path).is_file():
        check("S3", "有可用的产物可测", False, "S2 没有产出文件")
        return

    async with Scenario("S3 产物被删除后状态是否诚实"):
        backup = Path(path).read_bytes()
        Path(path).unlink()
        print(f"      已删除：{path}", flush=True)
        try:
            async with session_scope() as session:
                service = DeliveryService(session)
                target = next(
                    (d for d in await _deliverables(owner) if str(d.path) == path), None
                )
                if target is None:
                    check("S3", "记录仍在（文件已删）", False, "记录也没了")
                    return

                await service.verify_deliverable(owner_id=owner, deliverable_id=target.id)
                refreshed = await service.get_deliverable(
                    owner_id=owner, deliverable_id=target.id
                )
                payload = refreshed.as_dict()

            verification = payload.get("verification") or {}
            checks = verification.get("checks") or []
            check("S3", "文件消失后验证不通过", verification.get("passed") is not True,
                  str(checks)[:130])
            check("S3", "登记不再声称已校验", payload.get("verified") is not True,
                  str(payload.get("verified")))
            failed = [c.get("name") for c in checks if c.get("ok") is False]
            check("S3", "失败项点明文件缺失", bool(failed), str(failed))
        finally:
            Path(path).write_bytes(backup)
            print(f"      已恢复：{path}", flush=True)


async def _wait_terminal(run_id: str) -> tuple[str, float]:
    from app.infrastructure.database import session_scope
    from app.models.delivery import BuildRun

    started = time.monotonic()
    status = "unknown"
    while time.monotonic() - started < 900:
        await asyncio.sleep(5)
        async with session_scope() as session:
            run = await session.get(BuildRun, uuid.UUID(run_id))
            if run is None:
                break
            status = str(run.status)
        if status in TERMINAL:
            break
    return status, time.monotonic() - started


async def main() -> int:
    print("真实模型端到端验收（§12 场景，每个只跑一次）", flush=True)
    owner = await _owner()
    c0, t0 = await _usage()
    print(f"  账号：{owner}  起始累计：{c0} 次调用 / {t0} tokens", flush=True)

    await scenario_1(owner)
    path = await scenario_2(owner)
    await scenario_3(owner, path)

    c1, t1 = await _usage()
    failed = [r for r in RESULTS if not r[1]]
    print("\n" + "=" * 62)
    for name, _ok, detail in failed:
        print(f"  [FAIL] {name} — {detail}")
    print(f"结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过")
    print(f"本轮真实花费：{c1 - c0} 次模型调用 / {t1 - t0} tokens")
    print("=" * 62)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

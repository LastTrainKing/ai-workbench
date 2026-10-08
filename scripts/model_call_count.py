"""这一轮测试花掉了多少次模型调用、多少 token。

用户的原话：

    他搞这么多测试任务干嘛，老子的钱不是钱啊。

    跑完统计：这一轮测试一共花掉多少 token / 多少次模型调用，写进报告里。

一条命令给出答案，而且**答案是量出来的，不是估的**。数据库里每一次模型调用都有一行
`ai_runs`，所以"这一轮跑了几次"就是"这段时间里多了几行"。

    .venv/Scripts/python.exe scripts/model_call_count.py               # 今天
    .venv/Scripts/python.exe scripts/model_call_count.py --baseline    # 记一个起点
    .venv/Scripts/python.exe scripts/model_call_count.py               # 之后再跑，给差值

`--baseline` 把当前的时间戳写进 `build/model-call-baseline.json`，之后不带参数跑就是
"从那个点到现在"。

## 为什么用 ai_runs 而不是打日志数

日志会漏：后台 worker、失败重试、浏览器测试里的一次真对话，都会各自打各自的日志行。
`ai_runs` 是**所有**模型调用都要落的那张表——它是审计轨迹，不是调试输出。数它才是数
全部。缺 Key 的那一轮，这个数字应该是 **0**。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

BASELINE = ROOT / "build" / "model-call-baseline.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="统计模型调用次数与 token")
    parser.add_argument("--baseline", action="store_true", help="把现在记为起点")
    parser.add_argument("--hours", type=float, default=0.0, help="不设起点时，看最近 N 小时（默认今天）")
    args = parser.parse_args()

    import asyncio

    from sqlalchemy import func, select

    from app.infrastructure.database import session_scope
    from app.models.ai import AIRun

    if args.baseline:
        now = datetime.now(UTC)
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({"at": now.isoformat()}, indent=2), encoding="utf-8")
        print(f"  起点已记下：{now.isoformat()}")
        print(f"  {BASELINE}")
        return 0

    since: datetime
    label: str
    if BASELINE.is_file():
        since = datetime.fromisoformat(json.loads(BASELINE.read_text(encoding="utf-8"))["at"])
        label = f"从起点 {since.astimezone().strftime('%H:%M:%S')} 到现在"
    elif args.hours:
        since = datetime.now(UTC) - timedelta(hours=args.hours)
        label = f"最近 {args.hours} 小时"
    else:
        local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        since = local_midnight.astimezone(UTC)
        label = "今天（本地时间 0 点起）"

    async def collect() -> dict[str, object]:
        async with session_scope() as session:
            runs = (
                await session.execute(
                    select(AIRun).where(AIRun.created_at >= since).order_by(AIRun.created_at)
                )
            ).scalars()
            rows = list(runs)
            total_runs = (await session.execute(select(func.count()).select_from(AIRun))).scalar_one()
        return {"rows": rows, "total": int(total_runs)}

    data = asyncio.run(collect())
    rows = data["rows"]

    print("=" * 74)
    print("  模型调用统计")
    print("=" * 74)
    print(f"  范围      ：{label}")
    print(f"  调用次数  ：{len(rows)}")
    print(f"  历史总数  ：{data['total']} 行 ai_runs")

    tokens_in = 0
    tokens_out = 0
    for run in rows:
        metrics = run.metrics if isinstance(getattr(run, "metrics", None), dict) else {}
        tokens_in += int(metrics.get("prompt_tokens") or metrics.get("input_tokens") or 0)
        tokens_out += int(metrics.get("completion_tokens") or metrics.get("output_tokens") or 0)

    if rows:
        print(f"  输入 token：{tokens_in:,}")
        print(f"  输出 token：{tokens_out:,}")
        print(f"  合计      ：{tokens_in + tokens_out:,}")
        print("\n  明细：")
        for run in rows:
            when = run.created_at.astimezone().strftime("%m-%d %H:%M:%S")
            print(f"    {when}  {run.run_type:<16} {run.provider}/{run.model or '(无)'}  {run.status}")
    else:
        print("  **这一轮没有发生任何模型调用。**")

    print()
    if len(rows) == 0:
        print("  PASS：0 次调用")
        return 0
    print("  注意：这一轮要求是 0 次。上面这些如果不是 0，说明有测试真的打了模型。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

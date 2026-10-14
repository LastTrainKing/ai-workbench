"""Measure the "are these two the same statement?" signal before picking a threshold.

The rule this exists to enforce, from `RETRIEVAL.md` and paid for twice already:
**先测分布，再选阈值.** Two previous attempts to pick a relevance threshold by
reasoning about it failed — once with a score normalised over the result set, once
with a Chinese bigram overlap — and both were only settled by printing the
distribution and looking at it. The same trap is here: "0.82" for memory dedup was
chosen the same way, and it does not group the statements that are obviously the same
thing.

What it does:

  1. loads the real memories and candidates from the database,
  2. adds hand-written pairs that are *known* to be the same thing or not,
  3. scores every pair under both candidate metrics,
  4. prints the two distributions sorted, so the gap (or the overlap) is visible,
  5. evaluates each candidate threshold by false positives and false negatives.

The two metrics:

  char   `evaluator._similarity` — character-set overlap. Cheap, and what dedup uses
         today.
  bigram `lifecycle.similarity` — Jaccard over CJK bigrams + latin words. What the
         merge step uses.

    .venv/Scripts/python.exe scripts/sample_memory_similarity.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.ai.memory.evaluator import _similarity as char_similarity  # noqa: E402
from app.ai.memory.lifecycle import similarity as bigram_similarity  # noqa: E402
from app.infrastructure.database import session_scope  # noqa: E402
from app.models.context import MemoryCandidate, MemoryEntry  # noqa: E402

#: Pairs written by hand, with the answer known. The real data supplies the hard
#: cases; these supply the ones the real data happens not to contain, and each one is
#: a shape the dedup has to get right.
#:
#: (a, b, same?)
LABELLED: list[tuple[str, str, bool]] = [
    # --- the same thing, said differently. This is the set that must collapse. ---
    (
        "用户偏好演示文稿图表更多、页数更少，并会以此作为交付质量标准。",
        "用户在审阅交付物时以「图表是否充足、页数是否精简」为质量标准：图表太少或页数偏多的产出会被退回。",
        True,
    ),
    (
        "用户偏好演示文稿图表更多、页数更少，并会以此作为交付质量标准。",
        "用户以「图表充足、页数精简」作为演示文稿类交付物的验收标准，图表太少或页数偏多的产出会被退回。",
        True,
    ),
    (
        "用户偏好图表充足、文字精简、页数少的演示文稿，PPT 类交付物页数应控制在 12 页以内，图表太少、文字过密或页数偏多会被退回。",
        "用户在验收演示文稿时会因「图表太少、页数偏多」而直接退回交付物，图表充足与页数精简是其硬性质量标准。",
        True,
    ),
    (
        "用户偏好演示文稿图表更多、页数更少，并会以此作为交付质量标准。",
        "用户在验收演示文稿（如答辩PPT）时要求图表充足、页数精简，图表太少或页数偏多的产出会被退回。",
        True,
    ),
    ("用户以后所有项目都使用 Vue，不再使用 React。", "用户以后所有项目都用 Vue，不用 React 了。", True),
    ("用户是计算机专业大三学生。", "用户在读计算机专业，大三。", True),
    ("用户要求报告至少包含背景和结论两部分。", "用户要求报告必须有背景和结论两部分。", True),
    # --- different things. Each of these shares vocabulary with something above. ---
    (
        "用户偏好演示文稿图表更多、页数更少，并会以此作为交付质量标准。",
        "用户在验收演示文稿时要求封面包含答辩人和指导教师信息，且正文字号需足够大以确保投影时后排可读。",
        False,
    ),
    (
        "用户偏好演示文稿图表更多、页数更少，并会以此作为交付质量标准。",
        "用户在验收报告类交付物时要求内容完整：报告至少应包含背景和结论两部分，内容过简会被退回。",
        False,
    ),
    ("用户以后所有项目都使用 Vue，不再使用 React。", "用户是计算机专业大三学生。", False),
    ("用户是计算机专业大三学生。", "用户在做毕业设计。", False),
    ("用户喜欢用 Python 写脚本。", "用户喜欢用 Java 写脚本。", False),
    ("用户要求报告至少包含背景和结论两部分。", "用户要求报告页数控制在 12 页以内。", False),
    ("这是一条浏览器测试写入的记忆", "用户在验收演示文稿时要求封面包含答辩人和指导教师信息。", False),
]


async def load_real() -> tuple[list[str], list[str]]:
    async with session_scope() as session:
        entries = list(
            (
                await session.execute(
                    select(MemoryEntry).where(MemoryEntry.status == "active")
                )
            ).scalars()
        )
        candidates = list(
            (
                await session.execute(
                    select(MemoryCandidate).where(MemoryCandidate.status == "pending")
                )
            ).scalars()
        )
    return [e.content for e in entries], [c.content for c in candidates]


def scores(a: str, b: str) -> tuple[float, float, float]:
    """(char-set overlap, bigram Jaccard, bigram containment).

    Containment is |A ∩ B| / min(|A|, |B|) — the share of the *shorter* statement
    that the longer one also contains. Jaccard divides by the union, so it punishes
    a rephrasing that says the same thing at greater length; containment does not,
    and "the same preference stated at two lengths" is the case this has to catch.
    """
    from app.ai.memory.lifecycle import _terms

    left, right = _terms(a), _terms(b)
    if not left or not right:
        return char_similarity(a, b), 0.0, 0.0
    shared = len(left & right)
    containment = shared / min(len(left), len(right))
    return char_similarity(a, b), bigram_similarity(a, b), containment


def main() -> int:
    entries, candidates = asyncio.run(load_real())
    print(f"库里的已生效记忆 {len(entries)} 条，待确认候选 {len(candidates)} 条")
    print()

    rows: list[tuple[bool, float, float, float, str, str]] = []
    for left, right, same in LABELLED:
        char, bigram, contain = scores(left, right)
        rows.append((same, char, bigram, contain, left, right))

    # Real-vs-real pairs from the library, labelled by hand where the answer is
    # unambiguous: the six test strings are the same thing, the charts/pages family
    # is the same thing, and a charts/pages statement against a cover-format
    # statement is not.
    charts_family = [
        e
        for e in entries
        if "图表" in e and ("页数" in e or "精简" in e)
    ]
    test_rows = [e for e in entries if "浏览器测试" in e]
    others = [
        e
        for e in entries
        if e not in charts_family and e not in test_rows
    ]
    for i, left in enumerate(charts_family):
        for right in charts_family[i + 1 :]:
            rows.append((True, *scores(left, right), left, right))
    for left in charts_family:
        for right in others:
            rows.append((False, *scores(left, right), left, right))
    for i, left in enumerate(test_rows):
        for right in test_rows[i + 1 :]:
            rows.append((True, *scores(left, right), left, right))

    # --- the distributions ---------------------------------------------------- #
    METRICS = ((1, "char"), (2, "bigram"), (3, "contain"))
    for index, name in METRICS:
        for label in (True, False):
            values = sorted(row[index] for row in rows if row[0] is label)
            if not values:
                continue
            mid = values[len(values) // 2]
            print(
                f"{name:8} {'同一件事' if label else '不同的事':8} "
                f"n={len(values):3}  min={values[0]:.3f}  中位={mid:.3f}  max={values[-1]:.3f}"
            )
    print()

    for index, name in METRICS:
        print(f"— {name}：同一件事（从低到高，只列最低的 10 条）—")
        for same, _c, _b, _ct, left, right in sorted(
            (r for r in rows if r[0]), key=lambda r: r[index]
        )[:10]:
            print(f"  {_c if index == 1 else _b if index == 2 else _ct:.3f}  {left[:24]} ‖ {right[:24]}")
        print(f"— {name}：不同的事（从高到低，只列最高的 6 条）—")
        for same, _c, _b, _ct, left, right in sorted(
            (r for r in rows if not r[0]), key=lambda r: -r[index]
        )[:6]:
            print(f"  {_c if index == 1 else _b if index == 2 else _ct:.3f}  {left[:24]} ‖ {right[:24]}")
        print()

    # --- threshold evaluation ------------------------------------------------- #
    print("阈值评估（同一件事判为同一件 = 对；不同的事判为同一件 = 误合并）")
    print(f"{'metric':8} {'阈值':>6} {'漏判':>6} {'误合并':>8}")
    best: list[tuple[int, float, str]] = []
    for index, name in METRICS:
        for step in range(20, 96):
            threshold = step / 100
            fn = sum(1 for r in rows if r[0] and r[index] < threshold)
            fp = sum(1 for r in rows if not r[0] and r[index] >= threshold)
            best.append((fn + fp, threshold, name))
            if step % 10 == 0:
                print(f"{name:8} {threshold:>6.2f} {fn:>6} {fp:>8}")
    print()
    best.sort()
    print("综合错误最少的几个：")
    seen: set[str] = set()
    for total, threshold, name in best:
        if name in seen:
            continue
        seen.add(name)
        index = dict((n, i) for i, n in METRICS)[name]
        fn = sum(1 for r in rows if r[0] and r[index] < threshold)
        fp = sum(1 for r in rows if not r[0] and r[index] >= threshold)
        print(f"  {name:8} 阈值 {threshold:.2f} → 漏判 {fn}，误合并 {fp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

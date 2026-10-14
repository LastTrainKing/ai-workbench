"""Measure the "is this memory relevant to this request?" signal before choosing a floor.

The rule, from `RETRIEVAL.md` and paid for twice already: **先测分布，再选阈值**. The
knowledge-tier floor in `tiers.py` was chosen this way after two guesses failed. The
same trap is here, and it cost something the user noticed immediately:

    这次任务是「写个 300 字雨后小故事，做成 Word」。
    而它塞进上下文的三条记忆是：讲 PPT 页数的、讲报告结构的、讲答辩封面的。

The reason was not a threshold set too low — it was **no threshold at all**.
`ContextAssembler._memory_block` took the top `MAX_MEMORIES` by confidence/recency and
never looked at the request, so every run got the same memories regardless of what it was
about. The user's own diagnosis was exact: 「收起来也还是错的：错的是召回，不是展示」.

What this script does:

  1. loads the user's real active memories,
  2. pairs them with requests written by hand, each labelled relevant or not,
  3. scores every pair under several candidate signals,
  4. prints both distributions, so the gap — or the overlap — is visible,
  5. evaluates each candidate threshold by false positives and false negatives.

Nothing here is tuned by reasoning about the numbers. The numbers are printed first.

    .venv/Scripts/python.exe scripts/sample_memory_relevance.py
"""

from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.ai.memory.relevance import terms as _terms  # noqa: E402
from app.infrastructure.database import session_scope  # noqa: E402
from app.models.context import MemoryEntry  # noqa: E402

#: Requests, with the indices (1-based) of the memories that genuinely bear on them.
#:
#: Written by hand against the memories this user actually has, which is the point: the
#: real corpus supplies the wording and these supply the requests the real traffic has
#: not produced yet. An empty list is a first-class answer — 「不相关的宁可召回 0 条，
#: 不要凑数」 — and half of these are empty on purpose.
LABELLED: list[tuple[str, list[int]]] = [
    # --- bears on 记忆1 (报告类交付物要内容完整) ---
    ("帮我写一份项目验收报告", [1]),
    ("这份报告至少要包含哪些部分？", [1]),
    ("交付的报告内容太简了，帮我补一补", [1]),
    # --- bears on 记忆2 (PPT 图表多、页数少) ---
    ("做一份 15 分钟的答辩 PPT", [2, 3]),
    ("PPT 页数控制在多少比较合适", [2]),
    ("演示文稿里图表要多放一点吗", [2]),
    # --- bears on 记忆3 (答辩封面与字号) ---
    ("答辩 PPT 的封面要写什么", [3]),
    ("投影的时候后排看不清，正文要多大字号", [3]),
    # --- bears on nothing. Every one of these must recall 0 memories. ---
    ("写个 300 字雨后小故事，做成 Word", []),
    ("解释一下 TCP 三次握手", []),
    ("帮我写个 Python 快速排序", []),
    ("今天北京天气怎么样", []),
    ("推荐三本讲经济学的书", []),
    ("把这段英文翻译成中文", []),
    ("1+1 等于几", []),
    ("帮我起个网名", []),
]


def idf_weights(corpus: list[set[str]]) -> dict[str, float]:
    """How much a term's presence is worth, given how common it is in the corpus.

    A bigram every memory contains is not evidence of anything; one that appears in a
    single memory is. This is the signal the plain overlap count lacks, and the reason
    「一个」 and 「内容」 were worth the same as 「答辩」 under the old rule.
    """
    total = len(corpus)
    df: dict[str, int] = {}
    for terms in corpus:
        for term in terms:
            df[term] = df.get(term, 0) + 1
    return {term: math.log(1 + total / count) for term, count in df.items()}


def candidates(
    question: str,
    memory: str,
    weights: dict[str, float],
    df: dict[str, int],
    corpus_size: int,
) -> dict[str, float]:
    """Every signal under consideration, for one pair."""
    q = _terms(question)
    m = _terms(memory)
    shared = q & m
    overlap = len(shared)
    dice = 0.0 if not q or not m else 2 * overlap / (len(q) + len(m))
    coverage = 0.0 if not q else overlap / len(q)
    weighted = sum(weights.get(term, 0.0) for term in shared)
    strongest = max((weights.get(term, 0.0) for term in shared), default=0.0)
    #: The rarest shared term, as a document frequency. 1 means "no other memory
    #: contains it", which is the only thing that tells one memory apart from another.
    rarest = min((df[term] for term in shared), default=float("inf"))
    return {
        "overlap": float(overlap),
        "dice": dice,
        "coverage": coverage,
        "idf_sum": weighted,
        "idf_max": strongest,
        "rarity": rarity(rarest, corpus_size),
        "rarity_1overdf": 0.0 if rarest == float("inf") else 1.0 / rarest,
    }


def rarity(document_frequency: float, corpus_size: int) -> float:
    """How distinctive a term is, on a scale that does not depend on corpus size.

    0.0 for a term every memory contains, 1.0 for one no other memory contains.
    The `+ 1` matters at a corpus of one: with a single memory every term has
    `df == N`, and a formula without it would call all of them worthless.
    """
    if document_frequency == float("inf"):
        return 0.0
    return 1.0 - math.log(document_frequency) / math.log(corpus_size + 1)


def distribution(values: list[float]) -> str:
    return " ".join(f"{value:.3f}" for value in sorted(values))


def evaluate(
    signal: str,
    scores: list[tuple[float, bool, str]],
    thresholds: list[float],
) -> None:
    """False positives and false negatives for each candidate cut-off."""
    print(f"\n  {signal}")
    print(f"    {'阈值':>8} {'漏召(FN)':>10} {'误召(FP)':>10}  判定")
    best = None
    for threshold in thresholds:
        fp = [label for score, relevant, label in scores if not relevant and score >= threshold]
        fn = [label for score, relevant, label in scores if relevant and score < threshold]
        verdict = ""
        if not fp and not fn:
            verdict = "<- 两组完全分开"
            if best is None:
                best = threshold
        print(f"    {threshold:>8.3f} {len(fn):>10} {len(fp):>10}  {verdict}")
    if best is None:
        print("    （没有阈值能完全分开）")
        # Name the pairs that stand in the way, so the choice is made with eyes open
        # rather than by nudging a number until the counts look good.
        for threshold in thresholds:
            fp = [label for score, relevant, label in scores if not relevant and score >= threshold]
            fn = [label for score, relevant, label in scores if relevant and score < threshold]
            if len(fp) + len(fn) <= 2:
                print(f"    最接近的阈值 {threshold:.3f}：")
                for label in fn:
                    print(f"      漏召  {label}")
                for label in fp:
                    print(f"      误召  {label}")
                break


def main() -> int:
    memories, names = asyncio.run(load_memories())
    if not memories:
        print("数据库里没有 active 记忆，先跑一次真实的对话或验收。")
        return 1

    print("=== 记忆库（active）===")
    for index, text in enumerate(memories, 1):
        print(f"  {index}. {text}")

    corpus = [_terms(text) for text in memories]
    weights = idf_weights(corpus)
    df: dict[str, int] = {}
    for terms in corpus:
        for term in terms:
            df[term] = df.get(term, 0) + 1

    per_signal: dict[str, list[tuple[float, bool, str]]] = {}
    rows: list[tuple[str, int, bool, dict[str, float]]] = []
    for question, expected in LABELLED:
        for index, text in enumerate(memories, 1):
            relevant = index in expected
            scores = candidates(question, text, weights, df, len(corpus))
            label = f"{question}  ->  记忆{index}"
            rows.append((question, index, relevant, scores))
            for signal, value in scores.items():
                per_signal.setdefault(signal, []).append((value, relevant, label))

    print("\n=== 分数分布（相关 / 无关）===")
    for signal, scores in per_signal.items():
        relevant = [value for value, rel, _ in scores if rel]
        unrelated = [value for value, rel, _ in scores if not rel]
        print(f"\n  {signal}")
        print(f"    相关   n={len(relevant):>3}  {distribution(relevant)}")
        print(f"    无关   n={len(unrelated):>3}  {distribution(unrelated)}")
        low_rel = min(relevant) if relevant else float("nan")
        high_unrel = max(unrelated) if unrelated else float("nan")
        gap = low_rel - high_unrel
        print(f"    相关最低={low_rel:.3f}  无关最高={high_unrel:.3f}  间距={gap:+.3f}")

    print("\n=== 按阈值评估 ===")
    grids = {
        "overlap": [0.0, 1.0, 2.0, 3.0],
        "dice": [0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08],
        "coverage": [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30],
        "idf_sum": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
        "idf_max": [0.0, 0.7, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4],
        "rarity": [0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.75, 1.0],
    }
    for signal, thresholds in grids.items():
        evaluate(signal, per_signal[signal], thresholds)

    print("\n=== 逐条（overlap=1 时召回了什么）===")
    for question, expected in LABELLED:
        recalled = [
            index
            for index, text in enumerate(memories, 1)
            if candidates(question, text, weights, df, len(corpus))["overlap"] >= 1
        ]
        mark = "OK " if sorted(recalled) == sorted(expected) else "!! "
        print(f"  {mark}{question:<34} 期望={expected} 实得={recalled}")

    return 0


async def load_memories() -> tuple[list[str], list[str]]:
    async with session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(MemoryEntry)
                    .where(MemoryEntry.status == "active")
                    .order_by(MemoryEntry.created_at)
                )
            ).scalars()
        )
    return [row.content for row in rows], [str(row.id) for row in rows]


if __name__ == "__main__":
    raise SystemExit(main())

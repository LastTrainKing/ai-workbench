"""Sample the score distributions before choosing any threshold.

The rule this file exists to enforce, after two failed guesses:

    **Measure the distribution before picking a cut-off.**
    **Two guesses in a row means it is time to measure, not to guess a third time.**

Both earlier attempts chose a number first and looked at the data afterwards:

1. the retriever's fused score with a 0.35 cut-off — an unrelated question scored **0.70**,
   because the hybrid score is normalised *within the returned set*, so its top value is
   high whether or not anything matched;
2. term overlap with a threshold of 2 — the same unrelated question matched, because CJK
   character bigrams collide on ordinary words ("关系", "一下").

So this script does no judging at all. It asks a set of questions whose answer is known
in advance — half that the library genuinely covers, half that it does not — and prints
every candidate signal for each, grouped, so the separation (or the absence of it) is
visible as data rather than argued about.

    python scripts/sample_relevance.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.rag.retriever import Retriever
from app.ai.memory.relevance import terms as _terms
from app.infrastructure.database import get_session_factory
from app.models.knowledge import DocumentChunk
from sqlalchemy import select

#: Questions the library has nothing about. Chosen to be ordinary Chinese sentences, so
#: the test is not "does it reject nonsense" but "does it reject plausible unrelated
#: questions" — which is the case that actually reaches a user.
UNRELATED = [
    "请用三十个字解释一下量子纠缠和相对论的关系",
    "帮我写一首关于秋天的七言绝句",
    "北京到上海的高铁最快需要多长时间",
    "怎么做番茄炒蛋比较好吃",
    "推荐几部适合周末看的科幻电影",
    "明年世界杯在哪个国家举办",
]


def _overlap(question: str, text: str) -> int:
    return len(_terms(question) & _terms(text))


def describe(name: str, values: list[float]) -> str:
    if not values:
        return f"  {name:<22} (no samples)"
    return (
        f"  {name:<22} n={len(values):<3} "
        f"min={min(values):.3f}  median={statistics.median(values):.3f}  max={max(values):.3f}"
    )


async def main() -> int:
    factory = get_session_factory()
    retriever = Retriever()

    async with factory() as session:
        # The corpus: real chunks from the library.
        chunks = list(
            (
                await session.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.content.isnot(None))
                    .order_by(DocumentChunk.id)
                    .limit(400)
                )
            ).scalars()
        )
        if not chunks:
            print("no document chunks indexed — ingest something first")
            return 1

        # Build "covered" questions from the corpus itself: take a distinctive phrase
        # out of a chunk. If the library does not cover its own content, nothing does.
        #
        # Prose only. The first pass included code fences and diagrams, and one of them
        # produced a covered question with a *lower* vector score than the highest
        # unrelated one — a bad sample, not a bad signal. A question made of mermaid
        # syntax is not a question anyone would ask.
        covered: list[tuple[str, str]] = []
        for chunk in chunks:
            text = (chunk.content or "").strip()
            if len(text) < 60 or text.startswith("```") or "```" in text:
                continue
            if not any("\u4e00" <= c <= "\u9fff" for c in text):
                continue  # need CJK to make a realistic Chinese question
            phrase = "".join(text.split())[:18]
            if len(phrase) >= 10:
                covered.append((phrase, str(chunk.id)))
            if len(covered) >= 8:
                break

        print("=" * 74)
        print("  Relevance signal distributions")
        print("=" * 74)
        print(f"  corpus: {len(chunks)} chunks indexed")
        print(f"  covered questions: {len(covered)}   unrelated questions: {len(UNRELATED)}")

        groups: dict[str, dict[str, list[float]]] = {"covered": {}, "unrelated": {}}

        for group, questions in (
            ("covered", [q for q, _ in covered]),
            ("unrelated", UNRELATED),
        ):
            for question in questions:
                result = await retriever.retrieve(session, query=question, top_k=5)
                got = list(getattr(result, "chunks", []) or [])
                if not got:
                    groups[group].setdefault("fused_top", []).append(0.0)
                    groups[group].setdefault("vector_top", []).append(0.0)
                    groups[group].setdefault("keyword_top", []).append(0.0)
                    groups[group].setdefault("overlap_top", []).append(0.0)
                    continue

                groups[group].setdefault("fused_top", []).append(max(c.score for c in got))
                vector_top = max(getattr(c, "vector_score", 0.0) or 0.0 for c in got)
                keyword_top = max(getattr(c, "keyword_score", 0.0) or 0.0 for c in got)
                groups[group].setdefault("vector_top", []).append(vector_top)
                groups[group].setdefault("keyword_top", []).append(keyword_top)
                groups[group].setdefault("overlap_top", []).append(
                    max(_overlap(question, c.content) for c in got)
                )
                # A combination, because the two absolute signals fail in different
                # places: vector similarity misses an exact phrase that the offline
                # hash embedding does not place well, and the sparse score is zero for a
                # paraphrase that shares no surface form.
                groups[group].setdefault("vector_or_keyword", []).append(
                    max(vector_top / 0.15, keyword_top / 2.0)
                )

        # ------------------------------------------------------------------ output --
        for signal in ("fused_top", "vector_top", "keyword_top", "overlap_top", "vector_or_keyword"):
            print(f"\n--- {signal} ---")
            for group in ("covered", "unrelated"):
                print(describe(group, groups[group].get(signal, [])))

            covered_values = groups["covered"].get(signal, [])
            unrelated_values = groups["unrelated"].get(signal, [])
            if covered_values and unrelated_values:
                lo = min(covered_values)
                hi = max(unrelated_values)
                if lo > hi:
                    gap = lo - hi
                    print(f"  >>> SEPARABLE: 最小覆盖值 {lo:.3f} > 最大无关值 {hi:.3f}，间隙 {gap:.3f}")
                    print(f"  >>> 判据可取区间中点：{(lo + hi) / 2:.3f}")
                else:
                    print(f"  >>> NOT separable: 覆盖最小值 {lo:.3f} <= 无关最大值 {hi:.3f}（重叠 {hi - lo:.3f}）")

        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

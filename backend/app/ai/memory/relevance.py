"""Is this memory about *this* request?

One implementation, used by both places that have to answer it: the retrieval ladder's
memory tier (`ai/rag/tiers.py`) and the context assembler
(`ai/memory/assembler.py`). Two implementations of "relevant" is one too many, and the
two would drift — the ladder's tier decision and the material that gets injected have to
agree, or the run is told it answered from memory while being given something else.

The failure this fixes, in the user's words:

    这次任务是「写个 300 字雨后小故事，做成 Word」。
    而它塞进上下文的三条记忆是：讲 PPT 页数的、讲报告结构的、讲答辩封面的。

and their diagnosis, which was exactly right:

    收起来也还是错的：错的是召回，不是展示。

The cause was not a floor set too low — there was **no floor at all**.
`ContextAssembler._memory_block` ranked memories by confidence and recency and never
looked at the request, so every run got the same handful regardless of what it was about.

The signal, measured before it was chosen
-----------------------------------------
`scripts/sample_memory_relevance.py` pairs the user's real memories with sixteen requests
written by hand, each labelled, and prints the distribution of every candidate signal
before any threshold is picked. Measured on that sample (48 pairs, 9 relevant):

    signal                       related          unrelated        FN  FP
    overlap count (>=1)          1-4              0                0   5
    idf-weighted sum             1.4-4.2          0-1.4            2   1
    rarity of rarest term        1.000            0 / 0.500 / 1.0  0   1  <- chosen

A plain overlap count of one is not a signal at all: Chinese bigrams like 「内容」 and
「报告」 appear in several memories, so a single shared one says nothing about *which*
memory is about this request. That is why the ladder's memory tier and the assembler
were both wrong in the same direction.

The chosen signal asks a narrower question: **does this request share a term that is rare
in the memory corpus?** A term that appears in several memories cannot distinguish
between them, however often it appears.

`RARITY_FLOOR = 0.6` is the smallest value that removes all five over-recalls on the
measured sample; 0.5 lets four of them back in. On this corpus it means "at least one
shared term appears in no other memory", which is the strictest useful form of the rule.

One false positive remains, and it is left in on purpose: 「答辩 PPT 的封面要写什么」
also recalls the memory about PPT page counts. Both genuinely bear on the request, so
excluding the second would be the error, not including it.

Re-measure if the corpus changes shape: `sample_memory_relevance.py` prints the two
distributions and the gap, so the check is one command.
"""

from __future__ import annotations

import math
from typing import Any, Protocol

#: How rare a shared term has to be, on a scale that does not depend on corpus size.
#:
#: 0.0 for a term every memory contains, 1.0 for one no other memory contains. Measured;
#: see the module docstring.
RARITY_FLOOR = 0.6


class _HasContent(Protocol):
    content: str


def terms(text: str) -> set[str]:
    """Crude but predictable tokenisation for CJK and latin alike.

    Deliberately not a tokeniser dependency: this decides *whether to look closer*, not
    how to score. Every character bigram for CJK, every word for latin. A false positive
    costs one extra retrieval; a false negative sends a question that the user's own
    memory could answer down the expensive path, which is the failure this whole module
    exists to prevent.
    """
    lowered = text.lower()
    words = {w for w in "".join(c if c.isalnum() else " " for c in lowered).split() if len(w) > 1}
    cjk = [c for c in lowered if "\u4e00" <= c <= "\u9fff"]
    words |= {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    return words


def document_frequencies(corpus: list[set[str]]) -> dict[str, int]:
    """How many documents each term appears in."""
    counts: dict[str, int] = {}
    for document in corpus:
        for term in document:
            counts[term] = counts.get(term, 0) + 1
    return counts


def rarity(document_frequency: int, corpus_size: int) -> float:
    """How distinctive a term is, between 0.0 (in every memory) and 1.0 (in one).

    The `+ 1` matters at a corpus of one: with a single memory every term has
    `df == corpus_size`, and a formula without it would call all of them worthless.
    """
    if corpus_size <= 0:
        return 0.0
    return 1.0 - math.log(document_frequency) / math.log(corpus_size + 1)


def score(question: str, memory: str, *, df: dict[str, int], corpus_size: int) -> float:
    """The relevance of one memory to one request: the rarest shared term's rarity.

    0.0 when nothing is shared, or when the only shared terms are ones most memories
    contain. See the module docstring for why this and not an overlap count.
    """
    shared = terms(question) & terms(memory)
    if not shared:
        return 0.0
    return max(rarity(df.get(term, corpus_size), corpus_size) for term in shared)


def rank(
    question: str,
    memories: list[Any],
    *,
    floor: float = RARITY_FLOOR,
    limit: int | None = None,
) -> list[tuple[float, Any]]:
    """The memories that bear on `question`, best first.

    **Returns an empty list when nothing is relevant, and that is the point.** The rule
    the user stated: 「不相关的宁可召回 0 条，不要凑数」. A function that always returns
    something is not filtering.
    """
    if not memories:
        return []
    corpus = [terms(str(getattr(m, "content", m))) for m in memories]
    df = document_frequencies(corpus)
    scored = [(score(question, str(getattr(m, "content", m)), df=df, corpus_size=len(memories)), m) for m in memories]
    kept = [(value, m) for value, m in scored if value >= floor]
    kept.sort(key=lambda pair: pair[0], reverse=True)
    return kept[:limit] if limit else kept


__all__ = ["RARITY_FLOOR", "document_frequencies", "rank", "rarity", "score", "terms"]

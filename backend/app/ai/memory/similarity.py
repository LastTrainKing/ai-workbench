"""「这两句话是不是同一件事」— one decision, in one place, with a measured threshold.

Why this module exists
----------------------

Two implementations of this decision had grown up separately:

  `evaluator._similarity`   character-set overlap, threshold 0.82, used for dedup
  `lifecycle.similarity`    Jaccard over CJK bigrams + latin words, threshold 0.62,
                            used for the merge pass

They disagreed, which is the failure mode a "one decision, one implementer" rule
exists to prevent — the same pair of statements could be a duplicate to one part of
the system and two different facts to another.

What the measurement said
-------------------------

`scripts/sample_memory_similarity.py` scores 49 pairs — the real library, plus
hand-written ones whose answer is known — under three metrics. The finding is not a
threshold. It is that **no lexical threshold separates the two groups**:

    同一件事  「用户在做毕业设计，需要写文献综述」 vs 「用户正在做毕业设计，要写文献综述」
              jaccard 0.647   contain 0.786
    不同的事  「用户喜欢用 Python 写脚本。」        vs 「用户喜欢用 Java 写脚本。」
              jaccard 0.778   contain 0.875

The pair that is *not* the same thing scores **higher** than the pair that is, under
both metrics. Every threshold either merges Python into Java or misses the
rephrasing. That is a property of the data, not of the thresholds, and the honest
response is to stop asking a lexical metric this question.

So `same_thing` is used for the job it *can* do — recognising a near-copy, which is
what the merge pass needs — and the "is this the same fact, said differently"
decision is made by the model, which the evaluator already calls once per statement
and which can see the existing memories. See `evaluator._judge`.

The one guard that is not a threshold
-------------------------------------

`named_tokens` — latin words and numbers. Two statements that name *different* things
are not the same statement however similar they look, and that is exactly the
Python/Java shape. Requiring the two sets to be equal costs nothing on the
rephrasings (neither names anything) and closes the case a threshold cannot.
"""

from __future__ import annotations

import re
import unicodedata

#: Two memories this similar are the same fact said twice.
#:
#: Chosen from the measurement, not from taste. On the 49-pair sample:
#:
#:   lowest  "同一件事" pair that must be caught   0.611  (Vue / Vue, reworded)
#:   highest "不同的事" pair after the named-token guard   0.192  (报告背景结论 / 报告页数)
#:
#: So anything in [0.20, 0.61] separates them, and 0.60 is at the conservative end of
#: that window — it merges the pairs that are plainly the same wording and leaves the
#: rephrasings (0.10–0.33) to the judge, which is where they belong.
#:
#: Before the guard existed the window did not exist at all: Python/Java scored 0.778,
#: above every genuine rephrasing, so *any* threshold either merged it or missed them.
MERGE_THRESHOLD = 0.60

_LATIN = re.compile(r"[a-z0-9]{2,}")


def terms(text: str) -> set[str]:
    """The comparable units of a statement: latin words, and CJK character bigrams.

    Bigrams rather than single characters: Chinese has no word boundaries, and a
    single character carries too little meaning for "图表" and "图片" to be told
    apart. A bigram is the cheapest unit that carries some.
    """
    lowered = unicodedata.normalize("NFKC", (text or "").lower())
    words = set(_LATIN.findall(lowered))
    cjk = [c for c in lowered if "\u4e00" <= c <= "\u9fff"]
    words |= {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    return words


def named_tokens(text: str) -> set[str]:
    """Latin words and numbers — the things a statement can *name*.

    `python` / `java`, `react` / `vue`, `12` / `20`. Two statements that name
    different things are different statements; two that name nothing, or the same
    things, may well be the same one reworded.
    """
    return set(_LATIN.findall(unicodedata.normalize("NFKC", (text or "").lower())))


def similarity(a: str, b: str) -> float:
    """Jaccard overlap of the two term sets. 0..1."""
    left, right = terms(a), terms(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def containment(a: str, b: str) -> float:
    """How much of the *shorter* statement the longer one also contains. 0..1.

    Not used by the routing decision — reported by the sampling script, where it is
    the metric that looked most promising and turned out not to separate the groups
    either. Kept because the next person to reach for it should be able to reproduce
    that finding rather than repeat the experiment.
    """
    left, right = terms(a), terms(b)
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def same_thing(a: str, b: str, *, threshold: float = MERGE_THRESHOLD) -> bool:
    """Whether two statements are near-copies of one another.

    Two conditions, and the second is not a threshold:

    1. term overlap at or above `threshold`, and
    2. they name the same things — `named_tokens(a) == named_tokens(b)`.

    Without (2) the merge pass folds 「用户喜欢用 Python 写脚本」 into 「用户喜欢用
    Java 写脚本」 (0.778, above the threshold, and above every genuine rephrasing in
    the sample). With it, that pair is refused and every rephrasing in the sample is
    unaffected, because none of them names anything.
    """
    if named_tokens(a) != named_tokens(b):
        return False
    return similarity(a, b) >= threshold


__all__ = [
    "MERGE_THRESHOLD",
    "containment",
    "named_tokens",
    "same_thing",
    "similarity",
    "terms",
]

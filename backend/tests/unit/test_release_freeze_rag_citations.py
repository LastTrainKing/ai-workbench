"""Release-freeze regression tests — RAG citation constraint and grounding.

The confirmed defect: ``grounded = bool(used_ids)``. **One** valid citation number
anywhere in the answer marked the whole answer grounded, including the paragraphs around
it that cited nothing. The README then described the result as "citation-enforced", which
was never a property of the code.

``analyse_citations`` is pure, so these tests pin the rule directly: a sentence is
supported only when it carries a marker that resolves to a real source, and the answer is
grounded only when **every** claim-bearing sentence is supported and no marker dangles.
"""

from __future__ import annotations

import pytest

from app.ai.rag.pipeline import analyse_citations


def test_no_citations_at_all_is_not_grounded() -> None:
    report = analyse_citations("G1 是一种分代收集器。它的停顿时间可以预测。", 3)
    assert report.grounded is False
    assert report.valid_ids == []
    assert "没有任何引用编号" in report.reason


def test_every_claim_cited_is_grounded() -> None:
    answer = "G1 把堆划分为 Region[1]。停顿时间可以预测[2]。"
    report = analyse_citations(answer, 2)
    assert report.grounded is True
    assert report.valid_ids == [1, 2]
    assert report.invalid_ids == []
    assert report.coverage == 1.0


def test_one_citation_for_many_claims_is_not_grounded() -> None:
    """The defect, stated as an assertion.

    The first sentence cites [1] and the two that follow cite nothing. Under the old rule
    this was "grounded" because a valid number was present.
    """
    answer = (
        "G1 把堆划分为 Region[1]。"
        "ZGC 追求极低的停顿时间。"
        "HashMap 在 JDK 8 之后采用红黑树。"
    )
    report = analyse_citations(answer, 2)
    assert report.grounded is False
    assert report.claim_units == 3
    assert report.supported_units == 1
    assert report.coverage < 1.0
    assert "1/3" in report.reason


def test_out_of_range_citation_is_not_evidence() -> None:
    report = analyse_citations("这是唯一一句有编号的话[9]。", 2)
    assert report.grounded is False
    assert report.invalid_ids == [9]
    assert report.valid_ids == []
    assert "越界" in report.reason


def test_dangling_citation_invalidates_an_otherwise_cited_answer() -> None:
    """A number that points at nothing must not be silently treated as a citation."""
    answer = "第一句有据[1]。第二句引用了不存在的资料[7]。"
    report = analyse_citations(answer, 2)
    assert report.grounded is False
    assert report.invalid_ids == [7]
    assert report.valid_ids == [1]


def test_malformed_markers_are_not_counted() -> None:
    """`[123]` is outside the 1..99 the pattern accepts, so it matches nothing."""
    report = analyse_citations("这句话看起来有引用[123]但其实没有。", 2)
    assert report.valid_ids == []
    assert report.invalid_ids == []
    assert report.grounded is False


def test_no_retrieved_sources_cannot_be_grounded() -> None:
    report = analyse_citations("回答里写着 [1]。", 0)
    assert report.grounded is False
    assert report.source_count == 0
    assert "没有检索到" in report.reason


def test_empty_answer_is_not_grounded() -> None:
    report = analyse_citations("", 3)
    assert report.grounded is False
    assert report.claim_units == 0
    assert report.coverage == 0.0


def test_short_fragments_are_not_treated_as_claims() -> None:
    """A heading or a stray number is not a claim that needs a citation."""
    answer = "要点\n1.\n[1] G1 把堆划分为多个 Region，停顿可预测[1]。"
    report = analyse_citations(answer, 1)
    assert report.grounded is True


@pytest.mark.parametrize(
    "answer",
    [
        "只有结论，没有编号。",
        "编号越界[42]。",
        "格式不对[abc]的引用。",
        "",
    ],
)
def test_ungrounded_answers_are_never_reported_as_grounded(answer: str) -> None:
    assert analyse_citations(answer, 3).grounded is False


def test_report_payload_is_json_shaped() -> None:
    payload = analyse_citations("有据的一句[1]。", 1).to_payload()
    for key in (
        "source_count",
        "valid_citation_ids",
        "invalid_citation_ids",
        "claim_units",
        "supported_units",
        "coverage",
        "grounded",
        "reason",
    ):
        assert key in payload
    assert payload["grounded"] is True

"""Preference semantics: the task constrains, the preference only suggests.

The four acceptance criteria from the task book are asserted here at the level where
they are decided — the derivation and the resolution — so that a failure points at
the rule rather than at a model's mood:

    1  15 分钟 + 页数更少        → 10~14 页，且说明这是硬约束下的下限
    2  15 分钟 + 图表更多        → 图表偏好生效（charts_per_slide > 0）
    3  3 分钟 + 内容详尽         → 服从 3 分钟约束，并说明为什么做不到详尽
    4  模板不存在                → 明确报告做不到，不静默忽略
"""

from __future__ import annotations

import pytest

from app.ai.agent.constraints import (
    Preference,
    derive_constraints,
    resolve_preferences,
)
from app.ai.tools.builtin.documents import ChartRenderError, _write_pptx
from app.core.errors import ValidationError

STYLE = {
    "slides": "medium",
    "bullets_per_slide": 5,
    "words_per_bullet": 16,
    "font_scale": 1.0,
    "speaker_notes": False,
    "title_only_intro": True,
}


def sections(count: int) -> list[dict]:
    return [{"heading": f"第 {i} 页", "content": [f"要点 {i}"]} for i in range(count)]


# --------------------------------------------------------------------------- #
# Deriving the hard constraint
# --------------------------------------------------------------------------- #
def test_fifteen_minutes_gives_ten_to_fourteen_slides():
    constraints = derive_constraints("用资料做一份 15 分钟的答辩 PPT")
    assert (constraints.slide_min, constraints.slide_max) == (10, 14)
    assert constraints.duration_minutes == 15
    assert "15 分钟" in constraints.evidence


def test_three_minutes_gives_three_to_five_slides():
    constraints = derive_constraints("做一份 3 分钟的快速汇报 PPT")
    assert (constraints.slide_min, constraints.slide_max) == (3, 5)


def test_a_goal_without_a_duration_has_no_slide_constraint():
    constraints = derive_constraints("做一份介绍项目的 PPT")
    assert constraints.has_slide_range is False
    assert constraints.slide_min == 0


def test_the_chinese_phrase_is_parsed_even_when_followed_by_more_characters():
    """`分钟\b` never matched "15 分钟的…" — the boundary is not there in Chinese."""
    assert derive_constraints("15 分钟的答辩").duration_minutes == 15
    assert derive_constraints("做一份15分钟答辩PPT").duration_minutes == 15
    assert derive_constraints("a 20 minutes talk").duration_minutes == 20


def test_absurd_durations_are_rejected_rather_than_extrapolated():
    assert derive_constraints("做一份 900 分钟的汇报").has_slide_range is False


def test_the_rationale_names_the_source_phrase():
    text = derive_constraints("15 分钟答辩").rationale()
    assert "10~14" in text
    assert "15 分钟" in text


# --------------------------------------------------------------------------- #
# Criterion 1 — 15 minutes + fewer pages
# --------------------------------------------------------------------------- #
def test_fewer_pages_is_bounded_by_the_fifteen_minute_floor():
    constraints = derive_constraints("做一份 15 分钟的答辩 PPT")
    resolution = resolve_preferences(
        [Preference("用户偏好演示文稿页数更少")], constraints, workspace=__import__("pathlib").Path(".")
    )
    assert resolution.target_slides == 10, "must be the floor, not 2 and not 0"
    outcome = resolution.outcomes[0]
    assert outcome.verdict == "bounded"
    assert "10 页" in outcome.detail
    assert "不能再少" in outcome.detail


def test_the_report_states_that_ten_pages_is_the_minimum_under_the_constraint():
    constraints = derive_constraints("做一份 15 分钟的答辩 PPT")
    resolution = resolve_preferences(
        [Preference("页数更少")], constraints, workspace=__import__("pathlib").Path(".")
    )
    report = resolution.report()
    assert "取舍说明" in report
    assert "10 页" in report
    assert "最小值" in report


def test_fewer_pages_without_a_duration_is_simply_applied():
    constraints = derive_constraints("做一份 PPT")
    resolution = resolve_preferences(
        [Preference("页数更少")], constraints, workspace=__import__("pathlib").Path(".")
    )
    assert resolution.outcomes[0].verdict == "applied"
    assert resolution.target_slides == 0


# --------------------------------------------------------------------------- #
# Criterion 2 — more charts
# --------------------------------------------------------------------------- #
def test_more_charts_is_applied_and_recorded():
    constraints = derive_constraints("做一份 15 分钟的答辩 PPT")
    resolution = resolve_preferences(
        [Preference("演示文稿图表更多")], constraints, workspace=__import__("pathlib").Path(".")
    )
    assert resolution.charts_per_slide == 1
    assert resolution.outcomes[0].verdict == "applied"
    assert "图表" in resolution.report()


# --------------------------------------------------------------------------- #
# Criterion 3 — 3 minutes + detailed
# --------------------------------------------------------------------------- #
def test_detailed_content_cannot_override_a_three_minute_talk():
    constraints = derive_constraints("做一份 3 分钟的快速汇报 PPT")
    resolution = resolve_preferences(
        [Preference("内容要详尽")], constraints, workspace=__import__("pathlib").Path(".")
    )
    outcome = resolution.outcomes[0]
    assert outcome.verdict == "bounded"
    assert "做不到详尽" in outcome.detail
    assert "3 分钟" in outcome.detail
    # Built to the top of the allowed range: as much as three minutes can carry.
    assert resolution.target_slides == 5
    assert "做不到详尽" in resolution.report()


# --------------------------------------------------------------------------- #
# Criterion 4 — a preference that cannot be executed
# --------------------------------------------------------------------------- #
def test_a_missing_template_is_reported_not_ignored(tmp_path):
    constraints = derive_constraints("做一份 PPT")
    resolution = resolve_preferences(
        [Preference("用公司模板")], constraints, workspace=tmp_path
    )
    outcome = resolution.outcomes[0]
    assert outcome.verdict == "unsatisfiable"
    assert "模板" in outcome.detail
    assert str(tmp_path) in outcome.detail
    assert resolution.unsatisfiable == [outcome]
    # And it must be told to the user, not merely stored.
    assert "必须" in resolution.render()


def test_a_template_that_exists_is_used(tmp_path):
    (tmp_path / "公司模板.pptx").write_bytes(b"x")
    constraints = derive_constraints("做一份 PPT")
    resolution = resolve_preferences([Preference("用公司模板")], constraints, workspace=tmp_path)
    assert resolution.outcomes[0].verdict == "applied"
    assert "公司模板.pptx" in resolution.outcomes[0].detail


# --------------------------------------------------------------------------- #
# One sentence, two preferences
# --------------------------------------------------------------------------- #
def test_one_memory_carrying_two_preferences_yields_both_outcomes():
    """The learned memory reads "图表更多、页数更少" — matching it as one string
    would honour whichever intent was tested first and drop the other."""
    constraints = derive_constraints("做一份 15 分钟的答辩 PPT")
    resolution = resolve_preferences(
        [Preference("用户偏好演示文稿图表更多、页数更少")],
        constraints,
        workspace=__import__("pathlib").Path("."),
    )
    verdicts = {outcome.verdict for outcome in resolution.outcomes}
    assert verdicts == {"applied", "bounded"}
    assert resolution.charts_per_slide == 1
    assert resolution.target_slides == 10


def test_an_unrecognised_preference_is_labelled_as_guidance():
    constraints = derive_constraints("做一份 PPT")
    resolution = resolve_preferences(
        [Preference("喜欢蓝色配色")], constraints, workspace=__import__("pathlib").Path(".")
    )
    assert resolution.outcomes[0].verdict == "guidance"
    assert "无法确认" in resolution.outcomes[0].detail


def test_no_preferences_produces_no_outcomes_and_no_report():
    constraints = derive_constraints("做一份 PPT")
    resolution = resolve_preferences([], constraints, workspace=__import__("pathlib").Path("."))
    assert resolution.outcomes == []
    assert resolution.report() == ""


# --------------------------------------------------------------------------- #
# The writer enforces the constraint it was given
# --------------------------------------------------------------------------- #
def test_pptx_refuses_to_produce_fewer_slides_than_the_constraint_allows(tmp_path):
    target = tmp_path / "too-short.pptx"
    with pytest.raises(ValidationError) as caught:
        _write_pptx(target, sections(3), STYLE, min_slides=10, max_slides=14)
    message = str(caught.value)
    assert "至少 10 页" in message
    # 1 cover + 2 body sections. This is the "2 页" deck, refused at the writer.
    assert "实际只能生成 3 页" in message
    assert not target.exists(), "a deck that violates the constraint must not be left behind"


def test_pptx_meets_the_constraint_when_enough_content_is_supplied(tmp_path):
    target = tmp_path / "ok.pptx"
    stats = _write_pptx(target, sections(10), STYLE, min_slides=10, max_slides=14)
    # 1 cover + 9 body sections.
    assert stats["slides"] == 10
    assert 10 <= stats["slides"] <= 14
    assert target.is_file()


def test_pptx_trims_to_the_upper_bound_and_says_so(tmp_path):
    target = tmp_path / "long.pptx"
    stats = _write_pptx(target, sections(30), STYLE, min_slides=10, max_slides=14)
    assert stats["slides"] == 14
    assert stats["slides_dropped"] == 29 - 13  # 30 sections minus the cover, minus what fits
    assert stats["max_slides"] == 14


def test_a_chart_that_cannot_be_drawn_is_recorded_not_swallowed(tmp_path):
    """The exact mechanism behind the silently-unapplied "更多图表" preference.

    `_render_chart` used to return `None` and log a warning; the deck came out with
    zero images and every check still passed. Now the failure is raised by the
    renderer, caught per section so the rest of the deck survives, and reported —
    which is what makes `document_write` return `ok=False` instead of a tidy
    success for a deck that does not contain the charts it was asked for.
    """
    from app.ai.tools.builtin.documents import _render_chart

    with pytest.raises(ChartRenderError):
        _render_chart({"type": "bar", "categories": [], "series": {}}, tmp_path / "x.png")

    target = tmp_path / "chartless.pptx"
    bad = sections(2)
    bad[1]["chart"] = {"type": "bar", "categories": [], "series": {}}
    stats = _write_pptx(target, bad, STYLE)
    assert stats["images"] == 0
    assert len(stats["chart_failures"]) == 1
    assert "categories" in stats["chart_failures"][0]["error"]


def test_a_chart_that_can_be_drawn_is_counted(tmp_path):
    target = tmp_path / "charted.pptx"
    payload = sections(2)
    payload[1]["chart"] = {
        "type": "bar",
        "title": "各模块页数",
        "categories": ["架构", "数据库", "AI 模块"],
        "series": {"页数": [3, 4, 4]},
    }
    stats = _write_pptx(target, payload, STYLE)
    assert stats["images"] == 1
    assert stats["chart_failures"] == []
    assert target.is_file()


# --------------------------------------------------------------------------- #
# The chart shape a model actually produces
# --------------------------------------------------------------------------- #
def test_the_series_shape_a_model_actually_sends_is_accepted(tmp_path):
    """The real failure: `series: [{name, data}]` — the natural reading — was
    rejected because the implementation expected `{name: [values]}`. Every chart in
    a 10-slide deck failed, and the user's "more charts" preference went unapplied.
    """
    from app.ai.tools.builtin.documents import normalise_chart

    categories, series = normalise_chart(
        {
            "type": "line",
            "categories": ["1月", "2月", "3月"],
            "series": [{"name": "月活", "data": [42, 48, 55]}],
        }
    )
    assert categories == ["1月", "2月", "3月"]
    assert series == {"月活": [42.0, 48.0, 55.0]}


def test_a_full_deck_with_one_chart_per_slide_comes_out_with_every_chart(tmp_path):
    target = tmp_path / "per-slide.pptx"
    payload = sections(4)
    for index, section in enumerate(payload):
        section["chart"] = {
            "type": "bar",
            "title": f"图表 {index}",
            "categories": ["一", "二"],
            "series": [{"name": "数量", "data": [index + 1, index + 2]}],
        }
    stats = _write_pptx(target, payload, STYLE)
    assert stats["chart_failures"] == []
    # The first section becomes the cover, which carries no chart — so every
    # content slide has one, which is what "每页至少 1 张图" means.
    assert stats["images"] == len(payload) - 1
    assert stats["slides"] == len(payload)


def test_the_other_shapes_and_aliases_are_accepted():
    from app.ai.tools.builtin.documents import normalise_chart

    assert normalise_chart({"labels": ["a", "b"], "values": [1, 2]})[0] == ["a", "b"]
    assert normalise_chart({"categories": ["a", "b"], "series": {"n": [1, 2]}})[1] == {"n": [1.0, 2.0]}
    assert normalise_chart({"categories": ["a"], "series": [3]})[1] == {"数值": [3.0]}
    assert normalise_chart({"x": ["a"], "series": [[1]]})[1] == {"系列1": [1.0]}
    # Numeric strings and percent signs are what a model writes when it is unsure.
    assert normalise_chart({"categories": ["a"], "series": {"n": ["42%"]}})[1] == {"n": [42.0]}


def test_unusable_charts_say_exactly_what_is_wrong():
    from app.ai.tools.builtin.documents import normalise_chart

    with pytest.raises(ChartRenderError) as caught:
        normalise_chart({"categories": ["a", "b"], "series": {"n": [1]}})
    assert "数量必须一致" in str(caught.value)

    with pytest.raises(ChartRenderError) as caught:
        normalise_chart({"categories": ["a"], "series": {"n": ["很多"]}})
    assert "非数值" in str(caught.value)

    with pytest.raises(ChartRenderError) as caught:
        normalise_chart({"series": {"n": [1]}})
    assert "categories" in str(caught.value)

    with pytest.raises(ChartRenderError) as caught:
        normalise_chart({"categories": ["a"], "series": []})
    assert "空" in str(caught.value)

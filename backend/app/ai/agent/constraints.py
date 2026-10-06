"""Hard constraints from the task, soft preferences from the user, and who wins.

The failure this module exists for is small and specific:

    目标：做一份 15 分钟的答辩 PPT
    偏好：页数更少
    结果：2 页

Nothing crashed. Every check passed. The deck was 2 slides because "页数更少" was
treated as *the thing to do* rather than as *a standard to apply while doing it* —
and a 15-minute talk has a physical floor that no preference can talk its way under.

So there are three tiers and the order is not negotiable:

    任务目标（硬约束）  >  长期偏好（软约束）  >  默认行为

* **hard constraint** — derived from the task itself, deterministically, in code.
  "15 分钟" is a fact about the request; a 15-minute talk cannot be delivered from
  two slides, and no amount of preference changes that.
* **soft preference** — applied *within* the hard constraints, and when it cannot
  be fully honoured the trade-off is stated rather than hidden.
* **default** — what happens when neither says anything.

Two commitments follow, and they are the reason this is code rather than prompt text:

**A preference that cannot be executed must be reported.** A silently ignored
preference is worse than a refused one, because the user believes it took effect.
"If the company template does not exist" is a fact this module can check, and it
says so.

**The trade-off must be stated, not implied.** The resolution produces the exact
sentences that go into the plan and the final report, so "did it explain why the
deck is 10 pages?" is answerable by reading the output rather than by trusting the
model to have volunteered it.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

#: "15 分钟" / "15min" / "15 minutes". No trailing ``\b``: Python's word boundary
#: is defined against ASCII, and "分钟" followed by "的" is two word characters
#: with no boundary between them — so ``分钟\b`` silently never matched the most
#: common phrasing in Chinese. The digits in front are what keep "admin" from
#: matching.
_DURATION_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:分钟|分鐘|分|minutes|minute|mins|min)",
    re.IGNORECASE,
)

#: How many slides a talk of N minutes needs, as (minutes, minimum, maximum).
#:
#: Chosen from how presentations are actually paced — roughly one content slide
#: per 1 to 1.5 minutes of speech — with a floor of three because a deck still
#: needs a title and a closing slide. Written as anchors rather than a formula
#: because a formula that produced 17 slides for a 15-minute talk would be a
#: formula that had never been checked against a real one.
_SLIDE_ANCHORS: tuple[tuple[float, int, int], ...] = (
    (1.0, 3, 4),
    (3.0, 3, 5),
    (5.0, 4, 7),
    (10.0, 7, 10),
    (15.0, 10, 14),
    (20.0, 13, 18),
    (30.0, 18, 26),
    (45.0, 26, 38),
)

#: Preference phrases this module can actually act on. Anything else is passed
#: through as guidance, and said to be guidance.
_FEWER = ("更少", "少些", "少一点", "少一些", "精简", "减少", "压缩", "fewer", "less", "shorter")
_MORE = ("更多", "多些", "多一点", "多一些", "增加", "丰富", "more")
_DETAILED = ("详尽", "详细", "完整", "全面", "深入", "detailed", "thorough", "verbose")
_CONCISE = ("简洁", "精简", "简短", "concise", "brief")
_CHART = ("图表", "配图", "插图", "图形", "可视化", "chart", "figure", "diagram", "graph")
_TEMPLATE = ("模板", "template")
_WORDY = ("文字", "字数", "堆得太满", "文字量")


def _interpolate(minutes: float) -> tuple[int, int]:
    anchors = _SLIDE_ANCHORS
    if minutes <= anchors[0][0]:
        return anchors[0][1], anchors[0][2]
    if minutes >= anchors[-1][0]:
        # Extrapolate on the last segment rather than clamping: a two-hour talk
        # does not need the same deck as a 45-minute one.
        last = anchors[-1]
        previous = anchors[-2]
        span = last[0] - previous[0]
        rate_low = (last[1] - previous[1]) / span
        rate_high = (last[2] - previous[2]) / span
        extra = minutes - last[0]
        return round(last[1] + rate_low * extra), round(last[2] + rate_high * extra)
    for (m0, lo0, hi0), (m1, lo1, hi1) in itertools.pairwise(anchors):
        if m0 <= minutes <= m1:
            ratio = (minutes - m0) / (m1 - m0)
            return round(lo0 + (lo1 - lo0) * ratio), round(hi0 + (hi1 - hi0) * ratio)
    return anchors[0][1], anchors[0][2]


@dataclass
class TaskConstraints:
    """What the request itself requires, before anyone's preferences are consulted."""

    duration_minutes: float = 0.0
    slide_min: int = 0
    slide_max: int = 0
    #: The phrase the duration was read from, so the derivation is auditable.
    evidence: str = ""

    @property
    def has_slide_range(self) -> bool:
        return self.slide_min > 0 and self.slide_max >= self.slide_min

    def rationale(self) -> str:
        if not self.has_slide_range:
            return ""
        return (
            f"{self.duration_minutes:g} 分钟的汇报，按每分钟约 1 页的语速，"
            f"合理页数是 {self.slide_min}~{self.slide_max} 页"
            f"（下限来自「{self.evidence}」这条任务要求，不是偏好）"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "duration_minutes": self.duration_minutes,
            "slide_min": self.slide_min,
            "slide_max": self.slide_max,
            "evidence": self.evidence,
            "rationale": self.rationale(),
        }

    def render(self) -> str:
        lines = ["【本次任务的硬约束】这些来自任务本身，优先于任何长期偏好："]
        if self.duration_minutes:
            lines.append(f"- 汇报时长：{self.duration_minutes:g} 分钟（依据：原文「{self.evidence}」）")
        if self.has_slide_range:
            lines.append(f"- 幻灯片页数：必须是 {self.slide_min}~{self.slide_max} 页")
            lines.append(f"- 依据：{self.rationale()}")
        lines.append(
            "偏好只能在这个范围内起作用。偏好与硬约束冲突时，服从硬约束，"
            "并在结果里明确说明你做了什么取舍。"
        )
        return "\n".join(lines)


def derive_constraints(goal: str, *, deliverable_kind: str = "") -> TaskConstraints:
    """Read the task's own requirements out of the goal, in code.

    Deterministic on purpose. If this were left to the model, a preference saying
    "fewer pages" would be weighed against a duration the model had merely been
    told about — and a model asked to satisfy both will sometimes satisfy the
    wrong one. A number derived here cannot be negotiated away.
    """
    text = goal or ""
    match = _DURATION_PATTERN.search(text)
    if not match:
        return TaskConstraints()

    try:
        minutes = float(match.group(1))
    except ValueError:
        return TaskConstraints()
    if minutes <= 0 or minutes > 600:
        return TaskConstraints()

    low, high = _interpolate(minutes)
    return TaskConstraints(
        duration_minutes=minutes,
        slide_min=low,
        slide_max=high,
        evidence=match.group(0).strip(),
    )


# --------------------------------------------------------------------------- #
# Preferences
# --------------------------------------------------------------------------- #
@dataclass
class Preference:
    """One thing the user has said they prefer, and where it came from."""

    text: str
    kind: str = "preference"
    source: str = "memory"
    confidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "kind": self.kind,
            "source": self.source,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class PreferenceOutcome:
    """What happened to one preference, and why."""

    text: str
    verdict: str  # applied | bounded | unsatisfiable | guidance
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "verdict": self.verdict, "detail": self.detail}

    def render(self) -> str:
        return f"- [{self.verdict}] 「{self.text}」—— {self.detail}"


@dataclass
class PreferenceResolution:
    """The resolved set, plus the exact text that goes into the prompts."""

    outcomes: list[PreferenceOutcome] = field(default_factory=list)
    target_slides: int = 0
    charts_per_slide: int = 0
    constraints: TaskConstraints = field(default_factory=TaskConstraints)

    @property
    def unsatisfiable(self) -> list[PreferenceOutcome]:
        return [o for o in self.outcomes if o.verdict == "unsatisfiable"]

    @property
    def bounded(self) -> list[PreferenceOutcome]:
        return [o for o in self.outcomes if o.verdict == "bounded"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcomes": [o.as_dict() for o in self.outcomes],
            "target_slides": self.target_slides,
            "charts_per_slide": self.charts_per_slide,
            "unsatisfiable": [o.as_dict() for o in self.unsatisfiable],
            "bounded": [o.as_dict() for o in self.bounded],
        }

    def render(self) -> str:
        if not self.outcomes:
            return ""
        lines = ["【长期偏好如何被采纳】"]
        lines.extend(outcome.render() for outcome in self.outcomes)
        if self.unsatisfiable:
            lines.append(
                "上面标 unsatisfiable 的偏好这次做不到。必须在给用户的回复里明确说出来，"
                "不许假装满足，也不许默默跳过。"
            )
        return "\n".join(lines)

    def report(self) -> str:
        """The deterministic trade-off note appended to the final report.

        Written here rather than left to the model's summary because the question
        "did it explain the page count?" has to be answerable from the output, and
        a model that happens not to mention it would make the answer "no".
        """
        if not self.outcomes and not self.constraints.has_slide_range:
            return ""
        lines = ["【取舍说明】"]
        if self.constraints.has_slide_range:
            lines.append(f"- 硬约束：{self.constraints.rationale()}。")
        if self.target_slides:
            lines.append(
                f"- 本次目标页数定为 {self.target_slides} 页"
                + (
                    f"——这是 {self.constraints.duration_minutes:g} 分钟约束下的最小值，"
                    "长期偏好希望更少，但再少就讲不满这个时长。"
                    if self.target_slides == self.constraints.slide_min
                    and any(o.verdict == "bounded" for o in self.bounded)
                    else "。"
                )
            )
        if self.charts_per_slide:
            lines.append(f"- 图表偏好生效：每个内容页附 {self.charts_per_slide} 张图。")
        for outcome in self.outcomes:
            if outcome.verdict in ("bounded", "unsatisfiable"):
                lines.append(f"- 「{outcome.text}」：{outcome.detail}")
        return "\n".join(lines)


def _contains(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _template_files(workspace: Path) -> list[Path]:
    """Any file that could be used as a document template."""
    if not workspace.is_dir():
        return []
    suffixes = {".pptx", ".potx", ".docx", ".dotx", ".xlsx", ".xltx"}
    found: list[Path] = []
    for path in workspace.rglob("*"):
        if (
            path.is_file()
            and path.suffix.lower() in suffixes
            and ("template" in path.stem.lower() or "模板" in path.stem)
        ):
            found.append(path)
        if len(found) >= 5:
            break
    return found


def _charts_available() -> tuple[bool, str]:
    try:
        import matplotlib  # noqa: F401

        return True, ""
    except ImportError as exc:
        return False, f"当前环境没有安装 matplotlib（{exc}）"


def resolve_preferences(
    preferences: list[Preference],
    constraints: TaskConstraints,
    *,
    workspace: Path | None = None,
) -> PreferenceResolution:
    """Decide, per preference, whether it can be honoured — and say so when it cannot.

    Evaluated per *intent*, not per sentence. A single learned memory can easily
    read "用户偏好演示文稿图表更多、页数更少" — two preferences in one string — and
    matching the sentence as a whole would honour whichever intent happened to be
    tested first and silently drop the other. That is the same failure this module
    exists to prevent, one level down.
    """
    resolution = PreferenceResolution(constraints=constraints)
    workspace = workspace or Path.cwd()

    wants_fewer = False
    wants_detailed = False
    wants_charts = False

    def add(text: str, verdict: str, detail: str) -> None:
        resolution.outcomes.append(PreferenceOutcome(text, verdict, detail))

    for preference in preferences:
        text = preference.text
        lowered = text.lower()
        matched = False

        # --- a preference that names a resource we can actually check ---------
        if _contains(lowered, _TEMPLATE):
            matched = True
            templates = _template_files(workspace)
            if templates:
                add(text, "applied", f"找到可用模板 {templates[0].name}，会以它为基底")
            else:
                add(
                    text,
                    "unsatisfiable",
                    f"做不到：工作区（{workspace}）里没有任何模板文件"
                    "（*.pptx / *.potx / *.docx），无法套用模板",
                )

        if _contains(lowered, _CHART):
            matched = True
            available, reason = _charts_available()
            if available:
                wants_charts = True
                add(text, "applied", "会为内容页生成图表（matplotlib 可用）")
            else:
                add(text, "unsatisfiable", f"做不到：{reason}")

        # --- slide-count and density intents ----------------------------------
        if _contains(lowered, _FEWER) and (
            _contains(lowered, ("页", "slide", "page")) or constraints.has_slide_range
        ):
            matched = True
            wants_fewer = True
            if constraints.has_slide_range:
                add(
                    text,
                    "bounded",
                    f"只能部分满足：本次是 {constraints.duration_minutes:g} 分钟的汇报，"
                    f"硬约束要求至少 {constraints.slide_min} 页，"
                    f"因此取 {constraints.slide_min} 页（区间下限），不能再少",
                )
            else:
                add(text, "applied", "没有时长约束，按偏好的偏少页数执行")

        if _contains(lowered, _DETAILED):
            matched = True
            wants_detailed = True
            if constraints.has_slide_range:
                add(
                    text,
                    "bounded",
                    f"做不到详尽：本次是 {constraints.duration_minutes:g} 分钟的汇报，"
                    f"时长只允许 {constraints.slide_min}~{constraints.slide_max} 页，"
                    "内容必须压缩到讲得完的程度",
                )
            else:
                add(text, "applied", "没有时长约束，按偏好的详尽程度执行")

        if _contains(lowered, _CONCISE) and not _contains(lowered, _MORE):
            matched = True
            if constraints.has_slide_range:
                add(
                    text,
                    "bounded",
                    f"在 {constraints.slide_min}~{constraints.slide_max} 页内尽量精简，"
                    "但页数不能低于硬约束下限",
                )
            else:
                add(text, "applied", "按偏好精简")

        if _contains(lowered, _WORDY):
            matched = True
            add(text, "applied", "每页文字量会控制得更少，多用短句")

        if not matched:
            add(
                text,
                "guidance",
                "已作为风格参考采纳；这不是可机器验证的偏好，无法确认它是否真的生效",
            )

    # --- the resolved numbers ------------------------------------------------
    if constraints.has_slide_range:
        resolution.target_slides = constraints.slide_min if wants_fewer else constraints.slide_max
    if wants_charts:
        resolution.charts_per_slide = 1
    if wants_detailed and constraints.has_slide_range:
        # Detail was asked for and cannot be granted. The deck is built to the top
        # of the allowed range, which is as much content as the time allows.
        resolution.target_slides = constraints.slide_max

    return resolution


async def load_preferences(session: AsyncSession, user_id: Any) -> list[Preference]:
    """Collect the user's long-term preferences from both places they live.

    Two sources, deliberately: `UserPreference` is what the user set explicitly,
    `MemoryEntry` is what the system learned from delivery decisions. Reading only
    one of them would make half of the personalisation invisible to the layer that
    is supposed to enforce it.
    """
    from app.models.context import MemoryEntry, UserPreference

    found: list[Preference] = []
    try:
        rows = list(
            (
                await session.execute(
                    select(MemoryEntry).where(
                        MemoryEntry.user_id == user_id,
                        MemoryEntry.status == "active",
                        MemoryEntry.kind.in_(("preference", "constraint")),
                    )
                )
            ).scalars()
        )
        found.extend(
            Preference(
                text=row.content,
                kind=row.kind,
                source=f"memory:{row.id}",
                confidence=float(row.confidence or 0.0),
            )
            for row in rows
        )

        prefs = list(
            (
                await session.execute(
                    select(UserPreference).where(
                        UserPreference.user_id == user_id,
                        UserPreference.scope == "global",
                    )
                )
            ).scalars()
        )
        for row in prefs:
            value = row.value
            if isinstance(value, dict):
                value = value.get("value", value)
            found.append(
                Preference(
                    text=f"{row.key}: {value}",
                    kind="preference",
                    source=f"preference:{row.id}",
                )
            )
    except Exception as exc:
        # Not fatal — a run without personalisation still runs. But it is logged
        # rather than swallowed, because "no preferences" and "could not read the
        # preferences" must not look the same from the outside.
        logger.warning(
            "preference_load_failed", extra={"error": f"{type(exc).__name__}: {exc}"}
        )
    return found


__all__ = [
    "Preference",
    "PreferenceOutcome",
    "PreferenceResolution",
    "TaskConstraints",
    "derive_constraints",
    "load_preferences",
    "resolve_preferences",
]

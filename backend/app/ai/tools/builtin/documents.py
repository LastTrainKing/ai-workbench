"""Document generation and reading.

The agent could write code files and nothing else. This adds the formats people
actually hand to other people: Word, Excel, PowerPoint, PDF, plus markdown and
CSV.

Two rules shape the implementation, and both come from the same idea — that a
tool should not be able to lie about what it did:

**A missing library is a failure, not a fallback.** If `python-pptx` is not
installed, `document_write` returns an error naming the missing package. It does
not write a text file with a `.pptx` extension and report success.

**Generation is not completion.** Nothing here claims a document is correct. The
agent has to call `document_read` and check the page count, the row count, the
file size — the same rule as everywhere else in this codebase: the exit code
decides, not the model's opinion.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.ai.agent.tools import Tool, ToolContext
from app.ai.tools.permissions import default_output_path
from app.ai.types import ToolResult, ToolSpec
from app.core.errors import ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Refuse to write a document larger than this. Anything bigger is a mistake or
#: an accident, and either way it should not land silently in the workspace.
MAX_DOCUMENT_BYTES = 80_000_000
#: Ceilings on the structured input, so a runaway model cannot ask for a
#: 100 000-slide deck.
MAX_SLIDES = 200
MAX_ROWS = 20_000
MAX_SECTIONS = 400

#: Named styles, resolved from user preferences. The point of the product is that
#: the same request produces a different document for a different person.
STYLE_PRESETS: dict[str, dict[str, Any]] = {    "concise": {
        "slides": "few",
        "bullets_per_slide": 4,
        "words_per_bullet": 12,
        "font_scale": 1.0,
        "speaker_notes": False,
        "title_only_intro": True,
    },
    "academic": {
        "slides": "many",
        "bullets_per_slide": 6,
        "words_per_bullet": 24,
        "font_scale": 0.9,
        "speaker_notes": True,
        "title_only_intro": False,
    },
    "business": {
        "slides": "medium",
        "bullets_per_slide": 5,
        "words_per_bullet": 16,
        "font_scale": 1.0,
        "speaker_notes": True,
        "title_only_intro": True,
    },
}
DEFAULT_STYLE = "business"


def _resolve_style(name: str | None) -> tuple[str, dict[str, Any]]:
    key = (name or DEFAULT_STYLE).strip().lower()
    if key not in STYLE_PRESETS:
        key = DEFAULT_STYLE
    return key, STYLE_PRESETS[key]


def _require(module: str, package: str) -> Any:
    """Import a library or fail with an actionable message.

    The alternative — catching the ImportError and writing something else — is how
    a tool ends up reporting success for a file that will not open.
    """
    try:
        return __import__(module, fromlist=["_"])
    except ImportError as exc:
        raise ValidationError(
            f"生成该格式需要 {package}，当前环境没有安装。请先运行："
            f"pip install {package}。工具不会用其他格式替代并谎报成功。"
        ) from exc


class ChartRenderError(ValidationError):
    """A chart that was asked for and could not be drawn.

    This exception exists because the previous version returned ``None`` and logged
    a warning. That made "the chart failed" indistinguishable from "no chart was
    requested": the deck came out with zero images, the user's stated preference
    for more charts was silently not honoured, and every check still passed. A
    failure the caller cannot see is not a handled failure — it is a hidden one.
    """


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def _write_docx(
    path: Path, sections: list[dict[str, Any]], style: dict[str, Any], **_options: Any
) -> dict[str, Any]:
    docx = _require("docx", "python-docx")
    document = docx.Document()

    for section in sections[:MAX_SECTIONS]:
        heading = str(section.get("heading") or "").strip()
        level = int(section.get("level") or 1)
        if heading:
            document.add_heading(heading, level=max(1, min(level, 4)))
        body = section.get("content") or section.get("text") or ""
        if isinstance(body, list):
            for item in body:
                document.add_paragraph(str(item), style="List Bullet")
        else:
            for paragraph in str(body).split("\n\n"):
                if paragraph.strip():
                    document.add_paragraph(paragraph.strip())

    if not document.paragraphs:
        # A writer that recognises none of the caller's keys produces a valid but
        # empty document and reports success. That is how a 36 KB blank .docx gets
        # delivered as a finished report — the file opens, so nothing looks wrong
        # until someone reads it.
        raise ValidationError(
            "没有写入任何内容：sections 里没有可识别的字段。"
            "docx 使用 heading / content（或 text）。"
        )

    document.save(path)
    return {"paragraphs": len(document.paragraphs), "sections": len(sections)}


def _as_number(value: Any) -> float | None:
    """Coerce a chart value to a number, or None when it is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", "").replace("%", ""))
        except ValueError:
            return None
    return None


def normalise_chart(spec: dict[str, Any]) -> tuple[list[str], dict[str, list[float]]]:
    """Accept the shapes a model actually produces, or say clearly what is wrong.

    The first version required ``series`` to be ``{name: [values]}`` and the model
    consistently sent ``[{"name": ..., "data": [...]}]`` — which is the more natural
    reading of a field called `series`, and what every charting library in the world
    accepts. Every chart failed, the deck came out with no visuals, and the user's
    stated preference for more charts was not honoured.

    Failing loudly was correct and is why this was caught. Failing *only* because
    the caller guessed a private shape is not a useful contract, so both forms are
    accepted, along with the usual aliases, and anything genuinely unusable raises
    with the offending value quoted.
    """
    categories_raw = (
        spec.get("categories")
        or spec.get("labels")
        or spec.get("x")
        or spec.get("index")
        or []
    )
    if isinstance(categories_raw, dict):
        categories_raw = list(categories_raw.values())
    categories = [str(c) for c in categories_raw] if isinstance(categories_raw, (list, tuple)) else []

    raw_series = spec.get("series")
    if raw_series is None:
        raw_series = spec.get("data") or spec.get("values")
    if isinstance(raw_series, dict):
        raw_series = raw_series.get("data") if set(raw_series) == {"data"} else raw_series

    series: dict[str, list[float]] = {}

    def add(name: str, values: Any) -> None:
        if isinstance(values, dict):
            values = values.get("data") or values.get("values") or values.get("y")
        if not isinstance(values, (list, tuple)):
            raise ChartRenderError(
                f"chart 的系列「{name}」不是数组：{values!r}。"
                "series 应为 {名称: [数值]}，或 [{name, data}] 列表。"
            )
        numbers: list[float] = []
        for value in values:
            number = _as_number(value)
            if number is None:
                raise ChartRenderError(
                    f"chart 的系列「{name}」里有非数值：{value!r}。"
                    "图表只接受数字，请把文字说明放在标题或 categories 里。"
                )
            numbers.append(number)
        series[name] = numbers

    if isinstance(raw_series, dict):
        for name, values in raw_series.items():
            add(str(name), values)
    elif isinstance(raw_series, (list, tuple)):
        for index, item in enumerate(raw_series):
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("label") or f"系列{index + 1}")
                add(name, item.get("data") if "data" in item else item.get("values") or item.get("y") or item)
            elif isinstance(item, (list, tuple)):
                add(f"系列{index + 1}", item)
            else:
                # A flat list of numbers is one unnamed series.
                add("数值", raw_series)
                break
    else:
        raise ChartRenderError(
            f"chart 的 series 类型无法识别：{type(raw_series).__name__}。"
            "应为 {名称: [数值]} 或 [{name, data}]。"
        )

    if not categories:
        raise ChartRenderError(
            "chart 缺少 categories（横轴标签）。"
            f"收到的字段：{sorted(spec)}"
        )
    if not series:
        raise ChartRenderError("chart 的 series 是空的，画不出任何东西")

    for name, values in series.items():
        if len(values) != len(categories):
            raise ChartRenderError(
                f"chart 的系列「{name}」有 {len(values)} 个值，"
                f"但 categories 有 {len(categories)} 个 —— 数量必须一致"
            )

    return categories, series


def _render_chart(spec: dict[str, Any], target: Path) -> Path:
    """Draw a chart from a spec and return the file, or raise with the real reason.

    Raising rather than returning ``None``: a chart that was requested and did not
    appear is a failure of the request, and the caller has to be able to tell that
    apart from a section that simply had no chart. The previous version conflated
    the two and the result was a deck with zero visuals, a satisfied-looking
    result, and a user preference that had quietly not been applied.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ChartRenderError(
            "生成图表需要 matplotlib，当前环境没有安装。请先运行：pip install matplotlib。"
            "工具不会假装画了图。"
        ) from exc

    # A CJK font, or Chinese labels render as empty boxes.
    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft YaHei", "SimHei", "DejaVu Sans",
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False

    categories, series = normalise_chart(spec)

    kind = str(spec.get("type") or spec.get("kind") or "bar").lower()
    if kind not in {"bar", "line", "pie"}:
        # Not fatal: an unknown type is drawn as a bar chart, but it is reported so
        # "the chart is not what I asked for" is not a silent surprise.
        logger.warning("chart_type_unknown", extra={"type": kind})
        kind = "bar"

    try:
        figure, axes = plt.subplots(figsize=(7.2, 4.0), dpi=150)
        positions = range(len(categories))
        width = 0.8 / max(1, len(series))

        for index, (name, values) in enumerate(series.items()):
            offsets = [p + index * width for p in positions]
            if kind == "line":
                axes.plot(list(positions), list(values), marker="o", label=str(name))
            elif kind == "pie":
                axes.pie(list(values), labels=categories, autopct="%1.1f%%")
                break
            else:
                axes.bar(offsets, list(values), width=width, label=str(name))

        if kind != "pie":
            axes.set_xticks([p + width * (len(series) - 1) / 2 for p in positions])
            axes.set_xticklabels(categories, fontsize=9)
            if len(series) > 1:
                axes.legend(fontsize=9)
            axes.grid(axis="y", alpha=0.25)

        title = str(spec.get("title") or "")
        if title:
            axes.set_title(title, fontsize=12)
        figure.tight_layout()
        figure.savefig(target, bbox_inches="tight")
        plt.close(figure)
    except ChartRenderError:
        raise
    except Exception as exc:
        raise ChartRenderError(
            f"绘图失败（{type(exc).__name__}: {exc}）。图表类型 {kind}，"
            f"categories={categories[:8]}，series={list(series)[:8]}"
        ) from exc

    if not target.is_file() or target.stat().st_size == 0:
        raise ChartRenderError(f"绘图没有产生文件：{target}")
    return target


def _write_pptx(
    path: Path,
    sections: list[dict[str, Any]],
    style: dict[str, Any],
    *,
    min_slides: int = 0,
    max_slides: int = 0,
    **_options: Any,
) -> dict[str, Any]:
    pptx = _require("pptx", "python-pptx")
    presentation = pptx.Presentation()
    title_layout = presentation.slide_layouts[0]
    bullet_layout = presentation.slide_layouts[1]

    title_text = str(sections[0].get("heading") or sections[0].get("title") or "演示文稿") if sections else "演示文稿"
    cover = presentation.slides.add_slide(title_layout)
    cover.shapes.title.text = title_text
    if len(cover.placeholders) > 1:
        cover.placeholders[1].text = str(sections[0].get("content") or "")[:200]

    images_placed = 0
    chart_failures: list[dict[str, str]] = []
    limit = {"few": 8, "medium": 14, "many": 22}[style["slides"]]
    if max_slides:
        # A hard upper bound is a constraint, so the deck is cut to fit. Unlike the
        # lower bound this is recoverable inside the writer — but it is *reported*
        # rather than done quietly, because "your content was trimmed" is something
        # the caller has to know.
        limit = min(limit, max(1, max_slides - 1))
    body_sections = sections[1:] if len(sections) > 1 else sections
    per_slide = style["bullets_per_slide"]

    for section in body_sections[: min(limit, MAX_SLIDES)]:
        heading = str(section.get("heading") or section.get("title") or "").strip()
        body = section.get("content") or section.get("bullets") or ""
        bullets = body if isinstance(body, list) else [
            line.strip("-• \t") for line in str(body).split("\n") if line.strip()
        ]
        bullets = [str(b)[: style["words_per_bullet"] * 4] for b in bullets][:per_slide]

        slide = presentation.slides.add_slide(bullet_layout)
        slide.shapes.title.text = heading or "（无标题）"
        frame = slide.placeholders[1].text_frame
        frame.text = bullets[0] if bullets else ""
        for bullet in bullets[1:]:
            frame.add_paragraph().text = bullet

        if style["speaker_notes"]:
            slide.notes_slide.notes_text_frame.text = str(section.get("notes") or "")[:2000]

        # A visual, when the section carries one. Placed to the right of the text
        # rather than over it, so a chart never covers the bullets it illustrates.
        visual: Path | None = None
        raw_image = section.get("image")
        if raw_image:
            candidate = Path(str(raw_image))
            if candidate.is_file():
                visual = candidate
            else:
                # A named image that is not there is also a failure, not a no-op:
                # the slide was supposed to have a picture.
                chart_failures.append(
                    {"heading": heading or "（无标题）", "error": f"图片不存在：{candidate}"}
                )
        elif isinstance(section.get("chart"), dict):
            try:
                visual = _render_chart(
                    section["chart"],
                    target=path.parent / f"_chart_{path.stem}_{len(presentation.slides)}.png",
                )
            except ChartRenderError as exc:
                chart_failures.append({"heading": heading or "（无标题）", "error": str(exc)})
                logger.warning(
                    "slide_chart_failed",
                    extra={"heading": heading, "error": str(exc)},
                )

        if visual is not None and visual.is_file():
            try:
                from pptx.util import Inches

                slide.shapes.add_picture(
                    str(visual), Inches(4.7), Inches(1.7), width=Inches(4.8)
                )
                images_placed += 1
            except Exception as exc:
                chart_failures.append(
                    {"heading": heading or "（无标题）", "error": f"插入图片失败：{exc}"}
                )
                logger.warning(
                    "slide_image_failed", extra={"path": str(visual), "error": str(exc)}
                )

    total_slides = len(presentation.slides)
    dropped = max(0, len(body_sections) - min(limit, MAX_SLIDES))

    if min_slides and total_slides < min_slides:
        # The one thing a writer cannot fix by itself is content that was never
        # supplied. Failing here — loudly, and without leaving a file behind —
        # is what stops a 2-slide deck being delivered for a 15-minute talk.
        path.unlink(missing_ok=True)
        raise ValidationError(
            f"页数不达标：硬约束要求至少 {min_slides} 页，实际只能生成 {total_slides} 页。"
            f"收到 {len(sections)} 个 section，需要至少 {min_slides} 个"
            "（含 1 个标题页）。请补充内容后重写；不要用页数不足的文件充当交付物。"
        )

    presentation.save(path)
    # `images` is reported rather than assumed: a deck that came out with none should
    # say so, because that is exactly what the user complained about.
    return {
        "slides": total_slides,
        "images": images_placed,
        "chart_failures": chart_failures,
        "slides_dropped": dropped,
        "min_slides": min_slides,
        "max_slides": max_slides,
    }


def _write_xlsx(
    path: Path, sections: list[dict[str, Any]], style: dict[str, Any], **_options: Any
) -> dict[str, Any]:
    openpyxl = _require("openpyxl", "openpyxl")
    workbook = openpyxl.Workbook()
    default = workbook.active
    default.title = "Sheet1"
    written_sheets = 0
    total_rows = 0

    for index, section in enumerate(sections[:20]):
        rows = section.get("rows") or section.get("data") or []
        sheet = default if index == 0 else workbook.create_sheet()
        if section.get("heading"):
            sheet.title = str(section["heading"])[:31]
        if not isinstance(rows, list):
            continue
        for row in rows[:MAX_ROWS]:
            if isinstance(row, dict):
                sheet.append([row.get(k) for k in row])
            elif isinstance(row, (list, tuple)):
                sheet.append(list(row))
            else:
                sheet.append([row])
            total_rows += 1
        # A formula the caller supplied is written verbatim, which is the only
        # way to produce a workbook whose numbers actually recalculate.
        for formula in section.get("formulas") or []:
            cell = str(formula.get("cell") or "")
            expression = str(formula.get("formula") or "")
            if cell and expression:
                sheet[cell] = expression
        if section.get("chart") and total_rows:
            chart_spec = section["chart"]
            from openpyxl.chart import BarChart, LineChart, PieChart, Reference

            kinds = {"bar": BarChart, "line": LineChart, "pie": PieChart}
            chart = kinds.get(str(chart_spec.get("type") or "bar"), BarChart)()
            chart.title = str(chart_spec.get("title") or section.get("heading") or "Chart")
            last_col = max(1, len(rows[0]) if rows and isinstance(rows[0], (list, tuple)) else 1)
            data = Reference(sheet, min_col=1, min_row=1, max_col=last_col, max_row=min(total_rows, 50))
            chart.add_data(data, titles_from_data=True)
            sheet.add_chart(chart, str(chart_spec.get("anchor") or "H2"))
        written_sheets += 1

    workbook.save(path)
    return {"sheets": len(workbook.sheetnames), "rows": total_rows}


def _write_pdf(
    path: Path, sections: list[dict[str, Any]], style: dict[str, Any], **_options: Any
) -> dict[str, Any]:
    _require("reportlab", "reportlab")
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    document = SimpleDocTemplate(str(path), pagesize=A4)
    flow: list[Any] = []
    for section in sections[:MAX_SECTIONS]:
        if section.get("heading"):
            flow.append(Paragraph(str(section["heading"]), styles["Heading1"]))
            flow.append(Spacer(1, 8))
        body = section.get("content") or ""
        for chunk in (body if isinstance(body, list) else str(body).split("\n\n")):
            text = str(chunk).strip()
            if text:
                flow.append(Paragraph(text.replace("\n", "<br/>"), styles["BodyText"]))
                flow.append(Spacer(1, 6))
        if section.get("page_break"):
            flow.append(PageBreak())
    document.build(flow)
    return {"sections": len(sections)}


def _write_md(
    path: Path, sections: list[dict[str, Any]], style: dict[str, Any], **_options: Any
) -> dict[str, Any]:
    lines: list[str] = []
    for section in sections[:MAX_SECTIONS]:
        if section.get("heading"):
            level = max(1, min(int(section.get("level") or 1), 6))
            lines.append(f"{'#' * level} {section['heading']}")
            lines.append("")
        body = section.get("content") or ""
        if isinstance(body, list):
            lines.extend(f"- {item}" for item in body)
        else:
            lines.append(str(body))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"sections": len(sections), "lines": len(lines)}


def _write_csv(
    path: Path, sections: list[dict[str, Any]], style: dict[str, Any], **_options: Any
) -> dict[str, Any]:
    rows: list[Any] = []
    for section in sections[:20]:
        data = section.get("rows") or section.get("data") or []
        if isinstance(data, list):
            rows.extend(data)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        written = 0
        for row in rows[:MAX_ROWS]:
            if isinstance(row, dict):
                writer.writerow(list(row.values()))
            elif isinstance(row, (list, tuple)):
                writer.writerow(list(row))
            else:
                writer.writerow([row])
            written += 1
    return {"rows": written}


WRITERS = {
    "docx": _write_docx,
    "pptx": _write_pptx,
    "xlsx": _write_xlsx,
    "pdf": _write_pdf,
    "md": _write_md,
    "csv": _write_csv,
}


# --------------------------------------------------------------------------- #
# document_write
# --------------------------------------------------------------------------- #
class DocumentWriteTool(Tool):
    spec = ToolSpec(
        name="document_write",
        description=(
            "生成文档并写入工作区。支持 docx / xlsx / pptx / pdf / md / csv。"
            "风格会参考用户偏好（简洁 / 学术 / 商务）。"
            "pptx 可传 min_slides / max_slides 强制页数区间（来自任务的时长硬约束）；"
            "section 里带 chart（{type,title,categories,series}）会生成图表。"
            "注意：生成成功不代表内容正确，请随后用 document_read 回读校验页数、行列数等。"
        ),
        category="filesystem",
        parameters={
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": list(WRITERS)},
                "path": {"type": "string", "description": "相对于工作区的输出路径"},
                "sections": {
                    "type": "array",
                    "description": (
                        "结构化内容数组。每项可含：\n"
                        "- heading：标题（pptx 的页标题 / docx 的标题 / xlsx 的工作表名）\n"
                        "- content：正文；字符串，或字符串数组（每个元素是一行/一条要点）\n"
                        "- rows / data：表格数据，二维数组或对象数组\n"
                        "- formulas：[{cell: \"B7\", formula: \"=SUM(B2:B6)\"}]，原样写入\n"
                        "- chart：图表，形如 "
                        "{\"type\": \"bar|line|pie\", \"title\": \"...\", "
                        "\"categories\": [\"A\",\"B\"], "
                        "\"series\": [{\"name\": \"系列名\", \"data\": [1,2]}]}；"
                        "series 也接受 {\"系列名\": [1,2]} 形式。"
                        "categories 与每个 series 的长度必须一致，值必须是数字。\n"
                        "- notes：讲稿备注（仅 pptx）"
                    ),
                },
                "style": {"type": "string", "description": "concise / academic / business，留空则读用户偏好"},
                "min_slides": {
                    "type": "integer",
                    "description": "仅 pptx：硬约束要求的最少页数，不足会直接失败而不是交出不足页数的文件",
                },
                "max_slides": {
                    "type": "integer",
                    "description": "仅 pptx：硬约束允许的最多页数，超出会被裁剪并在结果里报告",
                },
            },
            "required": ["format", "path", "sections"],
        },
        permission_level=1,
        risk_level="low",
        mutates_state=True,
        timeout_seconds=120,
    )

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        fmt = str(kwargs.get("format") or "").strip().lower()
        if fmt not in WRITERS:
            raise ValidationError(f"不支持的格式：{fmt}。可选：{', '.join(WRITERS)}")

        sections = kwargs.get("sections")
        if isinstance(sections, str):
            try:
                sections = json.loads(sections)
            except json.JSONDecodeError as exc:
                raise ValidationError("sections 不是合法的 JSON 数组") from exc
        if not isinstance(sections, list) or not sections:
            raise ValidationError("sections 必须是非空数组")

        target = Path(
            ctx.resolve(default_output_path(str(kwargs.get("path") or f"document.{fmt}")))
        )
        ctx.require_permission(target, write=True)
        target.parent.mkdir(parents=True, exist_ok=True)

        style_name, style = _resolve_style(kwargs.get("style") or await _preferred_style(ctx))
        writer = WRITERS[fmt]

        options: dict[str, Any] = {}
        if fmt == "pptx":
            options["min_slides"] = _as_int(kwargs.get("min_slides"))
            options["max_slides"] = _as_int(kwargs.get("max_slides"))

        stats = writer(target, sections, style, **options)

        size = target.stat().st_size
        if size > MAX_DOCUMENT_BYTES:
            target.unlink(missing_ok=True)
            raise ValidationError(f"生成的文档超过上限（{size} 字节）")

        # A chart that was requested and did not appear is a failure of the request,
        # not a cosmetic detail. Returning `ok=True` here is what let a deck with
        # zero visuals pass every check while the user's "更多图表" preference went
        # silently unapplied.
        failures = stats.get("chart_failures") or []
        if failures:
            detail = "；".join(
                f"{item.get('heading')}：{item.get('error')}" for item in failures[:4]
            )
            return ToolResult(
                ok=False,
                data={
                    "path": str(target),
                    "format": fmt,
                    "style": style_name,
                    "size_bytes": size,
                    **stats,
                    "verified": False,
                },
                error=(
                    f"有 {len(failures)} 个图表没有生成：{detail}。"
                    "文件已写出但不完整，不能算完成；请修正 chart 的 categories / series 后重写。"
                ),
                summary=f"partial {target.name} ({len(failures)} charts failed)",
            )

        return ToolResult(
            ok=True,
            data={
                "path": str(target),
                "format": fmt,
                "style": style_name,
                "size_bytes": size,
                **stats,
                "verified": False,
                "next_step": "调用 document_read 回读校验，不要把「已生成」当作完成。",
            },
            summary=f"created {target.name} ({fmt}, {size} bytes, style={style_name})",
        )


def _as_int(value: Any) -> int:
    """Coerce an optional integer parameter, treating anything unusable as absent."""
    if value is None or value == "":
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


async def _preferred_style(ctx: ToolContext) -> str | None:
    """Read the user's document style preference, if there is one.

    This is what makes the same request produce a different document for a
    different person. A failure to read it is not an error — the default applies.
    """
    if ctx.session is None or ctx.owner_id is None:
        return None
    try:
        from sqlalchemy import select

        from app.models.context import UserPreference

        row = (
            await ctx.session.execute(
                select(UserPreference).where(
                    UserPreference.user_id == ctx.owner_id,
                    UserPreference.key.in_(("document_style", "ppt_style", "output_style")),
                )
            )
        ).scalars().first()
        if row is None:
            return None
        value = row.value
        if isinstance(value, dict):
            value = value.get("value")
        return str(value) if value else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# document_read
# --------------------------------------------------------------------------- #
class DocumentReadTool(Tool):
    spec = ToolSpec(
        name="document_read",
        description=(
            "读取文档并返回结构化内容，用于校验生成结果或把文档送进知识库。"
            "支持 docx / xlsx / pptx / pdf / md / txt / csv。"
        ),
        category="filesystem",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_chars": {"type": "integer", "description": "返回正文的最大字符数，默认 8000"},
            },
            "required": ["path"],
        },
        permission_level=0,
        risk_level="low",
        mutates_state=False,
        timeout_seconds=60,
    )

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> ToolResult:
        target = Path(ctx.resolve(str(kwargs.get("path") or "")))
        ctx.require_permission(target, write=False)
        if not target.is_file():
            raise ValidationError(f"文件不存在：{target}")

        limit = int(kwargs.get("max_chars") or 8000)
        suffix = target.suffix.lower()

        if suffix == ".docx":
            payload = _read_docx(target)
        elif suffix == ".xlsx":
            payload = _read_xlsx(target)
        elif suffix == ".pptx":
            payload = _read_pptx(target)
        elif suffix == ".csv":
            payload = _read_csv(target)
        elif suffix == ".pdf":
            payload = _read_pdf(target)
        else:
            payload = {
                "kind": "text",
                "text": target.read_text(encoding="utf-8", errors="replace"),
            }

        text = str(payload.get("text") or "")[:limit]
        payload["text"] = text
        payload["path"] = str(target)
        payload["size_bytes"] = target.stat().st_size
        payload["truncated"] = len(str(payload.get("text") or "")) >= limit

        return ToolResult(
            ok=True,
            data=payload,
            summary=f"read {target.name} ({payload.get('kind')}, {payload['size_bytes']} bytes)",
        )


def _read_docx(path: Path) -> dict[str, Any]:
    docx = _require("docx", "python-docx")
    document = docx.Document(str(path))
    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    return {
        "kind": "docx",
        "paragraphs": len(document.paragraphs),
        "tables": len(document.tables),
        "text": "\n".join(paragraphs),
    }


def _read_xlsx(path: Path) -> dict[str, Any]:
    openpyxl = _require("openpyxl", "openpyxl")
    workbook = openpyxl.load_workbook(str(path), data_only=False)
    sheets = []
    lines: list[str] = []
    for name in workbook.sheetnames:
        sheet = workbook[name]
        rows = [[cell.value for cell in row] for row in sheet.iter_rows()]
        sheets.append({"name": name, "rows": sheet.max_row, "columns": sheet.max_column})
        lines.append(f"[{name}]")
        lines.extend("\t".join("" if v is None else str(v) for v in row) for row in rows[:200])
    return {
        "kind": "xlsx",
        "sheets": sheets,
        "sheet_count": len(sheets),
        "charts": sum(len(workbook[n]._charts) for n in workbook.sheetnames),
        "text": "\n".join(lines),
    }


def _read_pptx(path: Path) -> dict[str, Any]:
    pptx = _require("pptx", "python-pptx")
    presentation = pptx.Presentation(str(path))
    slides = []
    lines: list[str] = []
    for index, slide in enumerate(presentation.slides, start=1):
        texts = [
            shape.text_frame.text
            for shape in slide.shapes
            if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip()
        ]
        notes = ""
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text
        slides.append({"index": index, "texts": texts, "has_notes": bool(notes.strip())})
        lines.append(f"[第 {index} 页] " + " | ".join(texts))
        if notes.strip():
            lines.append(f"  讲稿：{notes[:400]}")
    return {
        "kind": "pptx",
        "slide_count": len(presentation.slides),
        "slides": slides,
        "text": "\n".join(lines),
    }


def _read_csv(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        rows = list(csv.reader(handle))
    return {
        "kind": "csv",
        "row_count": len(rows),
        "columns": len(rows[0]) if rows else 0,
        "text": "\n".join("\t".join(row) for row in rows[:500]),
    }


def _read_pdf(path: Path) -> dict[str, Any]:
    """Read a PDF's text layer.

    A scanned PDF has no text layer. That is reported as such rather than
    returned as an empty document, because \"the file has no extractable text\"
    and \"the file is empty\" are different facts and only one of them is true.
    """
    pypdf = _require("pypdf", "pypdf")
    reader = pypdf.PdfReader(str(path))
    pages = [(page.extract_text() or "") for page in reader.pages]
    text = "\n".join(pages).strip()
    payload: dict[str, Any] = {
        "kind": "pdf",
        "page_count": len(reader.pages),
        "text": text,
    }
    if not text:
        payload["note"] = "这份 PDF 没有可提取的文本层（可能是扫描件）。当前版本不支持 OCR。"
    return payload


__all__ = [
    "STYLE_PRESETS",
    "WRITERS",
    "ChartRenderError",
    "DocumentReadTool",
    "DocumentWriteTool",
]

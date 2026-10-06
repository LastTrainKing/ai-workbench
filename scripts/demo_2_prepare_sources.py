"""Step 2 — three real materials, with real content.

A demo built on 18-byte files proves nothing. These are assembled from the project's
own thesis chapters, which are genuine technical prose with hundreds of lines each —
the kind of material a person would actually want turned into a deck.

    demo1-sources/系统总体设计.md     markdown, two chapters
    demo1-sources/AI模块设计.pdf      pdf, converted from a chapter
    demo1-sources/系统测试记录.txt    plain text

Run:  python scripts/demo_2_prepare_sources.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THESIS = ROOT / "docs" / "thesis"
OUT = ROOT / "samples" / "demo1-sources"


def read(name: str) -> str:
    return (THESIS / name).read_text(encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- markdown --
    markdown = (
        "# AI Workbench 系统总体设计与数据库设计\n\n"
        + read("04-系统总体设计.md")
        + "\n\n"
        + read("05-数据库设计.md")
    )
    md_path = OUT / "系统总体设计.md"
    md_path.write_text(markdown, encoding="utf-8")
    print(f"  MD   {md_path.name}  {len(markdown.splitlines())} 行  {md_path.stat().st_size:,} bytes")

    # ------------------------------------------------------------------ txt --
    text = read("09-系统测试.md") + "\n\n" + read("08-系统实现.md")
    # Plain text, not markdown: strip the heading markers so it reads as a record.
    plain = "\n".join(
        line.lstrip("#").strip() if line.startswith("#") else line for line in text.splitlines()
    )
    txt_path = OUT / "系统测试记录.txt"
    txt_path.write_text(plain, encoding="utf-8")
    print(f"  TXT  {txt_path.name}  {len(plain.splitlines())} 行  {txt_path.stat().st_size:,} bytes")

    # ------------------------------------------------------------------ pdf --
    pdf_path = OUT / "AI模块设计.pdf"
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

        # A CJK font, or the Chinese renders as blank boxes — a PDF that opens and
        # says nothing.
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        styles = getSampleStyleSheet()
        body = ParagraphStyle("body", parent=styles["Normal"], fontName="STSong-Light", fontSize=10.5, leading=16)
        heading = ParagraphStyle(
            "heading", parent=body, fontSize=14, leading=22, spaceBefore=10, spaceAfter=6
        )

        flow = []
        for raw in read("07-AI模块设计.md").splitlines():
            line = raw.strip()
            if not line:
                flow.append(Spacer(1, 4))
                continue
            if line.startswith("#"):
                text_line = line.lstrip("#").strip()
                if text_line:
                    flow.append(Paragraph(text_line, heading))
                continue
            # Escape the XML-significant characters Paragraph treats as markup.
            safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            flow.append(Paragraph(safe, body))

        SimpleDocTemplate(
            str(pdf_path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
            topMargin=18 * mm, bottomMargin=18 * mm, title="AI 模块设计",
        ).build(flow)
        print(f"  PDF  {pdf_path.name}  {pdf_path.stat().st_size:,} bytes")
    except Exception as exc:  # noqa: BLE001 - report it rather than pretend
        print(f"  PDF 失败：{type(exc).__name__}: {exc}")
        return 1

    # --------------------------------------------------------------- verify --
    print()
    print("  独立核验：")
    import pypdf

    reader = pypdf.PdfReader(str(pdf_path))
    text_out = "".join(page.extract_text() or "" for page in reader.pages)
    print(f"    PDF 页数 {len(reader.pages)}，提取文本 {len(text_out)} 字符")
    print(f"    含中文: {any('\u4e00' <= c <= '\u9fff' for c in text_out)}")
    print(f"    MD 行数 {len(markdown.splitlines())}，TXT 行数 {len(plain.splitlines())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

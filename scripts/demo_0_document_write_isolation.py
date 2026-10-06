"""Step 1 — is `document_write` actually usable from the agent's tool chain?

Before building a demo on top of it, confirm the tool itself works for the three
formats the product promises. If this fails, the demo would spend its time debugging
a tool-level problem, and the failure would look like an agent problem.

Each format is written, then read back, then opened independently — because "the tool
returned ok" and "the file is a real document" are different claims.

Run:  python scripts/demo_0_document_write_isolation.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

WORKSPACE = Path(r"C:\Users\user\Desktop\AI Workbench Workspace")
OUT = WORKSPACE / "demo" / "isolated"

PPTX_SECTIONS = [
    {
        "title": "系统架构",
        "bullets": [
            "分层设计：API / 服务 / 仓储 / 模型四层，边界由依赖方向保证",
            "Agent 不直接访问数据库，只能调用工具",
            "工具调用经过权限管理器，每次决策写入审计表",
        ],
    },
    {
        "title": "数据库设计",
        "bullets": [
            "31 张表，覆盖用户、知识、任务、AI 运行与个人上下文",
            "概念抽取形成 document_concepts 关联，支撑知识图谱",
            "迁移由 Alembic 管理，每次变更可回溯",
        ],
    },
    {
        "title": "AI 模块",
        "bullets": [
            "RAG：改写 → 混合检索 → 词法重排 → 预算内上下文组装",
            "自主 Agent：PLAN-EXECUTE-OBSERVE-VERIFY-FIX-RETRY",
            "交付层：BuildRun 驱动，产出物经机器校验后等待用户验收",
        ],
    },
]

DOCX_SECTIONS = [
    {
        "heading": "一、系统概述",
        "content": "本系统是一个本地优先的个人 AI 工作台，把用户导入的资料转化为可交付的成果。"
        "系统运行在 Windows 桌面环境，所有个人数据保留在本机。",
    },
    {
        "heading": "二、架构设计",
        "content": "后端采用分层架构，Agent 通过工具层与业务系统交互，不直接操作数据库。"
        "这一约束使每次操作都可命名、可分类、可中断，审计记录因此具备意义。",
    },
    {
        "heading": "三、验证机制",
        "content": "生成文件不等于完成。产出物必须能被读回并核对页数、行列数或字符数，"
        "验证结果以结构化证据保存，而不是一句结论。",
    },
]

XLSX_SECTIONS = [
    {
        "heading": "工具统计",
        "rows": [
            ["模块", "工具数", "权限级别"],
            ["文件系统", 12, 1],
            ["代码操作", 7, 2],
            ["Git", 6, 2],
            ["终端", 2, 3],
            ["文档", 2, 1],
        ],
        # Written verbatim so the workbook actually recalculates, rather than
        # carrying a number that only looks like a result.
        "formulas": [{"cell": "B7", "formula": "=SUM(B2:B6)"}],
        "chart": {"type": "bar", "title": "各模块工具数", "anchor": "E2"},
    }
]


async def main() -> int:
    from sqlalchemy import select

    from app.ai.agent.tools import ToolContext
    from app.ai.tools.builtin.documents import DocumentReadTool, DocumentWriteTool
    from app.ai.tools.permissions import AgentPolicy, PermissionManager
    from app.infrastructure.database import session_scope
    from app.models.user import User

    OUT.mkdir(parents=True, exist_ok=True)
    policy = AgentPolicy.from_settings(level=2)

    # Built the same way the agent builds it, including the permission manager — so
    # this exercises the real path rather than a simplified one. A tool that works
    # outside the sandbox and fails inside it is not working.
    async with session_scope() as session:
        user = (await session.execute(select(User).limit(1))).scalar_one()
        ctx = ToolContext(
            session=session,
            owner_id=user.id,
            run_id="isolation-check",
            permissions=PermissionManager(policy),
            working_directory=str(WORKSPACE),
        )
        return await _run_checks(ctx, OUT)


async def _run_checks(ctx: object, OUT: Path) -> int:
    from app.ai.tools.builtin.documents import DocumentReadTool, DocumentWriteTool

    writer = DocumentWriteTool()
    reader = DocumentReadTool()

    failures = 0

    # ------------------------------------------------------------------ pptx --
    print("=" * 74)
    print("  PPTX")
    print("=" * 74)
    pptx_path = OUT / "isolated.pptx"
    result = await writer.execute(
        ctx,
        format="pptx",
        path=str(pptx_path),
        title="AI Workbench 技术方案",
        sections=PPTX_SECTIONS,
    )
    print(f"  write ok={result.ok}  {result.summary}")
    if not result.ok:
        print(f"  error: {result.error}")
        failures += 1
    else:
        print(f"  size: {pptx_path.stat().st_size:,} bytes")
        back = await reader.execute(ctx, path=str(pptx_path))
        print(f"  readback ok={back.ok}")
        if back.ok:
            data = back.data or {}
            print(f"  slides: {data.get('slide_count')}")
            print(f"  first title: {str(data.get('titles', ['?'])[0])[:50]}")
        from pptx import Presentation

        deck = Presentation(str(pptx_path))
        print(f"  python-pptx 打开: {len(deck.slides)} 页")

    # ------------------------------------------------------------------ docx --
    print()
    print("=" * 74)
    print("  DOCX")
    print("=" * 74)
    docx_path = OUT / "isolated.docx"
    result = await writer.execute(
        ctx,
        format="docx",
        path=str(docx_path),
        title="AI Workbench 系统设计说明",
        sections=DOCX_SECTIONS,
    )
    print(f"  write ok={result.ok}  {result.summary}")
    if not result.ok:
        print(f"  error: {result.error}")
        failures += 1
    else:
        print(f"  size: {docx_path.stat().st_size:,} bytes")
        back = await reader.execute(ctx, path=str(docx_path))
        print(f"  readback ok={back.ok}")
        if back.ok:
            data = back.data or {}
            print(f"  paragraphs: {data.get('paragraphs')}")
            print(f"  chars: {data.get('char_count')}")
        from docx import Document

        document = Document(str(docx_path))
        headings = [p.text for p in document.paragraphs if p.style.name.startswith("Heading")]
        print(f"  python-docx 打开: {len(document.paragraphs)} 段, 标题 {len(headings)} 个")
        for heading in headings[:4]:
            print(f"    · {heading}")

    # ------------------------------------------------------------------ xlsx --
    print()
    print("=" * 74)
    print("  XLSX（必须含真实公式与图表）")
    print("=" * 74)
    xlsx_path = OUT / "isolated.xlsx"
    result = await writer.execute(
        ctx,
        format="xlsx",
        path=str(xlsx_path),
        title="工具分布统计",
        sections=XLSX_SECTIONS,
    )
    print(f"  write ok={result.ok}  {result.summary}")
    if not result.ok:
        print(f"  error: {result.error}")
        failures += 1
    else:
        print(f"  size: {xlsx_path.stat().st_size:,} bytes")
        from openpyxl import load_workbook

        book = load_workbook(str(xlsx_path))
        sheet = book.active
        print(f"  openpyxl 打开: {sheet.max_row} 行 × {sheet.max_column} 列")
        formulas = [
            (cell.coordinate, cell.value)
            for row in sheet.iter_rows()
            for cell in row
            if isinstance(cell.value, str) and cell.value.startswith("=")
        ]
        print(f"  真实公式 {len(formulas)} 个:")
        for coordinate, formula in formulas[:4]:
            print(f"    {coordinate} = {formula}")
        charts = len(getattr(sheet, "_charts", []))
        print(f"  图表 {charts} 个")
        back = await reader.execute(ctx, path=str(xlsx_path))
        print(f"  readback ok={back.ok}")
        if back.ok:
            print(f"  sheets: {(back.data or {}).get('sheet_count')}")

    # ---------------------------------------------------------------- result --
    print()
    print("=" * 74)
    if failures:
        print(f"  {failures} 个格式失败 —— 先修工具，不要往下做 Demo")
    else:
        print("  三种格式全部通过：生成 / 读回 / 独立打开")
    print("=" * 74)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

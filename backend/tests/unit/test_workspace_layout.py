"""The agent's workspace: where it is, what shape it has, and where output lands.

The problem this pins down was not tidiness. The workspace defaulted to
``%USERPROFILE%\\Desktop\\AI Workbench Workspace`` — the product creating its own
working directory at the root of the user's desktop — and inside it were 1,864
files belonging to a Python virtualenv, because nothing had ever decided what the
area was for. So these tests assert three things: the default is inside the
product, the layout is fixed, and a deliverable does not land wherever the model
happened to point.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.agent.tools import ToolContext
from app.ai.tools.builtin.documents import DocumentWriteTool
from app.ai.tools.permissions import (
    DELIVERABLES_DIR,
    WORKSPACE_DIRS,
    AgentPolicy,
    PermissionManager,
    default_output_path,
    default_workspace,
    ensure_workspace_layout,
    project_root,
)


def test_the_workspace_is_not_on_the_users_desktop():
    workspace = default_workspace()
    assert workspace == project_root() / ".runtime" / "workspace"
    # The project itself may sit on the desktop; what must not happen is the
    # workspace being a sibling of it, created directly on the desktop root.
    assert workspace.parent.name != "AI Workbench Workspace"
    assert Path.home() / "Desktop" / "AI Workbench Workspace" != workspace


def test_the_workspace_lives_inside_the_project():
    workspace = default_workspace()
    assert project_root() in workspace.parents


def test_the_layout_is_the_documented_one():
    assert WORKSPACE_DIRS == ("deliverables", "notes", "sources", ".tmp")
    assert DELIVERABLES_DIR == "deliverables"


def test_ensure_layout_creates_every_directory(tmp_path):
    ensure_workspace_layout(tmp_path)
    for name in WORKSPACE_DIRS:
        assert (tmp_path / name).is_dir()
    # Idempotent: a second call must not raise or change anything.
    ensure_workspace_layout(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(WORKSPACE_DIRS)


def test_a_bare_filename_is_routed_into_deliverables():
    assert default_output_path("答辩PPT.pptx") == "deliverables/答辩PPT.pptx"
    assert default_output_path("report.docx") == "deliverables/report.docx"


def test_an_explicit_directory_is_respected():
    assert default_output_path("notes/draft.md") == "notes/draft.md"
    assert default_output_path("sources/raw.csv") == "sources/raw.csv"
    assert default_output_path(r"notes\draft.md") == r"notes\draft.md"


def test_an_absolute_path_is_left_alone():
    absolute = str(Path("C:/somewhere/else/out.pptx")) if Path("C:/").exists() else "/tmp/out.pptx"
    assert default_output_path(absolute) == absolute


def _context(workspace: Path) -> ToolContext:
    policy = AgentPolicy.from_settings(level=2, workspace=workspace)
    return ToolContext(
        session=None,  # type: ignore[arg-type]
        owner_id=None,  # type: ignore[arg-type]
        run_id="workspace-check",
        permissions=PermissionManager(policy),
        working_directory=str(workspace),
    )


@pytest.mark.asyncio
async def test_document_write_puts_a_bare_filename_in_deliverables(tmp_path):
    """The path that used to put `答辩PPT.pptx` on the desktop."""
    ctx = _context(tmp_path)
    result = await DocumentWriteTool().execute(
        ctx,
        format="pptx",
        path="答辩PPT.pptx",
        sections=[{"heading": "封面", "content": ["一"]}, {"heading": "内容", "content": ["二"]}],
    )
    assert result.ok, result.error
    written = Path(result.data["path"])
    assert written.parent.name == DELIVERABLES_DIR
    assert written.is_file()
    assert not (tmp_path / "答辩PPT.pptx").exists()


@pytest.mark.asyncio
async def test_document_write_honours_an_explicit_directory(tmp_path):
    ctx = _context(tmp_path)
    result = await DocumentWriteTool().execute(
        ctx,
        format="md",
        path="notes/draft.md",
        sections=[{"heading": "草稿", "content": "内容"}],
    )
    assert result.ok, result.error
    assert Path(result.data["path"]).parent.name == "notes"


@pytest.mark.asyncio
async def test_the_workspace_is_reported_in_the_tool_result(tmp_path):
    """So "where did it put my file?" is answerable from the result alone."""
    ctx = _context(tmp_path)
    result = await DocumentWriteTool().execute(
        ctx,
        format="md",
        path="deliverables/note.md",
        sections=[{"heading": "标题", "content": "正文"}],
    )
    assert result.ok
    assert str(tmp_path) in result.data["path"]

"""Auto-ingestion: scanning, classification, near-duplicate detection, gap proposals.

The acceptance item is "drop in a folder of 5+ real files and it files them correctly with
no manual step". So the fixtures here are a real folder written to disk and walked, not a
list assembled in memory — a scanner tested against a list proves the list works.

Every threshold is asserted from both sides. A duplicate detector that calls everything a
duplicate and one that calls nothing a duplicate both pass a one-sided test.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import Document, DocumentChunk, KnowledgeBase
from app.models.user import User
from app.services.knowledge_ingest import (
    DUPLICATE_THRESHOLD,
    classify,
    detect_gaps,
    find_near_duplicates,
    overlap_ratio,
    scan_folder,
    shingles,
)


async def make_user(session: AsyncSession) -> uuid.UUID:
    user = User(
        email=f"ingest_{uuid.uuid4().hex[:10]}@aiworkbench.dev",
        username=f"ingest_{uuid.uuid4().hex[:10]}",
        display_name="Ingest Test",
        hashed_password="unused",
    )
    session.add(user)
    await session.flush()
    return user.id


# ------------------------------------------------------------------ the real folder --

THESIS_FILES = {
    "开题报告.md": (
        "毕业设计开题报告\n\n"
        "本课题研究基于深度学习的图像分割方法，用于医学影像的病灶识别。"
        "毕业设计的目标是完成一个可以运行的原型系统，并撰写毕业论文。"
        "论文需要包含文献综述、方法设计、实验与结论。"
    ),
    "文献综述.md": (
        "文献综述\n\n"
        "深度学习在医学影像分割领域的进展。综述了 U-Net、Mask R-CNN 等方法的"
        "优缺点，并讨论了小样本条件下的训练策略。毕业论文的文献综述部分需要"
        "覆盖近五年的工作。"
    ),
    "实验记录.md": (
        "实验记录\n\n"
        "第一次实验：batch size 8，学习率 1e-4，Dice 系数 0.71。\n"
        "第二次实验：加入数据增强后 Dice 系数提升到 0.78。"
    ),
    "进度安排.md": (
        "毕业设计进度安排\n\n"
        "三月：完成文献综述。四月：完成方法设计与初步实验。"
        "五月：撰写毕业论文初稿。六月：答辩。"
    ),
    "答辩要点.md": (
        "答辩要点\n\n"
        "评委常问的问题：为什么选这个网络结构、数据集有多大、"
        "和创新点在哪里。毕业论文答辩时要准备演示。"
    ),
}

RECIPE_FILES = {
    "番茄炒蛋.md": "番茄炒蛋的做法。先打蛋，热油下锅，炒散盛出。再炒番茄出汁，回锅翻匀即可。",
    "红烧肉.md": "红烧肉的做法。五花肉切块焯水，炒糖色，加酱油和料酒慢炖一小时。",
}


@pytest.fixture
def thesis_folder(tmp_path: Path) -> Path:
    """A real folder on disk, in the shape a user would actually drop in."""
    folder = tmp_path / "毕业设计"
    folder.mkdir()
    for name, body in THESIS_FILES.items():
        (folder / name).write_text(body, encoding="utf-8")
    # Things a real folder contains and this must not read.
    (folder / ".DS_Store").write_text("junk", encoding="utf-8")
    (folder / "node_modules").mkdir()
    (folder / "node_modules" / "package.json").write_text("{}", encoding="utf-8")
    (folder / "data.bin").write_bytes(b"\x00\x01\x02")
    return folder


def test_scans_a_real_folder_and_reports_what_it_skipped(thesis_folder: Path) -> None:
    """Skipping has to be visible. A folder of 9 files where 3 were ignored is a fact the
    user needs, and a silent skip looks identical to a successful read."""
    found = scan_folder(thesis_folder)
    names = {f.path.name for f in found}

    assert set(THESIS_FILES) <= names, "五个真实文件都要被扫到"
    assert ".DS_Store" not in names, "隐藏文件不读"
    assert "package.json" not in names, "node_modules 不读"

    unsupported = [f for f in found if not f.supported]
    assert unsupported, "data.bin 应该被标记为不支持，而不是消失"
    assert all(f.reason for f in unsupported), "跳过必须有理由"


def test_five_real_files_classify_into_the_right_base() -> None:
    """The whole point: no manual step. Every thesis file goes to 毕业设计 and the
    recipes do not."""
    bases = [
        ("kb-thesis", "毕业设计", "毕业论文、开题、文献综述、实验、答辩"),
        ("kb-cook", "菜谱", "家常菜的做法与食材"),
    ]
    profile = {"domain": "人工智能", "stage": "准备毕业设计"}

    for name, body in THESIS_FILES.items():
        result = classify(body, bases, profile)
        assert result.base_id == "kb-thesis", f"{name} 应该归到毕业设计，实际 {result.base_name}"

    for name, body in RECIPE_FILES.items():
        result = classify(body, bases, profile)
        assert result.base_id == "kb-cook", f"{name} 应该归到菜谱，实际 {result.base_name}"


def test_classification_explains_itself() -> None:
    """A placement without a reason cannot be corrected, and the product's rule is that
    the user can see and fix what it decided."""
    result = classify(
        "毕业设计的文献综述要覆盖近五年的工作",
        [("kb", "毕业设计", "毕业论文与文献综述")],
        {"stage": "准备毕业设计"},
    )
    assert result.base_id == "kb"
    assert result.reason, "必须给出理由"
    assert "毕业设计" in result.reason


def test_content_matching_nothing_is_reported_not_guessed() -> None:
    """Filing it somewhere would be worse than saying so."""
    result = classify(
        "量子纠缠与相对论的统一描述",
        [("kb", "毕业设计", "毕业论文与文献综述")],
        {},
    )
    assert result.base_id is None
    assert "没有哪个知识库" in result.reason


# ------------------------------------------------------------ near-duplicates --


def test_near_duplicate_catches_a_reexport_with_different_bytes() -> None:
    """The case hash-based dedup misses: the same material, different bytes."""
    original = "".join(THESIS_FILES.values())
    reexported = original + "\n\n（导出时间：2026-03-01）\n"
    assert overlap_ratio(original, reexported) > 0.9


def test_unrelated_documents_do_not_look_like_duplicates() -> None:
    """The other side. A detector that calls everything a duplicate is worse than none,
    because it will be ignored."""
    thesis = "".join(THESIS_FILES.values())
    recipes = "".join(RECIPE_FILES.values())
    assert overlap_ratio(thesis, recipes) < DUPLICATE_THRESHOLD


def test_shingles_work_without_spaces() -> None:
    """Chinese has no word boundaries. A word-shingle implementation would produce one
    enormous token and report every pair as unrelated."""
    parts = shingles("毕业设计文献综述")
    assert len(parts) > 1, "中文也要切出多个 shingle"


async def test_near_duplicate_finds_an_existing_document(session: AsyncSession) -> None:
    user_id = await make_user(session)
    base = KnowledgeBase(owner_id=user_id, name="毕业设计", description="论文")
    session.add(base)
    await session.flush()

    existing_text = "".join(THESIS_FILES.values())
    document = Document(
        owner_id=user_id,
        knowledge_base_id=base.id,
        title="开题报告合集",
        original_filename="a.md",
        stored_path="/tmp/a.md",
        content_hash="hash-a",
        status="ready",
    )
    session.add(document)
    await session.flush()
    session.add(
        DocumentChunk(
            document_id=document.id,
            knowledge_base_id=base.id,
            chunk_index=0,
            content=existing_text,
            char_count=len(existing_text),
            token_estimate=len(existing_text) // 2,
        )
    )
    await session.flush()

    findings = await find_near_duplicates(
        session, owner_id=user_id, text=existing_text + "\n（重新导出）"
    )
    assert findings, "同一份材料重新导出应该被发现"
    assert findings[0].overlap >= DUPLICATE_THRESHOLD
    assert "重合" in findings[0].reason, "要给出百分比，人才好判断"


async def test_near_duplicate_ignores_a_different_user(session: AsyncSession) -> None:
    """Isolation, on the ingest path too."""
    owner = await make_user(session)
    other = await make_user(session)
    base = KnowledgeBase(owner_id=owner, name="毕业设计", description="论文")
    session.add(base)
    await session.flush()

    text = "".join(THESIS_FILES.values())
    document = Document(
        owner_id=owner,
        knowledge_base_id=base.id,
        title="t",
        original_filename="a.md",
        stored_path="/tmp/a.md",
        content_hash="h",
        status="ready",
    )
    session.add(document)
    await session.flush()
    session.add(
        DocumentChunk(
            document_id=document.id,
            knowledge_base_id=base.id,
            chunk_index=0,
            content=text,
            char_count=len(text),
            token_estimate=10,
        )
    )
    await session.flush()

    assert await find_near_duplicates(session, owner_id=other, text=text) == []


# ------------------------------------------------------------------ the gaps --


async def test_gap_detection_proposes_rather_than_acts(session: AsyncSession) -> None:
    """It returns a sentence, not a download. Fetching material on someone's behalf
    without asking is how a library fills with things they did not choose."""
    user_id = await make_user(session)
    session.add(KnowledgeBase(owner_id=user_id, name="菜谱", description="家常菜做法"))
    await session.flush()

    gaps = await detect_gaps(session, owner_id=user_id, profile={"domain": "人工智能"})
    assert gaps, "档案里的领域在库里没有资料，应该提出来"
    assert all(isinstance(g, str) for g in gaps)
    assert any("人工智能" in g for g in gaps)


async def test_no_gap_when_the_library_covers_the_domain(session: AsyncSession) -> None:
    """The other side, so the check is not simply always-true."""
    user_id = await make_user(session)
    session.add(KnowledgeBase(owner_id=user_id, name="人工智能", description="深度学习与机器学习"))
    await session.flush()

    gaps = await detect_gaps(session, owner_id=user_id, profile={"domain": "人工智能"})
    assert gaps == []

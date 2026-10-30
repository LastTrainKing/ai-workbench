"""Dropping a folder in, and having it land in the right place by itself.

The user's words: 「查找补充知识库」、「知识库等等个性化的东西，可以自己检索，自己添加。」
Today the flow is manual — pick a file, pick a base, add tags. This module is the part
that decides things instead of asking.

Four decisions it makes, each with the reason attached to the result rather than left
implicit:

* **Which base.** Scored against the base's own name and description *and* the user's
  profile. A base called 「毕业设计」 and a profile that says 「准备毕业设计」 are two
  signals pointing the same way; using only one of them would misfile anything the base
  description words differently.
* **Whether it is already there.** `register_upload` already catches an identical file by
  content hash. That is not enough: the same document re-exported, or a draft and its
  final, are the same material with different bytes. Near-duplicates are caught by
  shingle overlap and reported with a percentage, because "80% the same as X" is a
  decision a person can make and "duplicate" is not.
* **What it is about.** The concepts come from the existing analysis pipeline; this module
  only counts and reports them.
* **What is missing.** When the profile names a domain the library has nothing on, that is
  said out loud as a proposal. **Not downloaded** — proposing is the whole point, because
  fetching material on someone's behalf without asking is how a library fills with things
  they did not choose.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.knowledge import Document, DocumentChunk, KnowledgeBase

logger = get_logger(__name__)

#: Files worth reading. Anything else is counted and reported rather than silently
#: skipped — a folder of 20 files where 14 were ignored is a fact the user needs.
SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".pdf", ".docx"}

#: Shingle size for near-duplicate detection, in characters. Five is short enough to
#: survive a paragraph being reworded and long enough not to fire on common phrases.
SHINGLE = 5

#: Above this, two documents are the same material. Measured on the Jaccard overlap of
#: 5-character shingles, which is what `near_duplicate` reports.
DUPLICATE_THRESHOLD = 0.6


@dataclass
class ScannedFile:
    path: Path
    size: int
    supported: bool
    reason: str = ""


@dataclass
class Classification:
    base_id: str | None
    base_name: str
    score: float
    reason: str


@dataclass
class DuplicateFinding:
    document_id: str
    title: str
    overlap: float
    reason: str


@dataclass
class IngestReport:
    """What happened, in numbers a person can check."""

    folder: str = ""
    scanned: int = 0
    supported: int = 0
    skipped: list[str] = field(default_factory=list)
    ingested: int = 0
    duplicates: list[DuplicateFinding] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    placements: list[dict[str, Any]] = field(default_factory=list)
    concepts: int = 0
    gaps: list[str] = field(default_factory=list)

    def summarise(self) -> str:
        """One line, in the shape the product asks for:

        「我读了 7 个文件，归到了『毕业设计』，抽出 43 个概念」
        """
        if not self.ingested and not self.duplicates:
            return f"扫描了 {self.scanned} 个文件，没有可入库的内容"

        bases: dict[str, int] = {}
        for placement in self.placements:
            bases[placement["base_name"]] = bases.get(placement["base_name"], 0) + 1
        where = "、".join(f"『{name}』{count} 个" for name, count in bases.items())

        parts = [f"我读了 {self.ingested} 个文件"]
        if where:
            parts.append(f"归到了 {where}")
        if self.concepts:
            parts.append(f"抽出 {self.concepts} 个概念")
        if self.duplicates:
            parts.append(f"发现 {len(self.duplicates)} 个和已有内容高度重合")
        if self.skipped:
            parts.append(f"跳过 {len(self.skipped)} 个不支持的格式")
        if self.failed:
            parts.append(f"{len(self.failed)} 个失败")
        return "，".join(parts) + "。"


# ------------------------------------------------------------------------ scanning --


def scan_folder(folder: Path, *, recursive: bool = True) -> list[ScannedFile]:
    """Walk a folder and classify each file as usable or not, with the reason.

    Hidden files and build directories are skipped by name: a folder the user dropped in
    is likely to contain `.git`, `node_modules` or a `.DS_Store`, and reading those would
    fill the library with noise while looking like it worked.
    """
    ignored_dirs = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".idea"}
    found: list[ScannedFile] = []

    if not folder.exists():
        return found

    walker = folder.rglob("*") if recursive else folder.glob("*")
    for path in walker:
        if not path.is_file():
            continue
        if any(part in ignored_dirs for part in path.parts):
            continue
        if path.name.startswith("."):
            continue

        suffix = path.suffix.lower()
        supported = suffix in SUPPORTED_SUFFIXES
        found.append(
            ScannedFile(
                path=path,
                size=path.stat().st_size,
                supported=supported,
                reason="" if supported else f"不支持的格式 {suffix or '(无扩展名)'}",
            )
        )

    return found


# -------------------------------------------------------------------- classification --


def _terms(text: str) -> set[str]:
    lowered = unicodedata.normalize("NFKC", (text or "").lower())
    words = set(re.findall(r"[a-z0-9]{2,}", lowered))
    cjk = [c for c in lowered if "\u4e00" <= c <= "\u9fff"]
    words |= {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    return words


def classify(
    text: str, bases: list[tuple[str, str, str]], profile: dict[str, Any]
) -> Classification:
    """Pick the base this material belongs in, and say why.

    `bases` is `(id, name, description)`. Scored on the document's opening — the first few
    hundred characters are where a document says what it is, and scoring the whole text
    would let a long document drift into whichever base mentions its topic most often.
    """
    opening = (text or "")[:1500]
    doc_terms = _terms(opening)
    if not doc_terms:
        return Classification(None, "", 0.0, "文档没有可识别的文字")

    profile_terms = _terms(
        " ".join(str(profile.get(key) or "") for key in ("identity", "stage", "domain", "bio"))
        + " "
        + " ".join(str(g) for g in (profile.get("current_goals") or []))
    )

    best: Classification | None = None
    for base_id, name, description in bases:
        base_terms = _terms(f"{name} {description}")
        if not base_terms:
            continue
        overlap = len(doc_terms & base_terms)
        if overlap == 0:
            continue
        # Normalised by the base's own vocabulary, so a base with a long description does
        # not win simply by having more words to collide with.
        score = overlap / max(1, len(base_terms))
        profile_bonus = 0.15 if profile_terms & base_terms else 0.0
        total = score + profile_bonus

        if best is None or total > best.score:
            reason = f"与「{name}」的描述重合 {overlap} 个词"
            if profile_bonus:
                reason += "，且与你的档案里的领域一致"
            best = Classification(base_id, name, round(total, 3), reason)

    if best is None:
        return Classification(None, "", 0.0, "没有哪个知识库和这份内容沾边")
    return best


# ------------------------------------------------------------------ duplicate check --


def shingles(text: str, size: int = SHINGLE) -> set[str]:
    """Character n-grams. Deliberately not word-based: Chinese has no spaces, and a
    word-shingle implementation that silently produced one giant token would report every
    pair of Chinese documents as unrelated."""
    normalised = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
    if len(normalised) < size:
        return {normalised} if normalised else set()
    return {normalised[i : i + size] for i in range(len(normalised) - size + 1)}


def overlap_ratio(a: str, b: str) -> float:
    """Jaccard overlap of the two shingle sets, 0..1."""
    left, right = shingles(a), shingles(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


async def find_near_duplicates(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    text: str,
    limit: int = 3,
) -> list[DuplicateFinding]:
    """Existing documents that are substantially the same material.

    `register_upload` catches an identical file by content hash. This catches the same
    material with different bytes — a re-export, a draft and its final — which is the case
    that actually fills a library with the same thing twice.
    """
    rows = list(
        (
            await session.execute(select(Document).where(Document.owner_id == owner_id).limit(200))
        ).scalars()
    )
    if not rows:
        return []

    # Compare against the first chunk of each document: enough to recognise the material,
    # and it avoids loading every document in full on each ingest.
    findings: list[DuplicateFinding] = []
    for row in rows:
        chunk = (
            (
                await session.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == row.id)
                    .order_by(DocumentChunk.chunk_index)
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if chunk is None or not chunk.content:
            continue
        ratio = overlap_ratio(text[:4000], chunk.content)
        if ratio >= DUPLICATE_THRESHOLD:
            findings.append(
                DuplicateFinding(
                    document_id=str(row.id),
                    title=row.title,
                    overlap=round(ratio, 3),
                    reason=f"和已有文档「{row.title}」重合 {ratio:.0%}",
                )
            )

    findings.sort(key=lambda f: f.overlap, reverse=True)
    return findings[:limit]


# ------------------------------------------------------------------------- the gaps --


async def detect_gaps(
    session: AsyncSession, *, owner_id: uuid.UUID, profile: dict[str, Any]
) -> list[str]:
    """Domains the profile names that the library has nothing on.

    Returns a *proposal*, never an action. Fetching material on someone's behalf without
    asking is how a library fills with things they did not choose — and the product's rule
    is that the system proposes and the person decides.
    """
    bases = list(
        (
            await session.execute(select(KnowledgeBase).where(KnowledgeBase.owner_id == owner_id))
        ).scalars()
    )
    corpus = _terms(" ".join(f"{b.name} {b.description or ''}" for b in bases))
    if not corpus:
        return []

    gaps: list[str] = []
    for key in ("domain", "stage"):
        value = str(profile.get(key) or "").strip()
        if not value:
            continue
        wanted = _terms(value)
        if wanted and not (wanted & corpus):
            gaps.append(f"你的档案里写着「{value}」，但知识库里没有对应的资料")
    return gaps


__all__ = [
    "DUPLICATE_THRESHOLD",
    "Classification",
    "DuplicateFinding",
    "IngestReport",
    "ScannedFile",
    "classify",
    "detect_gaps",
    "find_near_duplicates",
    "overlap_ratio",
    "scan_folder",
    "shingles",
]

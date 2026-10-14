"""Adopted skills: matching, adoption, personalisation, use and review.

The six stages the product defines, and where each one lives:

    FIND      discovery.py       search public sources
    ASSESS    discovery.py       shortlist with reasons
    VERIFY    scanner.py         security scan, format check, provenance
    ADOPT     here               *only on explicit confirmation*, then bind params
    USE       here               match a question, render the personalised body
    REVIEW    here               record uses, take a rating, retire what does not work

Two rules this module enforces rather than documents:

* **Nothing is stored without confirmation.** `adopt()` takes `confirmed: bool` and
  refuses when it is false. There is no code path that installs a skill as a side effect
  of discovering it, because the assess stage exists precisely so a person chooses.
* **The personalised body is what runs.** `bind_params()` fills the `{{param}}` slots from
  the user's own profile and memories, and that rendered body is what gets stored. A skill
  whose params were never bound is a general template, and running the general template is
  the thing this product is not.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.skills.sandbox import run_in_sandbox, verdict
from app.ai.skills.scanner import scan_skill
from app.ai.skills.schema import Skill, SkillParam
from app.core.logging import get_logger
from app.models.skill import InstalledSkill

logger = get_logger(__name__)

#: True now that the layer exists. Read by the retrieval ladder so tier 2 can tell
#: "no matching skill" apart from "the skill layer is not built" — an empty list and a
#: tier that was never consulted look identical in a report, and only one is true.
SKILL_LAYER_BUILT = True


@dataclass
class SkillMatch:
    """One skill that bears on a question.

    `body` carries the **rendered** skill text — the thing the agent actually works from.

    It used to be missing, and the consequence was the whole product claim:

        registry.py stored it    row.rendered_body = rendered
        SkillMatch did not have it
        tiers.py passed three keys   {"id", "name", "score"}
        conversation_service read    skill.get("summary", "")   -> always ""

    so the model was told 「【他的做法：PPT制作Skill】」 and then given **no body at all**.
    "Carries your way of working into the job" was an empty sentence. Found by reading the
    four sites end to end; there is now a test that asserts the body arrives
    (`tests/unit/test_skill_body_reaches_context.py`).
    """

    id: str
    name: str
    score: float = 0.0
    when_to_use: str = ""
    skill_type: str = "document"
    #: Rendered body. Unfilled `{{param}}` slots are kept as-is, never blanked — that is
    #: the existing design and it is what makes an unfilled slot visible to the model.
    body: str = ""


# ------------------------------------------------------------------------ matching --


def _terms(text: str) -> set[str]:
    """Word tokens plus CJK bigrams. The same shape as the retrieval ladder's tier 1.

    Deliberately the same approach, for the same reason: this decides *whether to look
    closer*, not how to rank. A false positive costs one extra candidate shown to the
    user; a false negative means a skill that fits never gets offered at all.
    """
    lowered = (text or "").lower()
    words = {w for w in "".join(c if c.isalnum() else " " for c in lowered).split() if len(w) > 1}
    cjk = [c for c in lowered if "\u4e00" <= c <= "\u9fff"]
    words |= {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    return words


async def find_matching_skills(
    session: AsyncSession, *, user_id: uuid.UUID, question: str, limit: int = 5
) -> list[SkillMatch]:
    """This user's enabled skills that bear on the question, best first.

    Scored on `when_to_use` first and the name second, weighted that way on purpose:
    `when_to_use` is the field the author filled in to say *when this applies*, so it is
    the honest thing to match against. Matching the body would fire on any skill that
    happens to mention a word.
    """
    rows = list(
        (
            await session.execute(
                select(InstalledSkill).where(
                    InstalledSkill.user_id == user_id,
                    InstalledSkill.enabled.is_(True),
                    InstalledSkill.status == "active",
                )
            )
        ).scalars()
    )
    if not rows:
        return []

    wanted = _terms(question)
    if not wanted:
        return []

    scored: list[tuple[float, InstalledSkill]] = []
    for row in rows:
        trigger = len(wanted & _terms(row.when_to_use))
        named = len(wanted & _terms(row.name))
        score = trigger * 2.0 + named * 1.0
        if score > 0:
            scored.append((score, row))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        SkillMatch(
            id=str(row.id),
            name=row.name,
            score=round(score, 2),
            when_to_use=row.when_to_use,
            skill_type=row.skill_type,
            # The rendered body, falling back to the template if a row predates the
            # `rendered_body` column. Never empty-by-accident: a skill whose body cannot
            # be found is worse than no skill, because the prompt then claims a method
            # and supplies nothing.
            body=row.rendered_body or row.body or "",
        )
        for score, row in scored[:limit]
    ]


# ------------------------------------------------------------------ personalisation --


def bind_params(body: str, params: list[SkillParam], values: dict[str, str]) -> str:
    """Substitute `{{param}}` placeholders with this user's values.

    An unfilled param is left as its placeholder rather than blanked, so the hole is
    visible in the stored body instead of silently producing a sentence with a gap in it.
    Blanking is the failure shape this project keeps finding: it turns "we do not know
    this about you yet" into prose that reads as though we do.
    """
    rendered = body
    for param in params:
        value = (values.get(param.name) or param.default or "").strip()
        if value:
            rendered = rendered.replace(f"{{{{{param.name}}}}}", value)
    return rendered


def unfilled_params(body: str, params: list[SkillParam], values: dict[str, str]) -> list[str]:
    """Which placeholders are still unresolved. Used to ask, rather than to guess."""
    return [
        param.name
        for param in params
        if f"{{{{{param.name}}}}}" in body and not (values.get(param.name) or param.default)
    ]


# ------------------------------------------------------------------------- adoption --


class AdoptionRefused(Exception):
    """Raised when adoption is not allowed. Never silently downgraded to a warning."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


async def adopt(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    skill: Skill,
    confirmed: bool,
    param_values: dict[str, str] | None = None,
    workspace: Path | None = None,
) -> InstalledSkill:
    """Store a skill for this user — but only if a person said yes and every gate passed.

    Three gates now, and they are all here, together, because they are the things that
    must not be bypassable. Split across call sites is how one of them eventually gets
    skipped.

    For a **script-type** skill the third gate is the sandbox: the script is run once
    somewhere disposable and does not become usable unless it exited zero and touched
    nothing outside its own directory. `workspace` is the directory to protect; when it is
    not given, a script-type skill is refused rather than admitted untested, because
    **an unverified skill is worse than no skill** — it will be used.
    """
    if not confirmed:
        # Not a warning. The assess stage exists so a person chooses; a skill that
        # installs itself is the behaviour this refusal prevents.
        raise AdoptionRefused(["未经用户确认，不能入库"])

    scan = scan_skill(skill)
    if scan.blocks_adoption:
        raise AdoptionRefused([f"{f.rule}: {f.detail}" for f in scan.blocking])

    if not skill.source:
        raise AdoptionRefused(["缺少来源，无法追溯，不允许入库"])

    sandbox_record: dict[str, Any] = {}
    if skill.is_scripted:
        if workspace is None:
            raise AdoptionRefused(
                ["脚本型 skill 必须先过沙箱；没有给出要保护的目录，不能验证，因此不允许入库"]
            )
        if not skill.scripts:
            raise AdoptionRefused(["声明为脚本型但没有脚本内容，无法验证"])

        # Every script is run, not just the first: a skill that ships three and hides the
        # problem in the third would otherwise be admitted on the strength of the other two.
        for index, script in enumerate(skill.scripts, start=1):
            result = await run_in_sandbox(script, workspace=workspace)
            sandbox_record[f"script_{index}"] = result.as_dict()
            if not result.clean:
                raise AdoptionRefused([f"脚本 #{index} 未通过沙箱：{verdict(result)}"])

    values = param_values or {}
    rendered = bind_params(skill.body, skill.params, values)

    existing = (
        await session.execute(
            select(InstalledSkill).where(
                InstalledSkill.user_id == user_id, InstalledSkill.name == skill.name
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    row = existing or InstalledSkill(user_id=user_id, name=skill.name)
    row.description = skill.description
    row.when_to_use = skill.when_to_use
    row.body = skill.body
    row.rendered_body = rendered
    row.skill_type = skill.skill_type.value
    row.scripts = list(skill.scripts)
    row.params = [
        {
            "name": p.name,
            "description": p.description,
            "source": p.source,
            "value": values.get(p.name, p.default),
        }
        for p in skill.params
    ]
    row.source = skill.source
    row.source_commit = skill.source_commit
    row.license = skill.license
    row.version = skill.version
    row.verified_at = now.isoformat()
    row.verification = {
        "findings": [
            {
                "rule": f.rule,
                "severity": f.severity.value,
                "detail": f.detail,
                "evidence": f.evidence,
            }
            for f in scan.findings
        ],
        "summary": scan.summary(),
        "unfilled_params": unfilled_params(skill.body, skill.params, values),
        # For a script-type skill, the exit code and the escape check. Empty for a
        # document-type skill, because those are not executed.
        "sandbox": sandbox_record,
    }
    row.status = "active"
    row.enabled = True

    session.add(row)
    await session.flush()
    logger.info(
        "skill_adopted", extra={"user": str(user_id), "skill": skill.name, "source": skill.source}
    )
    return row


# ------------------------------------------------------------------- use and review --


async def record_use(session: AsyncSession, *, skill_id: uuid.UUID, succeeded: bool) -> None:
    """A skill was used. Recorded whether or not it worked.

    Counting only successes would make `use_count` a quality score rather than a usage
    count, and the review stage needs to see a skill that gets tried and fails — that is
    the one to retire.
    """
    row = await session.get(InstalledSkill, skill_id)
    if row is None:
        logger.warning("skill_use_unknown", extra={"skill_id": str(skill_id)})
        return
    row.use_count += 1
    if succeeded:
        row.success_count += 1
    row.last_used_at = datetime.now(UTC)


async def review(
    session: AsyncSession,
    *,
    skill_id: uuid.UUID,
    rating: float,
    note: str = "",
    retire: bool = False,
) -> InstalledSkill:
    """The review stage. Without it the library becomes another dumping ground.

    `retire` disables rather than deletes: a skill that was imported, used and judged bad
    is evidence about a source, and deleting it throws that away.
    """
    row = await session.get(InstalledSkill, skill_id)
    if row is None:
        raise AdoptionRefused([f"没有这个 skill：{skill_id}"])

    row.rating = max(0.0, min(1.0, rating))
    row.review_note = note
    if retire:
        row.enabled = False
        row.status = "retired"
    await session.flush()
    return row


async def list_installed(session: AsyncSession, *, user_id: uuid.UUID) -> list[InstalledSkill]:
    rows = (
        await session.execute(
            select(InstalledSkill)
            .where(InstalledSkill.user_id == user_id)
            .order_by(InstalledSkill.created_at.desc())
        )
    ).scalars()
    return list(rows)


async def usage_stats(session: AsyncSession, *, user_id: uuid.UUID) -> dict[str, Any]:
    """Numbers for the review screen. Real counts, no estimates."""
    rows = await list_installed(session, user_id=user_id)
    used = [r for r in rows if r.use_count > 0]
    rated = [r for r in rows if r.rating is not None]
    return {
        "installed": len(rows),
        "active": len([r for r in rows if r.status == "active"]),
        "retired": len([r for r in rows if r.status == "retired"]),
        "ever_used": len(used),
        "never_used": len(rows) - len(used),
        "total_uses": sum(r.use_count for r in rows),
        "rated": len(rated),
        "average_rating": round(sum(r.rating or 0 for r in rated) / len(rated), 2) if rated else None,
    }


__all__ = [
    "SKILL_LAYER_BUILT",
    "AdoptionRefused",
    "SkillMatch",
    "adopt",
    "bind_params",
    "find_matching_skills",
    "list_installed",
    "record_use",
    "review",
    "unfilled_params",
    "usage_stats",
]

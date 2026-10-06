"""The skill layer's HTTP surface.

Six endpoints, one per stage a person actually touches. Two of them are shaped by rules
that are not negotiable:

* **`POST /skills/discover` writes nothing.** It searches and returns candidates with
  reasons. There is no endpoint that installs as a side effect of searching, because the
  assess stage exists so a person chooses and an endpoint that skips it would be the
  bypass.
* **`POST /skills/adopt` requires `confirmed: true`.** Not a default, not inferred from
  the request having arrived — an explicit field. `registry.adopt` refuses without it, and
  this route does not paper over that refusal.

Everything returns the project's standard envelope and goes through the same
authentication as every other route.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.ai.skills import discovery as discovery_module
from app.ai.skills import registry
from app.ai.skills.schema import Skill, SkillParam, SkillType, parse_skill
from app.api.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError, ValidationError
from app.core.response import ok
from app.models.context import UserProfile

router = APIRouter(prefix="/skills", tags=["skills"])


# ------------------------------------------------------------------------- schemas --


class DiscoverRequest(BaseModel):
    need: str = Field(default="", max_length=400, description="用户想完成的事")
    per_query: int = Field(default=6, ge=1, le=20)


class AdoptRequest(BaseModel):
    #: Required, with no default. A default of `true` would make every client that forgot
    #: the field install a skill without anybody saying yes.
    confirmed: bool = Field(description="用户是否确认导入；不传或为 false 一律拒绝")
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    when_to_use: str = ""
    body: str = Field(min_length=1)
    source: str = ""
    source_commit: str = ""
    license: str = ""
    version: str = "1.0.0"
    params: list[dict[str, Any]] = Field(default_factory=list)
    scripts: list[str] = Field(default_factory=list)
    param_values: dict[str, str] = Field(default_factory=dict)


class ReviewRequest(BaseModel):
    rating: float = Field(ge=0.0, le=1.0)
    note: str = ""
    retire: bool = False


class UseRequest(BaseModel):
    succeeded: bool = True


# --------------------------------------------------------------------------- read --


def _skill_payload(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "when_to_use": row.when_to_use,
        "skill_type": row.skill_type,
        "version": row.version,
        "source": row.source,
        "source_commit": row.source_commit,
        "license": row.license,
        "status": row.status,
        "enabled": row.enabled,
        "verified_at": row.verified_at,
        "verification": row.verification,
        "params": row.params,
        "has_scripts": bool(row.scripts),
        "use_count": row.use_count,
        "success_count": row.success_count,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "rating": row.rating,
        "review_note": row.review_note,
    }


@router.get("", summary="已安装的 skill")
async def list_skills(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    rows = await registry.list_installed(session, user_id=user.id)
    return ok({"items": [_skill_payload(r) for r in rows], "total": len(rows)})


@router.get("/stats", summary="使用与复盘的真实数字")
async def skill_stats(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    return ok(await registry.usage_stats(session, user_id=user.id))


@router.get("/{skill_id}", summary="单个 skill 的详情")
async def get_skill(skill_id: uuid.UUID, user: CurrentUser, session: DbSession) -> dict[str, Any]:
    rows = await registry.list_installed(session, user_id=user.id)
    for row in rows:
        if row.id == skill_id:
            payload = _skill_payload(row)
            # The rendered body is what actually runs, so it is part of the detail — a
            # user checking what a skill will do should not have to read the template and
            # mentally substitute the values.
            payload["rendered_body"] = row.rendered_body
            return ok(payload)
    raise NotFoundError("没有这个 skill")


# -------------------------------------------------------------------------- find --


@router.post("/discover", summary="去公开渠道找候选（只读，不写入任何东西）")
async def discover_skills(
    payload: DiscoverRequest, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    profile_row = (
        await session.execute(
            # The profile is what makes the search this person's; without it the terms are
            # the same for everyone, which is the thing this product is not.
            select(UserProfile).where(UserProfile.user_id == user.id)
        )
    ).scalar_one_or_none()

    profile = {
        "identity": getattr(profile_row, "identity", "") or "",
        "stage": getattr(profile_row, "stage", "") or "",
        "domain": getattr(profile_row, "domain", "") or "",
        "current_goals": getattr(profile_row, "current_goals", []) or [],
    }

    result = await discovery_module.discover(profile=profile, need=payload.need, per_query=payload.per_query)
    return ok(
        {
            "queries": result.queries,
            "summary": result.summary(),
            "error": result.error,
            "candidates": [
                {
                    "name": c.name,
                    "repo": c.repo,
                    "url": c.url,
                    "stars": c.stars,
                    "license": c.license,
                    "updated_at": c.updated_at,
                    "why": c.why,
                }
                for c in result.kept
            ],
            "rejected": [
                {"repo": c.repo, "url": c.url, "reason": c.dropped_because} for c in result.dropped
            ],
        }
    )


# ------------------------------------------------------------------------ adopt --


@router.post("/adopt", summary="导入一个 skill（必须带 confirmed=true）")
async def adopt_skill(
    payload: AdoptRequest, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    # A pasted skill file is parsed rather than trusted field-by-field, so its front
    # matter and its body cannot disagree. Otherwise the flat fields are used.
    skill: Skill | None = None
    if payload.body.lstrip().startswith("---"):
        skill, validation = parse_skill(
            payload.body, source=payload.source, source_commit=payload.source_commit
        )
        if not validation.ok:
            raise ValidationError("skill 文件格式不对：" + "；".join(validation.errors))
    else:
        skill = Skill(
            name=payload.name,
            description=payload.description,
            when_to_use=payload.when_to_use,
            body=payload.body,
            skill_type=SkillType.SCRIPT if payload.scripts else SkillType.DOCUMENT,
            scripts=list(payload.scripts),
            params=[
                SkillParam(name=str(p.get("name", "")), description=str(p.get("description", "")))
                for p in payload.params
            ],
            source=payload.source,
            source_commit=payload.source_commit,
            license=payload.license,
            version=payload.version,
        )

    try:
        row = await registry.adopt(
            session,
            user_id=user.id,
            skill=skill,
            confirmed=payload.confirmed,
            param_values=payload.param_values,
        )
    except registry.AdoptionRefused as exc:
        # The refusal is the product working. Returned as a validation error with the
        # reasons intact, never softened into a success.
        raise ValidationError("；".join(exc.reasons)) from exc

    return ok(_skill_payload(row))


@router.post("/{skill_id}/use", summary="记录一次使用（成功与否都记）")
async def record_skill_use(
    skill_id: uuid.UUID, payload: UseRequest, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    owned = [r.id for r in await registry.list_installed(session, user_id=user.id)]
    if skill_id not in owned:
        raise NotFoundError("没有这个 skill")
    await registry.record_use(session, skill_id=skill_id, succeeded=payload.succeeded)
    return ok({"recorded": True})


@router.post("/{skill_id}/review", summary="复盘：打分，必要时退役")
async def review_skill(
    skill_id: uuid.UUID, payload: ReviewRequest, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    owned = [r.id for r in await registry.list_installed(session, user_id=user.id)]
    if skill_id not in owned:
        raise NotFoundError("没有这个 skill")
    row = await registry.review(
        session, skill_id=skill_id, rating=payload.rating, note=payload.note, retire=payload.retire
    )
    return ok(_skill_payload(row))


@router.delete("/{skill_id}", summary="停用（不删除，保留它作为来源的证据）")
async def disable_skill(
    skill_id: uuid.UUID, user: CurrentUser, session: DbSession, hard: bool = Query(default=False)
) -> dict[str, Any]:
    owned = [r.id for r in await registry.list_installed(session, user_id=user.id)]
    if skill_id not in owned:
        raise NotFoundError("没有这个 skill")

    if hard:
        from app.models.skill import InstalledSkill

        row = await session.get(InstalledSkill, skill_id)
        if row is not None:
            await session.delete(row)
        return ok({"deleted": True})

    row = await registry.review(session, skill_id=skill_id, rating=0.0, note="已停用", retire=True)
    return ok(_skill_payload(row))


__all__ = ["router"]

"""Personal Context API: profile, memory, preferences, context preview.

These endpoints are what turn the context layer from a schema into something the
user can see and correct. The design commitment behind them:

    The user can always see what the system believes about them, and change it.

That is why ``/context/preview`` exists and why memory candidates are approved
rather than silently stored. An assistant that quietly accumulates a private
model of you is not trustworthy, however good its answers are.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.ai.memory import ContextAssembler, MemoryEvaluator, record_preference
from app.ai.types import ChatMessage
from app.api.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.response import ok
from app.models.context import (
    ContextSnapshot,
    MemoryCandidate,
    MemoryEntry,
    UserPreference,
    UserProfile,
)

router = APIRouter(prefix="", tags=["personal-context"])

logger = get_logger(__name__)

#: The kinds a memory can have, mirrored from the evaluator so the API can
#: validate them rather than accepting anything and storing a typo.
MEMORY_KINDS = ("identity", "goal", "preference", "skill", "constraint", "fact", "decision")
MEMORY_STATUSES = ("active", "archived", "rejected")


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class ProfileUpdate(BaseModel):
    """Partial update. Omitted fields are left untouched."""

    display_name: str | None = Field(default=None, max_length=120)
    identity: str | None = Field(default=None, max_length=200)
    stage: str | None = Field(default=None, max_length=120)
    domain: str | None = Field(default=None, max_length=200)
    current_goals: list[str] | None = None
    language: str | None = Field(default=None, max_length=16)
    timezone: str | None = Field(default=None, max_length=64)
    bio: str | None = Field(default=None, max_length=4000)
    onboarded: bool | None = None


class CleanupGroupRequest(BaseModel):
    """One duplicate group, as the user was shown it."""

    keep_id: str
    merge: list[str] = Field(default_factory=list)
    why: str = ""


class CleanupLitterRequest(BaseModel):
    id: str
    why: str = ""


class CleanupCandidateGroupRequest(BaseModel):
    """A group of pending candidates that say the same thing."""

    keep_id: str
    merge: list[str] = Field(default_factory=list)
    why: str = ""


class CleanupApplyRequest(BaseModel):
    """The plan the user approved, by id.

    Ids rather than whole rows: the client sends back what it was shown, and every id
    is checked against this user's own rows before anything is touched.
    """

    duplicate_groups: list[CleanupGroupRequest] = Field(default_factory=list)
    candidate_groups: list[CleanupCandidateGroupRequest] = Field(default_factory=list)
    litter: list[CleanupLitterRequest] = Field(default_factory=list)
    total_before: int = 0


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    kind: str = "fact"
    scope: str = "global"
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    kind: str | None = None
    scope: str | None = None
    status: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class PreferenceUpdate(BaseModel):
    """One preference, or several. Keys are free-form; values are any JSON."""

    preferences: dict[str, Any] = Field(default_factory=dict)
    scope: str = "global"


class ContextPreviewRequest(BaseModel):
    query: str = Field(default="", max_length=2000)
    purpose: str = Field(default="preview", max_length=24)
    knowledge_chunks: list[dict[str, Any]] = Field(default_factory=list)
    project_digest: str = Field(default="", max_length=4000)


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #
async def _get_or_create_profile(session: DbSession, user_id: uuid.UUID) -> UserProfile:
    profile = (
        await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    ).scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user_id)
        session.add(profile)
        await session.flush()
    return profile


@router.get("/profile", summary="The user's profile")
async def get_profile(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    profile = await _get_or_create_profile(session, user.id)
    return ok(
        {
            "display_name": profile.display_name,
            "identity": profile.identity,
            "stage": profile.stage,
            "domain": profile.domain,
            "current_goals": profile.current_goals,
            "language": profile.language,
            "timezone": profile.timezone,
            "bio": profile.bio,
            "onboarded": profile.onboarded,
            # The exact text carried into every call, so the user can see how
            # they are described rather than guessing.
            "digest": profile.digest(),
        }
    )


@router.patch("/profile", summary="Update the profile")
async def update_profile(
    payload: ProfileUpdate, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    profile = await _get_or_create_profile(session, user.id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if key == "current_goals":
            value = [str(item).strip() for item in value if str(item).strip()][:12]
        setattr(profile, key, value)
    await session.flush()
    return ok({"updated": True, "digest": profile.digest()}, message="档案已更新")


# --------------------------------------------------------------------------- #
# Memories
# --------------------------------------------------------------------------- #
@router.get("/memories", summary="List memories")
async def list_memories(
    user: CurrentUser,
    session: DbSession,
    kind: str | None = None,
    scope: str | None = None,
    status: str = "active",
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    stmt = select(MemoryEntry).where(MemoryEntry.user_id == user.id)
    if status and status != "all":
        if status not in MEMORY_STATUSES:
            raise ValidationError(f"status must be one of {', '.join(MEMORY_STATUSES)}")
        stmt = stmt.where(MemoryEntry.status == status)
    if kind:
        if kind not in MEMORY_KINDS:
            raise ValidationError(f"kind must be one of {', '.join(MEMORY_KINDS)}")
        stmt = stmt.where(MemoryEntry.kind == kind)
    if scope:
        stmt = stmt.where(MemoryEntry.scope == scope)

    stmt = stmt.order_by(MemoryEntry.use_count.desc(), MemoryEntry.updated_at.desc()).limit(limit)
    rows = list((await session.execute(stmt)).scalars())
    return ok({"memories": [row.as_dict() for row in rows], "count": len(rows)})


@router.post("/memories", summary="Add a memory by hand")
async def create_memory(
    payload: MemoryCreate, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    if payload.kind not in MEMORY_KINDS:
        raise ValidationError(f"kind must be one of {', '.join(MEMORY_KINDS)}")
    entry = MemoryEntry(
        user_id=user.id,
        kind=payload.kind,
        scope=payload.scope,
        content=payload.content.strip(),
        confidence=payload.confidence,
        source="manual",
        status="active",
        # A hand-written memory is confirmed by definition.
        confirmed_by_user=True,
    )
    session.add(entry)
    await session.flush()
    return ok(entry.as_dict(), message="记忆已添加")


@router.get("/memories/stats", summary="Memory purity numbers")
async def memory_stats(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    """The numbers that show the filter is doing work rather than hoarding."""
    evaluator = MemoryEvaluator(session, llm=None, user_id=user.id)
    return ok(await evaluator.stats())


@router.patch("/memories/{memory_id}", summary="Edit or archive a memory")
async def update_memory(
    memory_id: uuid.UUID, payload: MemoryUpdate, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    entry = await session.get(MemoryEntry, memory_id)
    if entry is None or entry.user_id != user.id:
        raise NotFoundError("记忆不存在")

    patch = payload.model_dump(exclude_unset=True)
    if patch.get("kind") and patch["kind"] not in MEMORY_KINDS:
        raise ValidationError(f"kind must be one of {', '.join(MEMORY_KINDS)}")
    if patch.get("status") and patch["status"] not in MEMORY_STATUSES:
        raise ValidationError(f"status must be one of {', '.join(MEMORY_STATUSES)}")

    for key, value in patch.items():
        if value is not None:
            setattr(entry, key, value)
    await session.flush()
    return ok(entry.as_dict(), message="记忆已更新")


@router.delete("/memories/{memory_id}", summary="Delete a memory")
async def delete_memory(memory_id: uuid.UUID, user: CurrentUser, session: DbSession) -> dict[str, Any]:
    """Hard delete.

    Deleting has to actually remove the row: an archive flag that still gets
    retrieved is worse than no delete button, because the user believes the
    memory is gone and it is not. Archiving is available through PATCH for
    anyone who wants a softer option.
    """
    entry = await session.get(MemoryEntry, memory_id)
    if entry is None or entry.user_id != user.id:
        raise NotFoundError("记忆不存在")
    await session.delete(entry)
    await session.flush()
    return ok({"deleted": str(memory_id)}, message="记忆已删除")


# --------------------------------------------------------------------------- #
# Candidates
# --------------------------------------------------------------------------- #
@router.get("/memory-candidates", summary="Memories awaiting a decision")
async def list_candidates(
    user: CurrentUser, session: DbSession, status: str = "pending"
) -> dict[str, Any]:
    stmt = select(MemoryCandidate).where(MemoryCandidate.user_id == user.id)
    if status and status != "all":
        stmt = stmt.where(MemoryCandidate.status == status)
    stmt = stmt.order_by(MemoryCandidate.created_at.desc()).limit(200)
    rows = list((await session.execute(stmt)).scalars())
    return ok({"candidates": [row.as_dict() for row in rows], "count": len(rows)})


@router.post("/memory-candidates/{candidate_id}/approve", summary="Approve a candidate")
async def approve_candidate(
    candidate_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    evaluator = MemoryEvaluator(session, llm=None, user_id=user.id)
    entry = await evaluator.approve(candidate_id)
    if entry is None:
        raise NotFoundError("候选不存在")
    return ok(entry.as_dict(), message="已确认并记为长期记忆")


@router.post("/memory-candidates/{candidate_id}/reject", summary="Reject a candidate")
async def reject_candidate(
    candidate_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    evaluator = MemoryEvaluator(session, llm=None, user_id=user.id)
    if not await evaluator.reject(candidate_id):
        raise NotFoundError("候选不存在")
    return ok({"rejected": str(candidate_id)}, message="已拒绝")


# --------------------------------------------------------------------------- #
# One-time cleanup
# --------------------------------------------------------------------------- #
@router.post("/memory-cleanup/plan", summary="What a cleanup would do — writes nothing")
async def memory_cleanup_plan(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    """Compute the cleanup and return it for review.

    **Nothing is changed here**, and that is the point: the plan names every row it
    would merge or delete, with the reason, so the user reads it before it happens.
    The rule is 「先给用户看、再删」 — memory is the user's, and a deletion they did not
    see is not a cleanup, it is the system editing their idea of themselves.

    `POST` rather than `GET` because it costs a model call: which rows are the same
    fact is decided by the model, not by a threshold. See `similarity.py` for the
    measurement that ruled the threshold out.
    """
    from app.ai.memory.cleanup import plan_cleanup
    from app.ai.providers.factory import build_llm_provider

    llm, _info = build_llm_provider()
    plan = await plan_cleanup(session, user_id=user.id, llm=llm)
    return ok(plan.as_dict(), message=plan.note)


@router.post("/memory-cleanup/apply", summary="Execute a cleanup the user has seen")
async def memory_cleanup_apply(
    payload: CleanupApplyRequest, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    """Execute the plan, by id.

    The client sends back the ids it was shown rather than a plan it invented, and
    every id is checked against this user's rows before anything happens — so this
    endpoint cannot be used to merge or delete something the user was never shown.
    """
    from app.ai.memory.cleanup import CandidateGroup, CleanupPlan, DuplicateGroup, apply_cleanup

    plan = CleanupPlan(total_before=payload.total_before)
    for group in payload.duplicate_groups:
        plan.duplicates.append(
            DuplicateGroup(
                keep_id=group.keep_id,
                keep_content="",
                keep_use_count=0,
                merged=[{"id": row_id, "content": "", "use_count": 0} for row_id in group.merge],
                why=group.why,
            )
        )
    for group in payload.candidate_groups:
        plan.candidate_groups.append(
            CandidateGroup(
                keep_id=group.keep_id,
                keep_content="",
                merged=[{"id": row_id, "content": ""} for row_id in group.merge],
                why=group.why,
            )
        )
    plan.litter = [{"id": row.id, "content": "", "use_count": 0, "why": row.why} for row in payload.litter]

    result = await apply_cleanup(session, user_id=user.id, plan=plan)
    return ok(
        result.as_dict(),
        message=(
            f"已合并 {result.merged} 条记忆、{result.candidates_folded} 条待确认，"
            f"删除 {result.deleted} 条"
        ),
    )


# --------------------------------------------------------------------------- #
# Preferences
# --------------------------------------------------------------------------- #
@router.get("/preferences", summary="Behavioural preferences")
async def list_preferences(
    user: CurrentUser, session: DbSession, scope: str | None = None
) -> dict[str, Any]:
    stmt = select(UserPreference).where(UserPreference.user_id == user.id)
    if scope:
        stmt = stmt.where(UserPreference.scope == scope)
    rows = list((await session.execute(stmt)).scalars())
    return ok(
        {
            "preferences": [
                {"key": row.key, "value": row.value, "scope": row.scope, "source": row.source}
                for row in rows
            ],
            "count": len(rows),
        }
    )


@router.patch("/preferences", summary="Set preferences")
async def update_preferences(
    payload: PreferenceUpdate, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    if not payload.preferences:
        raise ValidationError("至少提供一个偏好项")
    for key, value in payload.preferences.items():
        await record_preference(
            session,
            user_id=user.id,
            key=str(key)[:80],
            value=value,
            scope=payload.scope,
            source="user",
        )
    return ok({"updated": sorted(payload.preferences)}, message="偏好已保存")


# --------------------------------------------------------------------------- #
# Context preview
# --------------------------------------------------------------------------- #
class ProfileExtractRequest(BaseModel):
    description: str = Field(min_length=2, max_length=1000)


@router.post("/profile/extract", summary="Parse a self-description into profile fields")
async def extract_profile(payload: ProfileExtractRequest, user: CurrentUser) -> dict[str, Any]:
    """Turn one free-form sentence into structured fields — for confirmation, not storage.

    Nothing is written here. The first-run wizard shows the result back and the
    user decides; the same principle as memory candidates. An onboarding flow that
    silently commits its interpretation of you is the thing this design exists to
    avoid.
    """
    import json as _json

    from app.ai.providers.budget import reasoning_budget
    from app.ai.providers.factory import build_llm_provider

    llm, _ = build_llm_provider()
    system = (
        "从用户的一句话自我介绍里抽取结构化信息。只输出 JSON：\n"
        "{\n"
        '  "identity": "身份/职业，例如「计算机专业大三学生」",\n'
        '  "stage": "所处阶段，例如「准备毕业设计」",\n'
        '  "domain": "关注的领域",\n'
        '  "current_goals": ["当前目标，最多 4 条"],\n'
        '  "bio": "协作偏好或需要特别注意的地方，没有就留空"\n'
        "}\n"
        "没有提到的字段留空字符串或空数组，不要编造。"
    )
    try:
        chat = await llm.chat(
            [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=payload.description),
            ],
            temperature=0.0,
            # `reasoning_budget()`, not a literal. This call asked for 500 tokens, which
            # the thinking phase consumed before the model emitted a character, so
            # extraction raised `ai_reasoning_budget_exhausted` every single time and the
            # wizard's main path never worked. `budget.py` exists to stop exactly this
            # and already documents the same failure twice — the agent's recovery call
            # and the memory evaluator.
            max_tokens=reasoning_budget(),
            json_mode=True,
        )
        parsed = _json.loads(chat.content)
    except Exception as exc:
        logger.warning("profile_extract_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        raise ValidationError("暂时无法解析这段描述，你可以直接进入，之后再手动填写档案") from exc

    goals = parsed.get("current_goals") or []
    if isinstance(goals, str):
        goals = [goals]
    return ok(
        {
            "identity": str(parsed.get("identity") or "")[:200],
            "stage": str(parsed.get("stage") or "")[:120],
            "domain": str(parsed.get("domain") or "")[:200],
            "current_goals": [str(g)[:120] for g in goals][:4],
            "bio": str(parsed.get("bio") or "")[:400],
        }
    )


@router.get("/context/preview", summary="What the model would be told right now")

async def preview_context(
    user: CurrentUser,
    session: DbSession,
    query: str = "",
    budget: int = Query(default=1800, ge=200, le=8000),
) -> dict[str, Any]:
    """Assemble the context and return it, without calling a model.

    This is the transparency guarantee made usable: the user can see exactly
    which profile fields, memories and preferences would be sent, and how much of
    the budget each block takes.
    """
    assembler = ContextAssembler(session, user_id=user.id, budget_tokens=budget)
    assembled = await assembler.assemble(goal=query)
    payload = assembled.as_dict()
    payload["query"] = query
    payload["budget_tokens"] = budget
    return ok(payload)


@router.post("/context/preview", summary="Preview with knowledge and project context")
async def preview_context_post(
    payload: ContextPreviewRequest, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    assembler = ContextAssembler(session, user_id=user.id)
    assembled = await assembler.assemble(
        goal=payload.query,
        knowledge_chunks=payload.knowledge_chunks,
        project_digest=payload.project_digest,
    )
    return ok(assembled.as_dict())


@router.get("/context/snapshots", summary="Recent context injections")
async def list_context_snapshots(
    user: CurrentUser, session: DbSession, limit: int = Query(default=20, ge=1, le=100)
) -> dict[str, Any]:
    stmt = (
        select(ContextSnapshot)
        .where(ContextSnapshot.user_id == user.id)
        .order_by(ContextSnapshot.created_at.desc())
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars())
    return ok({"snapshots": [row.as_dict() for row in rows], "count": len(rows)})


@router.get("/context/stats", summary="How much context the system holds")
async def context_stats(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    """Counts for the onboarding and memory screens."""
    memories = await session.scalar(
        select(func.count()).select_from(MemoryEntry).where(MemoryEntry.user_id == user.id)
    )
    active = await session.scalar(
        select(func.count())
        .select_from(MemoryEntry)
        .where(MemoryEntry.user_id == user.id, MemoryEntry.status == "active")
    )
    pending = await session.scalar(
        select(func.count())
        .select_from(MemoryCandidate)
        .where(MemoryCandidate.user_id == user.id, MemoryCandidate.status == "pending")
    )
    prefs = await session.scalar(
        select(func.count()).select_from(UserPreference).where(UserPreference.user_id == user.id)
    )
    snapshots = await session.scalar(
        select(func.count()).select_from(ContextSnapshot).where(ContextSnapshot.user_id == user.id)
    )
    profile = (
        await session.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    ).scalar_one_or_none()
    return ok(
        {
            "memories_total": memories or 0,
            "memories_active": active or 0,
            "candidates_pending": pending or 0,
            "preferences": prefs or 0,
            "context_snapshots": snapshots or 0,
            "profile_ready": bool(profile and profile.digest()),
            "onboarded": bool(profile and profile.onboarded),
        }
    )

class TierQuery(BaseModel):
    """A question to run down the retrieval ladder."""

    question: str = Field(min_length=1, max_length=500)


@router.post("/retrieval/tiers", summary="这次是从哪一级拿到的答案")
async def retrieval_tiers(
    payload: TierQuery, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    """Run the tier ladder and report which one answered, and how long each took.

    This exists because the product's claim is that it is faster than starting from
    scratch, and a claim like that has to be visible where the work happens. The workbench
    shows the answer to "which tier did this come from" next to the context it injected,
    so a person can see that a question was answered from their own memory rather than
    from the model's general knowledge.
    """
    from app.ai.rag.tiers import resolve

    result = await resolve(session, user_id=user.id, question=payload.question)
    return ok(result.as_dict())

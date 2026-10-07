"""Delivery API: build runs, deliverables, acceptance.

The endpoints that make the loop visible. Two of them matter more than the rest:

* `POST /deliverables/{id}/reject` requires a reason, because a rejection without one
  teaches nothing.
* `GET /deliverables/{id}` returns the full verification evidence, because "it says the
  file is fine" is not something a user should have to take on faith.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession
from app.core.errors import NotFoundError, ValidationError
from app.core.response import ok
from app.models.delivery import BuildStatus
from app.services.delivery_service import DeliveryService

router = APIRouter(prefix="", tags=["delivery"])


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class BuildRunCreate(BaseModel):
    goal: str = Field(min_length=4, max_length=2000)
    deliverable_kind: str = "mixed"
    title: str = Field(default="", max_length=200)
    project_id: uuid.UUID | None = None
    #: Which material this run may use. Explicit, so "what did it read" has an answer.
    knowledge_base_ids: list[uuid.UUID] = Field(default_factory=list)
    document_ids: list[uuid.UUID] = Field(default_factory=list)


class DeliverableCreate(BaseModel):
    """Used when a run produces a file through a path other than the agent."""

    path: str = Field(min_length=1)
    kind: str = Field(min_length=1, max_length=16)
    title: str = Field(default="", max_length=240)
    style: str = Field(default="", max_length=32)
    parent_id: uuid.UUID | None = None


class RejectRequest(BaseModel):
    #: Required. See the module docstring.
    feedback: str = Field(min_length=2, max_length=1000)


# --------------------------------------------------------------------------- #
# Build runs
# --------------------------------------------------------------------------- #
@router.post("/build-runs", status_code=201, summary="Ask for something to be built")
async def create_build_run(
    payload: BuildRunCreate, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    service = DeliveryService(session)
    scope: dict[str, Any] = {}
    if payload.knowledge_base_ids:
        scope["knowledge_base_ids"] = [str(value) for value in payload.knowledge_base_ids]
    if payload.document_ids:
        scope["document_ids"] = [str(value) for value in payload.document_ids]

    run = await service.create_build_run(
        owner_id=user.id,
        goal=payload.goal,
        deliverable_kind=payload.deliverable_kind,
        scope=scope,
        project_id=payload.project_id,
        title=payload.title,
    )
    # The message is literally true. Creating a run does not start it — that is
    # `POST /build-runs/{id}/execute`. Saying "已开始处理" here was a lie that cost a
    # user a whole session: the run sat in DRAFT, the UI read like work in progress,
    # and nothing was ever executed.
    return ok(run.as_dict(include_plan=True), message="已创建，尚未开始")


@router.get("/build-runs", summary="List build runs")
async def list_build_runs(
    user: CurrentUser,
    session: DbSession,
    status: str | None = None,
    active_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    service = DeliveryService(session)
    runs = await service.list_build_runs(
        owner_id=user.id, status=status, active_only=active_only, limit=limit
    )
    return ok({"build_runs": [run.as_dict() for run in runs], "count": len(runs)})


@router.get("/build-runs/{build_run_id}", summary="One build run, with its plan")
async def get_build_run(
    build_run_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    service = DeliveryService(session)
    run = await service.get_build_run(owner_id=user.id, build_run_id=build_run_id)
    payload = run.as_dict(include_plan=True)
    # Verification is included here on purpose. The workbench's whole claim is that
    # the user can see *why* a file is considered good, and an endpoint that omits
    # the evidence makes that claim unkeepable - the UI had a "验证证据" heading with
    # nothing under it.
    payload["deliverables"] = [item.as_dict() for item in run.deliverables]
    return ok(payload)


@router.get("/build-runs/{build_run_id}/context", summary="What this run was told")
async def build_run_context(
    build_run_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    """The context this run actually received.

    The workbench shows this beside the execution, so "why did it do that" has an
    answer that is not a guess.
    """
    from app.models.context import ContextSnapshot

    service = DeliveryService(session)
    run = await service.get_build_run(owner_id=user.id, build_run_id=build_run_id)
    if run.context_snapshot_id is None:
        return ok({"available": False, "rendered": "", "reason": "本次运行没有注入个人上下文"})

    snapshot = await session.get(ContextSnapshot, run.context_snapshot_id)
    if snapshot is None:
        return ok({"available": False, "rendered": "", "reason": "上下文快照已被清理"})
    payload = snapshot.as_dict()
    payload["available"] = True
    return ok(payload)


@router.post("/build-runs/{build_run_id}/execute", summary="Start working on it")
async def execute_build_run_endpoint(
    build_run_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    """Hand the goal to the agent.

    Returns immediately with the ids; the work runs in the background. An agent run
    takes minutes, and holding the request open for it would time out while freezing
    every other request on the same worker.
    """
    from app.services.build_executor import execute_build_run

    service = DeliveryService(session)
    run = await service.get_build_run(owner_id=user.id, build_run_id=build_run_id)
    if run.status not in (BuildStatus.DRAFT.value, BuildStatus.READY.value):
        raise ValidationError(f"当前状态（{run.status}）下不能开始执行")

    # Assemble and record what this run will be told about the user, then hand the
    # same block to the agent — so "what did it know" and "what was it given" are
    # the same string rather than two things that ought to match.
    context = await service.attach_context(run)
    result = await execute_build_run(session, build_run_id=build_run_id, context=context)
    return ok(result, message="已开始执行")


@router.post("/build-runs/{build_run_id}/pause", summary="Pause")
async def pause_build_run(
    build_run_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    from app.services.autonomous_service import control

    service = DeliveryService(session)
    run = await service.get_build_run(owner_id=user.id, build_run_id=build_run_id)
    if run.ai_run_id is None:
        raise ValidationError("这次任务还没有开始执行")
    result = control(str(run.ai_run_id), "pause")
    return ok(result)


@router.post("/build-runs/{build_run_id}/resume", summary="Resume")
async def resume_build_run(
    build_run_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    from app.services.autonomous_service import control

    service = DeliveryService(session)
    run = await service.get_build_run(owner_id=user.id, build_run_id=build_run_id)
    if run.ai_run_id is None:
        raise ValidationError("这次任务还没有开始执行")
    result = control(str(run.ai_run_id), "resume")
    return ok(result)


@router.post("/build-runs/{build_run_id}/cancel", summary="Cancel")
async def cancel_build_run(
    build_run_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    from app.services.autonomous_service import control

    service = DeliveryService(session)
    run = await service.get_build_run(owner_id=user.id, build_run_id=build_run_id)
    if run.ai_run_id is None:
        raise ValidationError("这次任务还没有开始执行")
    result = control(str(run.ai_run_id), "cancel")
    if result.get("ok"):
        await service.set_status(run, BuildStatus.CANCELLED)
    return ok(result)


# --------------------------------------------------------------------------- #
# Deliverables
# --------------------------------------------------------------------------- #

@router.get("/deliverables", summary="List deliverables")
async def list_deliverables(
    user: CurrentUser,
    session: DbSession,
    build_run_id: uuid.UUID | None = None,
    pending_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    service = DeliveryService(session)
    items = await service.list_deliverables(
        owner_id=user.id, build_run_id=build_run_id, pending_only=pending_only, limit=limit
    )
    return ok({"deliverables": [item.as_dict() for item in items], "count": len(items)})


@router.get("/deliverables/{deliverable_id}", summary="One deliverable, with its evidence")
async def get_deliverable(
    deliverable_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    service = DeliveryService(session)
    item = await service.get_deliverable(owner_id=user.id, deliverable_id=deliverable_id)
    return ok(item.as_dict())


@router.get("/deliverables/{deliverable_id}/versions", summary="Version chain")
async def deliverable_versions(
    deliverable_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    service = DeliveryService(session)
    chain = await service.version_chain(owner_id=user.id, deliverable_id=deliverable_id)
    return ok({"versions": [item.as_dict(include_verification=False) for item in chain]})


@router.post("/deliverables/{deliverable_id}/verify", summary="Re-run verification")
async def verify_deliverable(
    deliverable_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    service = DeliveryService(session)
    result = await service.verify_deliverable(owner_id=user.id, deliverable_id=deliverable_id)
    return ok(result)


@router.post("/deliverables/{deliverable_id}/accept", summary="Accept a deliverable")
async def accept_deliverable(
    deliverable_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    service = DeliveryService(session)
    item = await service.accept(owner_id=user.id, deliverable_id=deliverable_id)
    return ok(item.as_dict(), message="已验收，它会记住这个标准")


@router.post("/deliverables/{deliverable_id}/reject", summary="Reject with a reason")
async def reject_deliverable(
    deliverable_id: uuid.UUID, payload: RejectRequest, user: CurrentUser, session: DbSession
) -> dict[str, Any]:
    service = DeliveryService(session)
    item = await service.reject(
        owner_id=user.id, deliverable_id=deliverable_id, feedback=payload.feedback
    )
    return ok(item.as_dict(), message="已退回，这个原因会变成一条待确认的偏好")


@router.get("/deliverables/{deliverable_id}/download", summary="Download the file")
async def download_deliverable(
    deliverable_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> FileResponse:
    service = DeliveryService(session)
    item = await service.get_deliverable(owner_id=user.id, deliverable_id=deliverable_id)
    target = Path(item.path)
    if not target.is_file():
        raise NotFoundError("文件已不在磁盘上")
    return FileResponse(
        path=target,
        filename=target.name,
        media_type="application/octet-stream",
    )


@router.post("/build-runs/{build_run_id}/deliverables", status_code=201, summary="Register a produced file")
async def register_deliverable(
    build_run_id: uuid.UUID,
    payload: DeliverableCreate,
    user: CurrentUser,
    session: DbSession,
) -> dict[str, Any]:
    """Attach a file to a run, verifying it before it is registered.

    Verification runs first on purpose: a deliverable row that has never been read back
    is a claim, and the product's whole position is that a file is not finished until it
    has been.
    """
    service = DeliveryService(session)
    run = await service.get_build_run(owner_id=user.id, build_run_id=build_run_id)

    from app.ai.memory import ContextAssembler

    assembler = ContextAssembler(session, user_id=user.id)
    assembled = await assembler.assemble(include_knowledge=False)

    item = await service.record_deliverable(
        run=run,
        path=payload.path,
        kind=payload.kind,
        title=payload.title,
        style=payload.style,
        parent_id=payload.parent_id,
        applied_preferences={"digest": assembled.profile_digest[:400]} if not assembled.is_empty else {},
    )
    result = await service.verify_deliverable(owner_id=user.id, deliverable_id=item.id)
    return ok({"deliverable": item.as_dict(), "verification": result}, message="产出物已登记并校验")


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #
@router.get("/delivery/summary", summary="Counts for the home page")
async def delivery_summary(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    service = DeliveryService(session)
    return ok(await service.summary(owner_id=user.id))


@router.get("/delivery/states", summary="The state machine, for the UI")
async def delivery_states() -> dict[str, Any]:
    """The states and which of them are terminal, so the UI does not hardcode them."""
    return ok(
        {
            "states": [
                {"value": state.value, "terminal": state.is_terminal, "active": state.is_active}
                for state in BuildStatus
            ]
        }
    )

"""Dashboard statistics and knowledge-graph endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.core.response import ok
from app.services.stats_service import GraphService, StatsService

router = APIRouter(tags=["insights"])


@router.get("/stats/dashboard", summary="Aggregated dashboard data (all values from the database)")
async def dashboard(user: CurrentUser, session: DbSession) -> dict:
    return ok(await StatsService(session).dashboard(owner_id=user.id))


@router.get("/graph", summary="Knowledge / task / project relationship graph")
async def graph(
    user: CurrentUser,
    session: DbSession,
    project_id: uuid.UUID | None = None,
    limit: int = Query(250, ge=20, le=800),
    include_concepts: bool = True,
    include_runs: bool = True,
) -> dict:
    return ok(
        await GraphService(session).build(
            owner_id=user.id,
            project_id=project_id,
            limit=limit,
            include_concepts=include_concepts,
            include_runs=include_runs,
        )
    )

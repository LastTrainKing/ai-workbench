"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1.agent import router as agent_router
from app.api.v1.agent_runs import router as agent_runs_router
from app.api.v1.ai import airuns_router
from app.api.v1.ai import router as ai_router
from app.api.v1.ai_config import router as ai_config_router
from app.api.v1.auth import router as auth_router
from app.api.v1.auth import user_router
from app.api.v1.context import router as context_router
from app.api.v1.delivery import router as delivery_router
from app.api.v1.health import router as health_router
from app.api.v1.insights import router as insights_router
from app.api.v1.knowledge import documents_router, search_router
from app.api.v1.knowledge import router as knowledge_router
from app.api.v1.projects import router as projects_router
from app.api.v1.tasks import notes_router
from app.api.v1.tasks import router as tasks_router

api_router = APIRouter()

# Health is intentionally unauthenticated so orchestrators can probe it.
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(user_router)
api_router.include_router(projects_router)
api_router.include_router(knowledge_router)
api_router.include_router(documents_router)
api_router.include_router(search_router)
api_router.include_router(tasks_router)
api_router.include_router(notes_router)
api_router.include_router(ai_router)
api_router.include_router(ai_config_router)
api_router.include_router(agent_router)
api_router.include_router(agent_runs_router)
api_router.include_router(context_router)
api_router.include_router(delivery_router)
api_router.include_router(airuns_router)
api_router.include_router(insights_router)

__all__ = ["api_router"]

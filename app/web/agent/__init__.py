from fastapi import APIRouter

from app.web.agent.performance import router as performance_router
from app.web.agent.reports import router as reports_router

router = APIRouter(tags=["web-agent"])
router.include_router(reports_router)
router.include_router(performance_router)

__all__ = ["router"]

from fastapi import APIRouter


def build_router() -> APIRouter:
    from app.web.agent.performance import router as performance_router
    from app.web.agent.reports import router as reports_router
    from app.web.agent.workqueue import router as workqueue_router

    router = APIRouter(tags=["web-agent"])
    router.include_router(performance_router)
    router.include_router(reports_router)
    router.include_router(workqueue_router)
    return router


__all__ = ["build_router"]

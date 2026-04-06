"""Web route builder entrypoints."""

from fastapi import APIRouter


def build_router() -> APIRouter:
    from app.web.admin import build_router as build_admin_router
    from app.web.agent import build_router as build_agent_router
    from app.web.auth import build_router as build_auth_router
    from app.web.public import build_router as build_public_router
    from app.web.reseller import build_router as build_reseller_router
    from app.web.vendor import build_router as build_vendor_router

    router = APIRouter(tags=["web"])
    router.include_router(build_auth_router())
    router.include_router(build_admin_router())
    router.include_router(build_agent_router())
    router.include_router(build_vendor_router())
    router.include_router(build_reseller_router())
    router.include_router(build_public_router())
    return router


__all__ = ["build_router"]

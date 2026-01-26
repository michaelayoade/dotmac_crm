from fastapi import APIRouter

from app.web.vendor.auth import router as vendor_auth_router
from app.web.vendor.routes import router as vendor_routes_router

router = APIRouter(tags=["web-vendor"])
router.include_router(vendor_auth_router)
router.include_router(vendor_routes_router)

__all__ = ["router"]

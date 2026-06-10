from fastapi import APIRouter

from app.api.field.attachments import router as attachments_router
from app.api.field.devices import router as devices_router

router = APIRouter(prefix="/field")
router.include_router(attachments_router)
router.include_router(devices_router)

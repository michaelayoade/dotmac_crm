from fastapi import APIRouter

from app.api.field.attachments import router as attachments_router
from app.api.field.devices import router as devices_router
from app.api.field.equipment import router as equipment_router
from app.api.field.jobs import router as jobs_router
from app.api.field.materials import router as materials_router
from app.api.field.notes import router as notes_router
from app.api.field.schedule import router as schedule_router
from app.api.field.transitions import router as transitions_router
from app.api.field.worklogs import router as worklogs_router

router = APIRouter(prefix="/field")
router.include_router(attachments_router)
router.include_router(devices_router)
router.include_router(equipment_router)
router.include_router(jobs_router)
router.include_router(materials_router)
router.include_router(notes_router)
router.include_router(schedule_router)
router.include_router(transitions_router)
router.include_router(worklogs_router)

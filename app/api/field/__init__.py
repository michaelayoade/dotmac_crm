from fastapi import APIRouter, Depends

from app.api.field.attachments import router as attachments_router
from app.api.field.devices import router as devices_router
from app.api.field.equipment import router as equipment_router
from app.api.field.jobs import router as jobs_router
from app.api.field.locations import router as locations_router
from app.api.field.map_assets import router as map_assets_router
from app.api.field.materials import router as materials_router
from app.api.field.notes import router as notes_router
from app.api.field.schedule import router as schedule_router
from app.api.field.transitions import router as transitions_router
from app.api.field.vendor_projects import router as vendor_projects_router
from app.api.field.voice import router as voice_router
from app.api.field.worklogs import router as worklogs_router
from app.services.auth_dependencies import require_technician

router = APIRouter(prefix="/field")

# Staff field-app routers require the caller to actually be a field technician,
# not merely an authenticated user named on a work order. Excluded:
#   - attachments / devices: shared with vendors (project evidence, push tokens
#     are owned by person XOR vendor_user), each already scoped per-resource.
#   - vendor_projects: guarded by its own require_vendor_token dependency.
_technician = [Depends(require_technician)]

router.include_router(attachments_router)
router.include_router(devices_router)
router.include_router(equipment_router, dependencies=_technician)
router.include_router(jobs_router, dependencies=_technician)
router.include_router(locations_router, dependencies=_technician)
router.include_router(map_assets_router, dependencies=_technician)
router.include_router(materials_router, dependencies=_technician)
router.include_router(notes_router, dependencies=_technician)
router.include_router(schedule_router, dependencies=_technician)
router.include_router(transitions_router, dependencies=_technician)
router.include_router(vendor_projects_router)
router.include_router(voice_router, dependencies=_technician)
router.include_router(worklogs_router, dependencies=_technician)

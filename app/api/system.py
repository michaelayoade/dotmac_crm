"""System health JSON API — host vitals + severity for external monitoring.

Thin wrapper over ``app.services.system_health``. The existing ``/health`` is a
trivial liveness probe and ``/metrics`` is Prometheus app-metrics; this exposes
the structured host CPU/memory/disk/load/uptime payload (with ok/warning/critical
evaluation) that the admin system page already renders.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services import system_health as system_health_service
from app.services.auth_dependencies import require_permission

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health", dependencies=[Depends(require_permission("system:monitoring:read"))])
def get_system_health(db: Session = Depends(get_db)) -> dict:
    return system_health_service.system_health_report(db)

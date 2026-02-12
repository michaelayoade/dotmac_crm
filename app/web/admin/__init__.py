"""Admin web routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal

# Import auth helpers first to avoid circular imports
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats
from app.web.admin.admin_hub import router as admin_hub_router
from app.web.admin.automations import router as automations_router
from app.web.admin.campaigns import router as campaigns_router
from app.web.admin.crm import router as crm_router

# Import routers after auth helpers are available
from app.web.admin.dashboard import router as dashboard_router
from app.web.admin.gis import router as gis_router
from app.web.admin.integrations import router as integrations_router
from app.web.admin.inventory import router as inventory_router
from app.web.admin.legal import router as legal_router
from app.web.admin.material_requests import router as material_requests_router
from app.web.admin.meta_oauth import router as meta_oauth_router
from app.web.admin.network import router as network_router
from app.web.admin.notifications import router as notifications_router
from app.web.admin.operations import router as operations_router
from app.web.admin.projects import router as projects_router
from app.web.admin.reports import router as reports_router
from app.web.admin.service_teams import router as service_teams_router
from app.web.admin.subscribers import router as subscribers_router
from app.web.admin.surveys import router as surveys_router
from app.web.admin.system import router as system_router
from app.web.admin.tickets import router as tickets_router
from app.web.admin.vendors import router as vendors_router
from app.web.auth.dependencies import require_web_auth

_META_OAUTH_CALLBACK_PATH = "/admin/crm/meta/callback"


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_web_auth_or_meta_callback(
    request: Request,
    db: Session = Depends(_get_db),
):
    if request.url.path == _META_OAUTH_CALLBACK_PATH:
        return {}
    return require_web_auth(request, db)


router = APIRouter(
    prefix="/admin",
    tags=["web-admin"],
    dependencies=[Depends(require_web_auth_or_meta_callback)],
)


@router.get("")
def admin_root():
    return RedirectResponse(url="/admin/dashboard", status_code=303)


@router.get("/tickets")
def admin_tickets_alias():
    return RedirectResponse(url="/admin/support/tickets", status_code=302)


@router.get("/tickets/new")
def admin_tickets_new_alias():
    return RedirectResponse(url="/admin/support/tickets/create", status_code=302)


# Include all admin sub-routers
router.include_router(dashboard_router)
router.include_router(system_router)
router.include_router(projects_router)
router.include_router(tickets_router)
router.include_router(inventory_router)
router.include_router(gis_router)
router.include_router(integrations_router)
router.include_router(vendors_router)
router.include_router(campaigns_router)
router.include_router(surveys_router)
router.include_router(crm_router)
router.include_router(subscribers_router)
router.include_router(notifications_router)
router.include_router(network_router)
router.include_router(legal_router, prefix="/system")
router.include_router(meta_oauth_router)
router.include_router(admin_hub_router, prefix="/system")
router.include_router(operations_router)
router.include_router(material_requests_router)
router.include_router(service_teams_router)
router.include_router(reports_router)
router.include_router(automations_router)

__all__ = ["get_current_user", "get_sidebar_stats", "router"]

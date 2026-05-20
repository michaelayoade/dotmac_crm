"""Admin web route builder and shared helpers."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import QueryParams

from app.db import get_db
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats
from app.web.auth.dependencies import require_web_auth

_META_OAUTH_CALLBACK_PATH = "/admin/crm/meta/callback"


def require_web_auth_or_meta_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    if request.url.path == _META_OAUTH_CALLBACK_PATH:
        return {}
    return require_web_auth(request, db)


def _redirect_with_query(path: str, query_params: QueryParams) -> RedirectResponse:
    query_string = str(query_params)
    target = f"{path}?{query_string}" if query_string else path
    return RedirectResponse(url=target, status_code=302)


def build_router() -> APIRouter:
    from app.web.admin.admin_hub import router as admin_hub_router
    from app.web.admin.ai import router as ai_router
    from app.web.admin.automations import router as automations_router
    from app.web.admin.billing_risk import customer_retention_router
    from app.web.admin.billing_risk import router as billing_risk_router
    from app.web.admin.campaigns import router as campaigns_router
    from app.web.admin.crm import router as crm_router
    from app.web.admin.dashboard import router as dashboard_router
    from app.web.admin.data_quality import router as data_quality_router
    from app.web.admin.gis import router as gis_router
    from app.web.admin.integrations import router as integrations_router
    from app.web.admin.intelligence import router as intelligence_router
    from app.web.admin.inventory import router as inventory_router
    from app.web.admin.legal import router as legal_router
    from app.web.admin.material_requests import router as material_requests_router
    from app.web.admin.meta_oauth import router as meta_oauth_router
    from app.web.admin.network import router as network_router
    from app.web.admin.notifications import router as notifications_router
    from app.web.admin.operations import router as operations_router
    from app.web.admin.performance import router as performance_router
    from app.web.admin.projects import router as projects_router
    from app.web.admin.reports import router as reports_router
    from app.web.admin.service_teams import router as service_teams_router
    from app.web.admin.storage import router as storage_router
    from app.web.admin.subscribers import router as subscribers_router
    from app.web.admin.surveys import router as surveys_router
    from app.web.admin.system import router as system_router
    from app.web.admin.tickets import router as tickets_router
    from app.web.admin.user_guide import router as user_guide_router
    from app.web.admin.vendors import router as vendors_router

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

    @router.get("/tickets/{ticket_ref}")
    def admin_ticket_detail_alias(ticket_ref: str):
        return RedirectResponse(url=f"/admin/support/tickets/{ticket_ref}", status_code=302)

    @router.get("/tickets/{ticket_ref}/edit")
    def admin_ticket_edit_alias(ticket_ref: str):
        return RedirectResponse(url=f"/admin/support/tickets/{ticket_ref}/edit", status_code=302)

    @router.get("/inbox")
    def admin_inbox_alias(request: Request):
        return _redirect_with_query("/admin/crm/inbox", request.query_params)

    @router.get("/leads")
    def admin_leads_alias(request: Request):
        return _redirect_with_query("/admin/crm/leads", request.query_params)

    @router.get("/leads/new")
    def admin_leads_new_alias(request: Request):
        return _redirect_with_query("/admin/crm/leads/new", request.query_params)

    @router.get("/leads/{lead_id}")
    def admin_lead_detail_alias(lead_id: str, request: Request):
        return _redirect_with_query(f"/admin/crm/leads/{lead_id}", request.query_params)

    @router.get("/leads/{lead_id}/edit")
    def admin_lead_edit_alias(lead_id: str, request: Request):
        return _redirect_with_query(f"/admin/crm/leads/{lead_id}/edit", request.query_params)

    @router.get("/quotes")
    def admin_quotes_alias(request: Request):
        return _redirect_with_query("/admin/crm/quotes", request.query_params)

    @router.get("/quotes/new")
    def admin_quotes_new_alias(request: Request):
        return _redirect_with_query("/admin/crm/quotes/new", request.query_params)

    @router.get("/quotes/{quote_id}")
    def admin_quote_detail_alias(quote_id: str, request: Request):
        return _redirect_with_query(f"/admin/crm/quotes/{quote_id}", request.query_params)

    @router.get("/quotes/{quote_id}/edit")
    def admin_quote_edit_alias(quote_id: str, request: Request):
        return _redirect_with_query(f"/admin/crm/quotes/{quote_id}/edit", request.query_params)

    router.include_router(dashboard_router)
    router.include_router(system_router)
    router.include_router(projects_router)
    router.include_router(tickets_router)
    router.include_router(storage_router)
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
    router.include_router(customer_retention_router)
    router.include_router(billing_risk_router)
    router.include_router(reports_router)
    router.include_router(performance_router)
    router.include_router(automations_router)
    router.include_router(ai_router)
    router.include_router(intelligence_router)
    router.include_router(data_quality_router)
    router.include_router(user_guide_router)
    return router


__all__ = ["build_router", "get_current_user", "get_sidebar_stats", "require_web_auth_or_meta_callback"]

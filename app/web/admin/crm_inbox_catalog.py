"""CRM inbox catalog and settings redirect routes."""

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.logging import get_logger
from app.services import crm as crm_service

router = APIRouter(tags=["web-admin-crm"])
logger = get_logger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_current_roles(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        roles = auth.get("roles") or []
        if isinstance(roles, list):
            return [str(role) for role in roles]
    return []


def _get_current_scopes(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        scopes = auth.get("scopes") or []
        if isinstance(scopes, list):
            return [str(scope) for scope in scopes]
    return []


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme in {"http", "https", "mailto", "tel"}:
        return True
    return parsed.scheme == ""


def _inbox_settings_redirect(next_url: str | None = None):
    if next_url and _is_safe_url(next_url):
        return RedirectResponse(url=next_url, status_code=303)
    return RedirectResponse(url="/admin/crm/inbox/settings", status_code=303)


@router.get("/inbox/whatsapp-templates", response_class=JSONResponse)
async def whatsapp_templates(
    request: Request,
    target_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.permissions import can_view_inbox
    from app.services.crm.inbox.whatsapp_templates import list_whatsapp_templates

    if not can_view_inbox(_get_current_roles(request), _get_current_scopes(request)):
        return JSONResponse({"templates": [], "error": "Forbidden"}, status_code=403)

    templates = list_whatsapp_templates(db, target_id=target_id)
    return JSONResponse({"templates": templates})


@router.get("/inbox/whatsapp-contacts", response_class=JSONResponse)
async def whatsapp_contacts(
    request: Request,
    search: str | None = Query(None),
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.permissions import can_view_inbox

    if not can_view_inbox(_get_current_roles(request), _get_current_scopes(request)):
        return JSONResponse({"contacts": [], "error": "Forbidden"}, status_code=403)

    try:
        contacts = crm_service.contacts.list_whatsapp_contacts(
            db,
            search=search,
            limit=20,
            offset=0,
        )
        return JSONResponse({"contacts": contacts})
    except Exception:
        logger.exception("whatsapp_contact_list_failed")
        return JSONResponse({"contacts": [], "error": "Failed to load contacts"}, status_code=500)


@router.get("/inbox/email-connector", response_class=HTMLResponse)
def email_connector_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/whatsapp-connector", response_class=HTMLResponse)
def whatsapp_connector_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/email-poll", response_class=HTMLResponse)
def email_poll_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/email-check", response_class=HTMLResponse)
def email_check_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/email-reset-cursor", response_class=HTMLResponse)
def email_reset_cursor_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/email-polling/reset", response_class=HTMLResponse)
def email_polling_reset_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/email-delete", response_class=HTMLResponse)
def email_delete_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/email-activate", response_class=HTMLResponse)
def email_activate_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/teams", response_class=HTMLResponse)
def inbox_teams_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/agents", response_class=HTMLResponse)
def inbox_agents_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/agent-teams", response_class=HTMLResponse)
def inbox_agent_teams_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)

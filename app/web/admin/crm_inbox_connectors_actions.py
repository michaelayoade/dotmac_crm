"""CRM inbox connector/action POST routes."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal

router = APIRouter(tags=["web-admin-crm"])


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


def _as_str(value: object | None) -> str | None:
    if isinstance(value, str):
        return value
    return None


@router.post("/inbox/email-connector", response_class=HTMLResponse)
async def configure_email_connector(
    request: Request,
    name: str = Form("CRM Email"),
    target_id: str | None = Form(None),
    connector_id: str | None = Form(None),
    create_new: str | None = Form(None),
    username: str | None = Form(None),
    password: str | None = Form(None),
    from_email: str | None = Form(None),
    from_name: str | None = Form(None),
    smtp_host: str | None = Form(None),
    smtp_port: str | None = Form(None),
    smtp_use_tls: str | None = Form(None),
    smtp_use_ssl: str | None = Form(None),
    skip_smtp_test: str | None = Form(None),
    polling_enabled: str | None = Form(None),
    smtp_enabled: str | None = Form(None),
    imap_host: str | None = Form(None),
    imap_port: str | None = Form(None),
    imap_use_ssl: str | None = Form(None),
    imap_mailbox: str | None = Form(None),
    imap_search_all: str | None = Form(None),
    pop3_host: str | None = Form(None),
    pop3_port: str | None = Form(None),
    pop3_use_ssl: str | None = Form(None),
    poll_interval_seconds: str | None = Form(None),
    rate_limit_per_minute: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.connectors_admin import configure_email_connector

    form = await request.form()
    result = configure_email_connector(
        db,
        form=form,
        defaults={
            "name": name,
            "target_id": target_id,
            "connector_id": connector_id,
            "create_new": create_new,
            "username": username,
            "password": password,
            "from_email": from_email,
            "from_name": from_name,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_use_tls": smtp_use_tls,
            "smtp_use_ssl": smtp_use_ssl,
            "skip_smtp_test": skip_smtp_test,
            "polling_enabled": polling_enabled,
            "smtp_enabled": smtp_enabled,
            "imap_host": imap_host,
            "imap_port": imap_port,
            "imap_use_ssl": imap_use_ssl,
            "imap_mailbox": imap_mailbox,
            "imap_search_all": imap_search_all,
            "pop3_host": pop3_host,
            "pop3_port": pop3_port,
            "pop3_use_ssl": pop3_use_ssl,
            "poll_interval_seconds": poll_interval_seconds,
            "rate_limit_per_minute": rate_limit_per_minute,
        },
        next_url=request.query_params.get("next"),
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    url = f"{result.next_url}?{result.query_key}=1"
    if result.error_detail and result.query_key == "email_error":
        detail = quote(result.error_detail or "Email validation failed", safe="")
        url = f"{result.next_url}?email_error=1&email_error_detail={detail}"
    return RedirectResponse(url=url, status_code=303)


@router.post("/inbox/whatsapp-connector", response_class=HTMLResponse)
async def configure_whatsapp_connector(
    request: Request,
    name: str = Form("CRM WhatsApp"),
    target_id: str | None = Form(None),
    connector_id: str | None = Form(None),
    create_new: str | None = Form(None),
    access_token: str | None = Form(None),
    phone_number_id: str | None = Form(None),
    business_account_id: str | None = Form(None),
    base_url: str | None = Form(None),
    rate_limit_per_minute: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.connectors_admin import configure_whatsapp_connector

    form = await request.form()
    result = configure_whatsapp_connector(
        db,
        form=form,
        defaults={
            "name": name,
            "target_id": target_id,
            "connector_id": connector_id,
            "create_new": create_new,
            "access_token": access_token,
            "phone_number_id": phone_number_id,
            "business_account_id": business_account_id,
            "base_url": base_url,
            "rate_limit_per_minute": rate_limit_per_minute,
        },
        next_url=request.query_params.get("next"),
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    url = f"{result.next_url}?{result.query_key}=1"
    return RedirectResponse(url=url, status_code=303)


@router.post("/inbox/email-poll", response_class=HTMLResponse)
async def poll_email_channel(
    request: Request,
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.email_actions import poll_email_channel

    result = poll_email_channel(
        db,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    return HTMLResponse(result.message, status_code=result.status_code)


@router.post("/inbox/email-check", response_class=HTMLResponse)
async def check_email_inbox(
    request: Request,
    target_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    form = await request.form()
    target_id_value = _as_str(form.get("target_id")) if "target_id" in form else target_id
    from app.services.crm.inbox.email_actions import check_email_inbox

    result = check_email_inbox(
        db,
        target_id_value,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    return HTMLResponse(result.message, status_code=result.status_code)


@router.post("/inbox/email-reset-cursor", response_class=HTMLResponse)
async def reset_email_imap_cursor(
    request: Request,
    target_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    form = await request.form()
    target_id_value = _as_str(form.get("target_id")) if "target_id" in form else target_id
    from app.services.crm.inbox.email_actions import reset_email_imap_cursor

    result = reset_email_imap_cursor(
        db,
        target_id_value,
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    return HTMLResponse(result.message, status_code=result.status_code)


@router.post("/inbox/email-polling/reset", response_class=HTMLResponse)
async def reset_email_polling_runs(
    request: Request,
    target_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    form = await request.form()
    target_id_value = _as_str(form.get("target_id")) if "target_id" in form else target_id
    from app.services.crm.inbox.email_actions import reset_email_polling_runs

    result = reset_email_polling_runs(
        db,
        target_id_value,
        request.query_params.get("next"),
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    return RedirectResponse(url=f"{result.next_url}?{result.query_key}=1", status_code=303)


@router.post("/inbox/email-delete", response_class=HTMLResponse)
async def delete_email_inbox(
    request: Request,
    target_id: str | None = Form(None),
    connector_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    form = await request.form()
    target_id_value = _as_str(form.get("target_id")) if "target_id" in form else target_id
    connector_id_value = _as_str(form.get("connector_id")) if "connector_id" in form else connector_id
    from app.services.crm.inbox.email_actions import delete_email_inbox

    result = delete_email_inbox(
        db,
        target_id_value,
        connector_id_value,
        request.query_params.get("next"),
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if not result.ok and result.error_detail == "Inbox target is required.":
        return HTMLResponse(
            "<p class='text-xs text-red-400'>Inbox target is required.</p>",
            status_code=400,
        )
    if not result.ok and result.error_detail and result.query_key == "email_error":
        detail = quote(result.error_detail or "Failed to delete inbox", safe="")
        return RedirectResponse(
            url=f"{result.next_url}?email_error=1&email_error_detail={detail}",
            status_code=303,
        )
    return RedirectResponse(url=f"{result.next_url}?{result.query_key}=1", status_code=303)


@router.post("/inbox/email-activate", response_class=HTMLResponse)
async def activate_email_inbox(
    request: Request,
    target_id: str | None = Form(None),
    connector_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    form = await request.form()
    target_id_value = _as_str(form.get("target_id")) if "target_id" in form else target_id
    connector_id_value = _as_str(form.get("connector_id")) if "connector_id" in form else connector_id
    from app.services.crm.inbox.email_actions import activate_email_inbox

    result = activate_email_inbox(
        db,
        target_id_value,
        connector_id_value,
        request.query_params.get("next"),
        roles=_get_current_roles(request),
        scopes=_get_current_scopes(request),
    )
    if not result.ok and result.error_detail == "Inbox target is required.":
        return HTMLResponse(
            "<p class='text-xs text-red-400'>Inbox target is required.</p>",
            status_code=400,
        )
    if not result.ok and result.error_detail and result.query_key == "email_error":
        detail = quote(result.error_detail or "Failed to activate inbox", safe="")
        return RedirectResponse(
            url=f"{result.next_url}?email_error=1&email_error_detail={detail}",
            status_code=303,
        )
    return RedirectResponse(url=f"{result.next_url}?{result.query_key}=1", status_code=303)

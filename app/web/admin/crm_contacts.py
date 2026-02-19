"""CRM contacts routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.logging import get_logger
from app.services.audit_helpers import recent_activity_for_paths
from app.services.crm.web_contacts import (
    ContactUpsertInput,
    contact_detail_data,
    contact_form_error_context,
    convert_contact_to_subscriber,
    create_contact,
    delete_contact,
    edit_contact_form_context,
    list_contacts_page_data,
    merge_contacts,
    merge_labels,
    new_contact_form_context,
    update_contact,
)

router = APIRouter(tags=["web-admin-crm"])
templates = Jinja2Templates(directory="templates")
logger = get_logger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _crm_base_context(*args, **kwargs):
    from app.web.admin.crm import _crm_base_context as _shared_crm_base_context

    return _shared_crm_base_context(*args, **kwargs)


def _is_safe_url(value: str) -> bool:
    from app.web.admin.crm import _is_safe_url as _shared_is_safe_url

    return _shared_is_safe_url(value)


def _load_crm_agent_team_options(db: Session):
    from app.web.admin.crm import _load_crm_agent_team_options as _shared_load_crm_agent_team_options

    return _shared_load_crm_agent_team_options(db)


@router.get("/contacts", response_class=HTMLResponse)
def crm_contacts_list(
    request: Request,
    search: str | None = None,
    party_status: str | None = None,
    is_active: str | None = None,
    has_channels: str | None = None,
    linked_to_org: str | None = None,
    order_by: str = Query("created_at"),
    order_dir: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    db: Session = Depends(get_db),
):
    context = _crm_base_context(request, db, "contacts")
    context.update(
        list_contacts_page_data(
            db,
            search=search,
            party_status=party_status,
            is_active=is_active,
            has_channels=has_channels,
            linked_to_org=linked_to_org,
            order_by=order_by,
            order_dir=order_dir,
            page=page,
            per_page=per_page,
        )
    )
    context["recent_activities"] = recent_activity_for_paths(db, ["/admin/crm"])
    return templates.TemplateResponse("admin/crm/contacts.html", context)


@router.get("/contacts/merge", response_class=HTMLResponse)
def crm_contacts_merge_form(
    request: Request,
    source_id: str | None = Query(default=None),
    source_label: str | None = Query(default=None),
    target_id: str | None = Query(default=None),
    target_label: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "source_id": source_id or "",
            "source_label": source_label or "",
            "target_id": target_id or "",
            "target_label": target_label or "",
        }
    )
    return templates.TemplateResponse("admin/crm/contact_merge.html", context)


@router.post("/contacts/merge", response_class=HTMLResponse)
def crm_contacts_merge_submit(
    request: Request,
    source_person_id: str = Form(...),
    target_person_id: str = Form(...),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_current_user

    source_value = (source_person_id or "").strip()
    target_value = (target_person_id or "").strip()
    try:
        current_user = get_current_user(request)
        merged_by_person_id = current_user.get("person_id") if current_user else None
        target_uuid = merge_contacts(
            db,
            source_person_id=source_value,
            target_person_id=target_value,
            merged_by_person_id=merged_by_person_id,
        )
        return RedirectResponse(
            url=f"/admin/crm/contacts/{target_uuid}?next=/admin/crm/contacts",
            status_code=303,
        )
    except (ValidationError, ValueError) as exc:
        db.rollback()
        error = str(exc) or "Unable to merge contacts."
        logger.exception(
            "contact_merge_validation_failed source_id=%s target_id=%s",
            source_value,
            target_value,
        )
    except Exception as exc:
        db.rollback()
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        if not error:
            error = "Unable to merge contacts."
        logger.exception(
            "contact_merge_failed source_id=%s target_id=%s",
            source_value,
            target_value,
        )

    source_label, target_label = merge_labels(
        db,
        source_person_id=source_value,
        target_person_id=target_value,
    )
    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "error": error,
            "source_id": source_value,
            "source_label": source_label,
            "target_id": target_value,
            "target_label": target_label,
        }
    )
    return templates.TemplateResponse("admin/crm/contact_merge.html", context, status_code=400)


@router.get("/contacts/new", response_class=HTMLResponse)
def crm_contact_new(request: Request, db: Session = Depends(get_db)):
    context = _crm_base_context(request, db, "contacts")
    context.update(new_contact_form_context())
    return templates.TemplateResponse("admin/crm/contact_form.html", context)


@router.post("/contacts", response_class=HTMLResponse)
def crm_contact_create(
    request: Request,
    display_name: str | None = Form(None),
    splynx_id: str | None = Form(None),
    emails: list[str] = Form([]),
    phones: list[str] = Form([]),
    whatsapp_phones: list[str] = Form([]),
    primary_email: str | None = Form(None),
    primary_phone: str | None = Form(None),
    address_line1: str | None = Form(None),
    address_line2: str | None = Form(None),
    city: str | None = Form(None),
    region: str | None = Form(None),
    postal_code: str | None = Form(None),
    country_code: str | None = Form(None),
    person_id: str | None = Form(None),
    organization_id: str | None = Form(None),
    notes: str | None = Form(None),
    party_status: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    form_input = ContactUpsertInput(
        display_name=display_name,
        splynx_id=splynx_id,
        emails=emails,
        phones=phones,
        whatsapp_phones=whatsapp_phones,
        primary_email=primary_email,
        primary_phone=primary_phone,
        address_line1=address_line1,
        address_line2=address_line2,
        city=city,
        region=region,
        postal_code=postal_code,
        country_code=country_code,
        person_id=person_id,
        organization_id=organization_id,
        notes=notes,
        party_status=party_status,
        is_active=is_active,
    )

    try:
        create_contact(db, form_input)
        return RedirectResponse(url="/admin/crm/contacts", status_code=303)
    except (ValidationError, ValueError) as exc:
        db.rollback()
        error = str(exc) or "Unable to save contact."
        logger.exception(
            "contact_create_validation_failed display_name=%s phones=%s whatsapp_phones=%s emails=%s request_id=%s",
            (display_name or "").strip(),
            phones,
            whatsapp_phones,
            emails,
            getattr(request.state, "request_id", None),
        )
    except Exception as exc:
        db.rollback()
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        if not error:
            error = "Unable to save contact."
        logger.exception(
            "contact_create_failed display_name=%s phones=%s whatsapp_phones=%s emails=%s request_id=%s",
            (display_name or "").strip(),
            phones,
            whatsapp_phones,
            emails,
            getattr(request.state, "request_id", None),
        )

    context = _crm_base_context(request, db, "contacts")
    context.update(contact_form_error_context(db, form=form_input, mode="create"))
    context["error"] = error
    return templates.TemplateResponse("admin/crm/contact_form.html", context, status_code=400)


@router.get("/contacts/{contact_id}/edit", response_class=HTMLResponse)
def crm_contact_edit(request: Request, contact_id: str, db: Session = Depends(get_db)):
    edit_context = edit_contact_form_context(db, contact_id)
    if not edit_context:
        return RedirectResponse(url="/admin/crm/contacts", status_code=303)

    context = _crm_base_context(request, db, "contacts")
    context.update(edit_context)
    return templates.TemplateResponse("admin/crm/contact_form.html", context)


@router.post("/contacts/{contact_id}/edit", response_class=HTMLResponse)
def crm_contact_update(
    request: Request,
    contact_id: str,
    display_name: str | None = Form(None),
    splynx_id: str | None = Form(None),
    emails: list[str] = Form([]),
    phones: list[str] = Form([]),
    whatsapp_phones: list[str] = Form([]),
    primary_email: str | None = Form(None),
    primary_phone: str | None = Form(None),
    address_line1: str | None = Form(None),
    address_line2: str | None = Form(None),
    city: str | None = Form(None),
    region: str | None = Form(None),
    postal_code: str | None = Form(None),
    country_code: str | None = Form(None),
    person_id: str | None = Form(None),
    organization_id: str | None = Form(None),
    notes: str | None = Form(None),
    party_status: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    form_input = ContactUpsertInput(
        display_name=display_name,
        splynx_id=splynx_id,
        emails=emails,
        phones=phones,
        whatsapp_phones=whatsapp_phones,
        primary_email=primary_email,
        primary_phone=primary_phone,
        address_line1=address_line1,
        address_line2=address_line2,
        city=city,
        region=region,
        postal_code=postal_code,
        country_code=country_code,
        person_id=person_id,
        organization_id=organization_id,
        notes=notes,
        party_status=party_status,
        is_active=is_active,
    )

    try:
        update_contact(db, contact_id, form_input)
        return RedirectResponse(url="/admin/crm/contacts", status_code=303)
    except (ValidationError, ValueError) as exc:
        db.rollback()
        error = str(exc) or "Unable to save contact."
        logger.exception(
            "contact_update_validation_failed contact_id=%s phones=%s whatsapp_phones=%s emails=%s",
            contact_id,
            phones,
            whatsapp_phones,
            emails,
        )
    except Exception as exc:
        db.rollback()
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        if not error:
            error = "Unable to save contact."
        logger.exception(
            "contact_update_failed contact_id=%s phones=%s whatsapp_phones=%s emails=%s",
            contact_id,
            phones,
            whatsapp_phones,
            emails,
        )

    context = _crm_base_context(request, db, "contacts")
    context.update(contact_form_error_context(db, form=form_input, mode="update", contact_id=contact_id))
    context["error"] = error
    return templates.TemplateResponse("admin/crm/contact_form.html", context, status_code=400)


@router.post("/contacts/{contact_id}/delete", response_class=HTMLResponse)
def crm_contact_delete(request: Request, contact_id: str, db: Session = Depends(get_db)):
    _ = request
    delete_contact(db, contact_id)
    return RedirectResponse(url="/admin/crm/contacts", status_code=303)


@router.post("/contacts/{person_id}/convert", response_class=HTMLResponse)
def crm_contact_convert(
    person_id: UUID,
    subscriber_type: str = Form("person"),
    account_status: str = Form("active"),
    db: Session = Depends(get_db),
):
    _ = subscriber_type
    subscriber_id = convert_contact_to_subscriber(db, person_id, account_status)
    if subscriber_id is None:
        return RedirectResponse(url="/admin/crm/contacts", status_code=303)
    return RedirectResponse(url=f"/admin/subscribers/{subscriber_id}", status_code=303)


@router.get("/contacts/{contact_id}", response_class=HTMLResponse)
async def contact_detail_page(
    request: Request,
    contact_id: str,
    db: Session = Depends(get_db),
    next: str | None = Query(default=None),
):
    """Full page contact details view."""
    from app.web.admin import get_current_user, get_sidebar_stats

    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)
    detail_data = contact_detail_data(db, contact_id)
    if not detail_data:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "Contact not found"},
            status_code=404,
        )

    back_url = "/admin/crm/inbox"
    if next and _is_safe_url(next):
        back_url = next
    assignment_options = _load_crm_agent_team_options(db)

    return templates.TemplateResponse(
        "admin/crm/contact_detail.html",
        {
            "request": request,
            "contact": detail_data["contact"],
            "back_url": back_url,
            "active_page": "inbox",
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "agents": assignment_options["agents"],
            "teams": assignment_options["teams"],
            "agent_labels": assignment_options["agent_labels"],
        },
    )

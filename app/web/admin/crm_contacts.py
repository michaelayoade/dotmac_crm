"""CRM contacts routes."""

import csv
import io
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.logging import get_logger
from app.models.person import ChannelType as PersonChannelType
from app.models.person import Person
from app.models.subscriber import Organization
from app.services import crm as crm_service
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


def _parse_bool_filter(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return str(value).lower() in {"1", "true", "yes", "on"}


def _parse_export_days(value: str | None) -> int:
    try:
        parsed = int((value or "").strip())
    except (TypeError, ValueError):
        return 30
    # Keep bounds reasonable for export payload size and UX.
    return max(1, min(parsed, 3650))


def _csv_response(data: list[dict[str, str]], filename: str) -> StreamingResponse:
    output = io.StringIO()
    if data:
        writer = csv.DictWriter(output, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)
    else:
        output.write("No data available\n")
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _contact_channels(contact) -> tuple[str, str]:
    channels = list(contact.channels or [])
    email_values = [ch.address for ch in channels if ch.channel_type == PersonChannelType.email and ch.address]
    whatsapp_values = [ch.address for ch in channels if ch.channel_type == PersonChannelType.whatsapp and ch.address]
    if not email_values and contact.email and not str(contact.email).endswith("@example.invalid"):
        email_values = [str(contact.email)]
    return "; ".join(email_values), "; ".join(whatsapp_values)


def _clean_export_value(value: object | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text in {"-", "—"}:
        return ""
    return text


def _linked_label(contact, people_map: dict[str, Person], org_map: dict[str, Organization]) -> str:
    person = people_map.get(str(contact.person_id)) if contact.person_id else None
    org = org_map.get(str(contact.organization_id)) if contact.organization_id else None
    if person:
        return f"{person.first_name or ''} {person.last_name or ''}".strip() or (person.display_name or "")
    if org:
        return org.name or ""
    return ""


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
    export_days: str | None = Query("30"),
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
    context["export_days"] = str(_parse_export_days(export_days))
    return templates.TemplateResponse("admin/crm/contacts.html", context)


@router.get("/contacts/export")
def crm_contacts_export_csv(
    search: str | None = None,
    party_status: str | None = None,
    is_active: str | None = None,
    has_channels: str | None = None,
    linked_to_org: str | None = None,
    order_by: str = Query("created_at"),
    order_dir: str = Query("desc"),
    export_days: str | None = Query("30"),
    db: Session = Depends(get_db),
):
    safe_order_by = order_by if order_by in {"created_at", "display_name"} else "created_at"
    safe_order_dir = order_dir if order_dir in {"asc", "desc"} else "desc"
    active_filter = _parse_bool_filter(is_active)
    has_channels_filter = _parse_bool_filter(has_channels)
    linked_to_org_filter = _parse_bool_filter(linked_to_org)
    parsed_export_days = _parse_export_days(export_days)
    cutoff = datetime.now(UTC) - timedelta(days=parsed_export_days)

    contacts = crm_service.contacts.list(
        db=db,
        person_id=None,
        organization_id=None,
        party_status=party_status,
        is_active=active_filter,
        search=search,
        order_by=safe_order_by,
        order_dir=safe_order_dir,
        limit=10000,
        offset=0,
    )
    if has_channels_filter is not None:
        contacts = [contact for contact in contacts if bool(contact.channels) is has_channels_filter]
    if linked_to_org_filter is not None:
        contacts = [contact for contact in contacts if bool(contact.organization_id) is linked_to_org_filter]
    contacts = [contact for contact in contacts if contact.created_at and contact.created_at >= cutoff]

    person_ids = {contact.person_id for contact in contacts if contact.person_id}
    org_ids = {contact.organization_id for contact in contacts if contact.organization_id}
    people_map = (
        {str(person.id): person for person in db.query(Person).filter(Person.id.in_(person_ids)).all()}
        if person_ids
        else {}
    )
    org_map = (
        {str(org.id): org for org in db.query(Organization).filter(Organization.id.in_(org_ids)).all()}
        if org_ids
        else {}
    )

    rows: list[dict[str, str]] = []
    for contact in contacts:
        email_values, whatsapp_values = _contact_channels(contact)
        metadata = contact.metadata_ if isinstance(contact.metadata_, dict) else {}
        if metadata.get("is_reseller"):
            type_value = "Reseller"
        elif contact.party_status:
            type_value = contact.party_status.value.replace("_", " ").title()
        else:
            type_value = ""

        rows.append(
            {
                "Contact": _clean_export_value(
                    contact.display_name or " ".join([contact.first_name or "", contact.last_name or ""]).strip()
                ),
                "Customer ID": _clean_export_value(metadata.get("splynx_id")),
                "Contact Details - Email": _clean_export_value(email_values),
                "Contact Details - WhatsApp": _clean_export_value(whatsapp_values),
                "Linked": _clean_export_value(_linked_label(contact, people_map, org_map)),
                "Type": _clean_export_value(type_value),
                "Status": _clean_export_value("Active" if contact.is_active else "Inactive"),
                "Job Title": _clean_export_value(contact.job_title),
                "City": _clean_export_value(contact.city),
                "Region": _clean_export_value(contact.region),
                "Address": _clean_export_value(contact.address_line1),
                "Gender": _clean_export_value(contact.gender.value.title() if contact.gender else ""),
                "Marketing Opt-in": _clean_export_value("Yes" if contact.marketing_opt_in else "No"),
                "Created Date": _clean_export_value(
                    contact.created_at.strftime("%Y-%m-%d") if contact.created_at else ""
                ),
            }
        )

    filename = f"crm_contacts_{parsed_export_days}d_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv"
    return _csv_response(rows, filename)


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

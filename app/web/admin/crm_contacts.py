"""CRM contacts routes."""

import contextlib
import uuid
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.logging import get_logger
from app.models.person import ChannelType as PersonChannelType
from app.models.person import PartyStatus, Person
from app.models.subscriber import Organization, SubscriberStatus
from app.schemas.crm.contact import ContactCreate, ContactUpdate
from app.schemas.person import PartyStatusEnum
from app.services import crm as crm_service
from app.services import person as person_service
from app.services.audit_helpers import recent_activity_for_paths
from app.services.common import coerce_uuid
from app.services.crm import contact as contact_service
from app.services.crm.inbox.formatting import format_contact_for_template
from app.services.person import InvalidTransitionError, People
from app.services.subscriber import subscriber as subscriber_service

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


def _coerce_uuid_optional(value: str | None):
    from app.web.admin.crm import _coerce_uuid_optional as _shared_coerce_uuid_optional

    return _shared_coerce_uuid_optional(value)


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
    if order_by not in {"created_at", "display_name"}:
        order_by = "created_at"
    if order_dir not in {"asc", "desc"}:
        order_dir = "desc"

    def _parse_bool_filter(value: str | None) -> bool | None:
        if value is None or value == "":
            return None
        return str(value).lower() in {"1", "true", "yes", "on"}

    offset = (page - 1) * per_page
    active_filter = _parse_bool_filter(is_active)
    has_channels_filter = _parse_bool_filter(has_channels)
    linked_to_org_filter = _parse_bool_filter(linked_to_org)

    filtered_contacts_all = crm_service.contacts.list(
        db=db,
        person_id=None,
        organization_id=None,
        party_status=party_status,
        is_active=active_filter,
        search=search,
        order_by=order_by,
        order_dir=order_dir,
        limit=10000,
        offset=0,
    )
    if has_channels_filter is not None:
        filtered_contacts_all = [
            contact for contact in filtered_contacts_all if bool(contact.channels) is has_channels_filter
        ]
    if linked_to_org_filter is not None:
        filtered_contacts_all = [
            contact for contact in filtered_contacts_all if bool(contact.organization_id) is linked_to_org_filter
        ]

    total = len(filtered_contacts_all)
    total_pages = (total + per_page - 1) // per_page if total else 1
    contacts = filtered_contacts_all[offset : offset + per_page]
    people_map = {}
    org_map = {}
    for contact in contacts:
        if contact.person_id and str(contact.person_id) not in people_map:
            person = db.get(Person, contact.person_id)
            if person:
                people_map[str(contact.person_id)] = person
        if contact.organization_id and str(contact.organization_id) not in org_map:
            org = db.get(Organization, contact.organization_id)
            if org:
                org_map[str(contact.organization_id)] = org

    # Compute contact stats (using already-fetched unfiltered counts)
    all_unfiltered = crm_service.contacts.list(
        db=db,
        person_id=None,
        organization_id=None,
        party_status=None,
        is_active=None,
        search=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
    )
    contact_stats: dict[str, Any] = {
        "total": len(all_unfiltered),
        "active": sum(1 for c in all_unfiltered if c.is_active),
        "inactive": sum(1 for c in all_unfiltered if not c.is_active),
    }
    party_status_counts: dict[str, int] = {
        PartyStatus.lead.value: 0,
        PartyStatus.contact.value: 0,
        PartyStatus.customer.value: 0,
        PartyStatus.subscriber.value: 0,
        "reseller": 0,
        "unknown": 0,
    }
    channel_counts: dict[str, int] = {}
    with_channels_count = 0
    linked_to_org_count = 0
    for contact in all_unfiltered:
        contact_metadata = contact.metadata_ if isinstance(contact.metadata_, dict) else {}
        if contact_metadata.get("is_reseller"):
            party_status_counts["reseller"] = party_status_counts.get("reseller", 0) + 1

        status_key = contact.party_status.value if contact.party_status else "unknown"
        party_status_counts[status_key] = party_status_counts.get(status_key, 0) + 1
        if contact.organization_id:
            linked_to_org_count += 1
        if contact.channels:
            with_channels_count += 1
            # Count each channel type once per contact.
            channel_types = {channel.channel_type.value for channel in contact.channels}
            for channel_type in channel_types:
                channel_counts[channel_type] = channel_counts.get(channel_type, 0) + 1
    contact_stats["by_party_status"] = party_status_counts
    contact_stats["by_channel_type"] = dict(sorted(channel_counts.items(), key=lambda item: (-item[1], item[0])))
    contact_stats["linked_organization"] = linked_to_org_count
    contact_stats["unlinked_organization"] = len(all_unfiltered) - linked_to_org_count
    contact_stats["with_channels"] = with_channels_count
    contact_stats["without_channels"] = len(all_unfiltered) - with_channels_count

    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "contacts": contacts,
            "people_map": people_map,
            "org_map": org_map,
            "search": search or "",
            "party_status": party_status or "",
            "party_statuses": [item.value for item in PartyStatus],
            "is_active": "" if is_active is None else str(is_active),
            "has_channels": "" if has_channels is None else str(has_channels),
            "linked_to_org": "" if linked_to_org is None else str(linked_to_org),
            "order_by": order_by,
            "order_dir": order_dir,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "contact_stats": contact_stats,
            "recent_activities": recent_activity_for_paths(db, ["/admin/crm"]),
        }
    )
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

    error = None
    source_label = ""
    target_label = ""
    try:
        source_person_id = (source_person_id or "").strip()
        target_person_id = (target_person_id or "").strip()
        if not source_person_id or not target_person_id:
            raise ValueError("Select both source and target contacts from the dropdown.")
        source_uuid = coerce_uuid(source_person_id)
        target_uuid = coerce_uuid(target_person_id)
        if source_uuid == target_uuid:
            raise ValueError("Source and target contacts must be different.")

        source_person = db.get(Person, source_uuid)
        target_person = db.get(Person, target_uuid)
        if not source_person or not target_person:
            raise ValueError("Both contacts must exist to merge.")

        source_label = (
            source_person.display_name
            or f"{source_person.first_name or ''} {source_person.last_name or ''}".strip()
            or source_person.email
            or str(source_person.id)
        )
        target_label = (
            target_person.display_name
            or f"{target_person.first_name or ''} {target_person.last_name or ''}".strip()
            or target_person.email
            or str(target_person.id)
        )

        merged_by_value = None
        current_user = get_current_user(request)
        if current_user and current_user.get("person_id"):
            merged_by_value = coerce_uuid(current_user["person_id"])

        person_service.people.merge(
            db,
            source_id=source_uuid,
            target_id=target_uuid,
            merged_by_id=merged_by_value,
        )
        return RedirectResponse(
            url=f"/admin/crm/contacts/{target_uuid}?next=/admin/crm/contacts",
            status_code=303,
        )
    except (ValueError, ValidationError) as exc:
        db.rollback()
        error = str(exc) or "Unable to merge contacts."
        logger.exception(
            "contact_merge_validation_failed source_id=%s target_id=%s",
            source_person_id,
            target_person_id,
        )
    except Exception as exc:
        db.rollback()
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        if not error:
            error = "Unable to merge contacts."
        logger.exception(
            "contact_merge_failed source_id=%s target_id=%s",
            source_person_id,
            target_person_id,
        )

    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "error": error,
            "source_id": source_person_id,
            "source_label": source_label,
            "target_id": target_person_id,
            "target_label": target_label,
        }
    )
    return templates.TemplateResponse("admin/crm/contact_merge.html", context, status_code=400)


@router.get("/contacts/new", response_class=HTMLResponse)
def crm_contact_new(request: Request, db: Session = Depends(get_db)):
    contact = {
        "id": "",
        "display_name": "",
        "splynx_id": "",
        "email": "",
        "phone": "",
        "address_line1": "",
        "address_line2": "",
        "city": "",
        "region": "",
        "postal_code": "",
        "country_code": "",
        "person_id": "",
        "organization_id": "",
        "notes": "",
        "party_status": PartyStatus.contact.value,
        "is_active": True,
    }
    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "contact": contact,
            "organization_label": None,
            "party_statuses": [item.value for item in PartyStatus],
            "form_title": "New Contact",
            "submit_label": "Create Contact",
            "action_url": "/admin/crm/contacts",
            "contact_emails": [""],
            "contact_phones": [""],
            "contact_whatsapp": [],
            "primary_email_index": 0,
            "primary_phone_index": 0,
        }
    )
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
    error = None
    contact: dict[str, str | bool] = {
        "display_name": (display_name or "").strip(),
        "splynx_id": (splynx_id or "").strip(),
        "email": "",
        "phone": "",
        "address_line1": (address_line1 or "").strip(),
        "address_line2": (address_line2 or "").strip(),
        "city": (city or "").strip(),
        "region": (region or "").strip(),
        "postal_code": (postal_code or "").strip(),
        "country_code": (country_code or "").strip(),
        "person_id": (person_id or "").strip(),
        "organization_id": (organization_id or "").strip(),
        "notes": (notes or "").strip(),
        "party_status": (party_status or "").strip(),
        "is_active": is_active == "true",
    }
    try:
        display_name_value = contact["display_name"] if isinstance(contact["display_name"], str) else ""
        splynx_id_value = contact["splynx_id"] if isinstance(contact["splynx_id"], str) else ""
        address_line1_value = contact["address_line1"] if isinstance(contact["address_line1"], str) else ""
        address_line2_value = contact["address_line2"] if isinstance(contact["address_line2"], str) else ""
        city_value = contact["city"] if isinstance(contact["city"], str) else ""
        region_value = contact["region"] if isinstance(contact["region"], str) else ""
        postal_code_value = contact["postal_code"] if isinstance(contact["postal_code"], str) else ""
        country_code_value = contact["country_code"] if isinstance(contact["country_code"], str) else ""
        organization_id_value = contact["organization_id"] if isinstance(contact["organization_id"], str) else ""
        notes_value = contact["notes"] if isinstance(contact["notes"], str) else ""
        name_parts = display_name_value.split() if display_name_value else []
        first_name = name_parts[0] if name_parts else "Unknown"
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "Unknown"
        party_status_value = None
        if isinstance(contact.get("party_status"), str) and contact["party_status"]:
            try:
                party_status_value = PartyStatusEnum(contact["party_status"])
            except ValueError:
                party_status_value = None
        email_values = [e.strip() for e in emails if e and e.strip()]
        phone_values = [p.strip() for p in phones if p and p.strip()]
        whatsapp_values = [p.strip() for p in whatsapp_phones if p and p.strip()]
        primary_email_index = int(primary_email) if primary_email is not None and primary_email.isdigit() else None
        primary_phone_index = int(primary_phone) if primary_phone is not None and primary_phone.isdigit() else None
        if email_values:
            primary_idx = primary_email_index if primary_email_index is not None else 0
            primary_idx = primary_idx if 0 <= primary_idx < len(email_values) else 0
            primary_email_value = email_values[primary_idx]
            existing_email_owner = (
                db.query(Person).filter(func.lower(Person.email) == primary_email_value.lower()).first()
            )
            if existing_email_owner:
                raise ValueError("Email already belongs to another contact")
        primary_email_value = (
            email_values[primary_email_index]
            if email_values and primary_email_index is not None and primary_email_index < len(email_values)
            else (email_values[0] if email_values else "")
        )
        email_value = primary_email_value or f"contact-{uuid.uuid4().hex}@placeholder.local"
        payload = ContactCreate(
            first_name=first_name,
            last_name=last_name,
            display_name=display_name_value or None,
            splynx_id=splynx_id_value or None,
            email=email_value,
            phone=phone_values[primary_phone_index]
            if phone_values and primary_phone_index is not None and primary_phone_index < len(phone_values)
            else (phone_values[0] if phone_values else None),
            address_line1=address_line1_value or None,
            address_line2=address_line2_value or None,
            city=city_value or None,
            region=region_value or None,
            postal_code=postal_code_value or None,
            country_code=country_code_value or None,
            organization_id=_coerce_uuid_optional(organization_id_value),
            party_status=party_status_value or PartyStatusEnum.contact,
            notes=notes_value or None,
            is_active=bool(contact["is_active"]),
        )
        person = crm_service.contacts.create(db=db, payload=payload)
        contact_service.update_contact_channels(
            db,
            person,
            email_values,
            phone_values,
            whatsapp_values,
            primary_email_index,
            primary_phone_index,
        )
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

    # Get organization label for typeahead if organization_id was submitted
    organization_label = None
    if isinstance(contact.get("organization_id"), str) and contact["organization_id"]:
        org = db.get(Organization, coerce_uuid(contact["organization_id"]))
        if org:
            organization_label = org.name

    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "contact": contact,
            "organization_label": organization_label,
            "party_statuses": [item.value for item in PartyStatus],
            "form_title": "New Contact",
            "submit_label": "Create Contact",
            "action_url": "/admin/crm/contacts",
            "error": error,
            "contact_emails": [e.strip() for e in emails if e and e.strip()] or [""],
            "contact_phones": [p.strip() for p in phones if p and p.strip()] or [""],
            "contact_whatsapp": [p.strip() for p in whatsapp_phones if p and p.strip()],
            "primary_email_index": int(primary_email) if primary_email is not None and primary_email.isdigit() else 0,
            "primary_phone_index": int(primary_phone) if primary_phone is not None and primary_phone.isdigit() else 0,
        }
    )
    return templates.TemplateResponse("admin/crm/contact_form.html", context, status_code=400)


@router.get("/contacts/{contact_id}/edit", response_class=HTMLResponse)
def crm_contact_edit(request: Request, contact_id: str, db: Session = Depends(get_db)):
    contact_obj = contact_service.get_person_with_relationships(db, contact_id)
    if not contact_obj:
        return RedirectResponse(url="/admin/crm/contacts", status_code=303)
    channels = list(contact_obj.channels or [])
    emails = [ch.address for ch in channels if ch.channel_type == PersonChannelType.email]
    phones = [ch.address for ch in channels if ch.channel_type == PersonChannelType.phone]
    whatsapp_phones = [ch.address for ch in channels if ch.channel_type == PersonChannelType.whatsapp]
    primary_email_index = next(
        (i for i, ch in enumerate([c for c in channels if c.channel_type == PersonChannelType.email]) if ch.is_primary),
        0,
    )
    primary_phone_index = next(
        (i for i, ch in enumerate([c for c in channels if c.channel_type == PersonChannelType.phone]) if ch.is_primary),
        0,
    )
    contact = {
        "id": str(contact_obj.id),
        "display_name": contact_obj.display_name or "",
        "splynx_id": contact_obj.metadata_.get("splynx_id") if contact_obj.metadata_ else "",
        "email": contact_obj.email or "",
        "phone": contact_obj.phone or "",
        "address_line1": contact_obj.address_line1 or "",
        "address_line2": contact_obj.address_line2 or "",
        "city": contact_obj.city or "",
        "region": contact_obj.region or "",
        "postal_code": contact_obj.postal_code or "",
        "country_code": contact_obj.country_code or "",
        "person_id": str(contact_obj.person_id) if contact_obj.person_id else "",
        "organization_id": str(contact_obj.organization_id) if contact_obj.organization_id else "",
        "notes": contact_obj.notes or "",
        "party_status": contact_obj.party_status.value if contact_obj.party_status else PartyStatus.contact.value,
        "is_active": contact_obj.is_active,
    }

    # Get organization label for typeahead
    organization_label = None
    if contact_obj.organization:
        organization_label = contact_obj.organization.name

    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "contact": contact,
            "organization_label": organization_label,
            "party_statuses": [item.value for item in PartyStatus],
            "form_title": "Edit Contact",
            "submit_label": "Save Contact",
            "action_url": f"/admin/crm/contacts/{contact_id}/edit",
            "contact_emails": emails or ([contact_obj.email] if contact_obj.email else [""]),
            "contact_phones": phones or ([contact_obj.phone] if contact_obj.phone else [""]),
            "contact_whatsapp": whatsapp_phones,
            "primary_email_index": primary_email_index if emails else 0,
            "primary_phone_index": primary_phone_index if phones else 0,
        }
    )
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
    error = None
    contact: dict[str, str | bool] = {
        "id": contact_id,
        "display_name": (display_name or "").strip(),
        "splynx_id": (splynx_id or "").strip(),
        "email": "",
        "phone": "",
        "address_line1": (address_line1 or "").strip(),
        "address_line2": (address_line2 or "").strip(),
        "city": (city or "").strip(),
        "region": (region or "").strip(),
        "postal_code": (postal_code or "").strip(),
        "country_code": (country_code or "").strip(),
        "person_id": (person_id or "").strip(),
        "organization_id": (organization_id or "").strip(),
        "notes": (notes or "").strip(),
        "party_status": (party_status or "").strip(),
        "is_active": is_active == "true",
    }
    try:
        display_name_value = contact["display_name"] if isinstance(contact["display_name"], str) else ""
        splynx_id_value = contact["splynx_id"] if isinstance(contact["splynx_id"], str) else ""
        address_line1_value = contact["address_line1"] if isinstance(contact["address_line1"], str) else ""
        address_line2_value = contact["address_line2"] if isinstance(contact["address_line2"], str) else ""
        city_value = contact["city"] if isinstance(contact["city"], str) else ""
        region_value = contact["region"] if isinstance(contact["region"], str) else ""
        postal_code_value = contact["postal_code"] if isinstance(contact["postal_code"], str) else ""
        country_code_value = contact["country_code"] if isinstance(contact["country_code"], str) else ""
        organization_id_value = contact["organization_id"] if isinstance(contact["organization_id"], str) else ""
        notes_value = contact["notes"] if isinstance(contact["notes"], str) else ""
        party_status_value = None
        if isinstance(contact.get("party_status"), str) and contact["party_status"]:
            try:
                party_status_value = PartyStatusEnum(contact["party_status"])
            except ValueError:
                party_status_value = None
        email_values = [e.strip() for e in emails if e and e.strip()]
        phone_values = [p.strip() for p in phones if p and p.strip()]
        whatsapp_values = [p.strip() for p in whatsapp_phones if p and p.strip()]
        primary_email_index = int(primary_email) if primary_email is not None and primary_email.isdigit() else None
        primary_phone_index = int(primary_phone) if primary_phone is not None and primary_phone.isdigit() else None
        payload = ContactUpdate(
            display_name=display_name_value or None,
            splynx_id=splynx_id_value or None,
            address_line1=address_line1_value or None,
            address_line2=address_line2_value or None,
            city=city_value or None,
            region=region_value or None,
            postal_code=postal_code_value or None,
            country_code=country_code_value or None,
            organization_id=_coerce_uuid_optional(organization_id_value),
            party_status=party_status_value,
            notes=notes_value or None,
            is_active=bool(contact["is_active"]),
        )
        person = crm_service.contacts.update(db=db, contact_id=contact_id, payload=payload)
        contact_service.update_contact_channels(
            db,
            person,
            email_values,
            phone_values,
            whatsapp_values,
            primary_email_index,
            primary_phone_index,
        )
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

    # Get organization label for typeahead
    organization_label = None
    if isinstance(contact.get("organization_id"), str) and contact["organization_id"]:
        org = db.get(Organization, coerce_uuid(contact["organization_id"]))
        if org:
            organization_label = org.name

    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "contact": contact,
            "organization_label": organization_label,
            "party_statuses": [item.value for item in PartyStatus],
            "form_title": "Edit Contact",
            "submit_label": "Save Contact",
            "action_url": f"/admin/crm/contacts/{contact_id}/edit",
            "error": error,
            "contact_emails": [e.strip() for e in emails if e and e.strip()] or [""],
            "contact_phones": [p.strip() for p in phones if p and p.strip()] or [""],
            "contact_whatsapp": [p.strip() for p in whatsapp_phones if p and p.strip()],
            "primary_email_index": int(primary_email) if primary_email is not None and primary_email.isdigit() else 0,
            "primary_phone_index": int(primary_phone) if primary_phone is not None and primary_phone.isdigit() else 0,
        }
    )
    return templates.TemplateResponse("admin/crm/contact_form.html", context, status_code=400)


@router.post("/contacts/{contact_id}/delete", response_class=HTMLResponse)
def crm_contact_delete(request: Request, contact_id: str, db: Session = Depends(get_db)):
    _ = request
    crm_service.contacts.delete(db=db, contact_id=contact_id)
    return RedirectResponse(url="/admin/crm/contacts", status_code=303)


@router.post("/contacts/{person_id}/convert", response_class=HTMLResponse)
def crm_contact_convert(
    person_id: UUID,
    subscriber_type: str = Form("person"),
    account_status: str = Form("active"),
    db: Session = Depends(get_db),
):
    person = db.get(Person, person_id)
    if not person:
        return RedirectResponse(url="/admin/crm/contacts", status_code=303)

    status_map = {
        "active": SubscriberStatus.active,
        "canceled": SubscriberStatus.terminated,
        "delinquent": SubscriberStatus.pending,
    }
    status = status_map.get(account_status, SubscriberStatus.active)

    subscriber = subscriber_service.create(
        db,
        {
            "person_id": person.id,
            "status": status,
        },
    )
    with contextlib.suppress(InvalidTransitionError):
        People.transition_status(db, str(person.id), PartyStatus.subscriber)
    db.commit()

    return RedirectResponse(
        url=f"/admin/subscribers/{subscriber.id}",
        status_code=303,
    )


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

    contact = None
    try:
        contact_service.Contacts.get(db, contact_id)
        contact = contact_service.get_person_with_relationships(db, contact_id)
    except Exception:
        contact = None

    if not contact:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "Contact not found"},
            status_code=404,
        )

    contact_details = format_contact_for_template(contact, db)
    back_url = "/admin/crm/inbox"
    if next and _is_safe_url(next):
        back_url = next
    assignment_options = _load_crm_agent_team_options(db)

    return templates.TemplateResponse(
        "admin/crm/contact_detail.html",
        {
            "request": request,
            "contact": contact_details,
            "back_url": back_url,
            "active_page": "inbox",
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "agents": assignment_options["agents"],
            "teams": assignment_options["teams"],
            "agent_labels": assignment_options["agent_labels"],
        },
    )

"""Service helpers for CRM contact web routes."""

from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.person import ChannelType as PersonChannelType
from app.models.person import PartyStatus, Person
from app.models.subscriber import Organization, SubscriberStatus
from app.schemas.crm.contact import ContactCreate, ContactUpdate
from app.schemas.person import PartyStatusEnum
from app.services import crm as crm_service
from app.services import person as person_service
from app.services.common import coerce_uuid
from app.services.crm import contact as contact_service
from app.services.crm.inbox.formatting import format_contact_for_template
from app.services.person import InvalidTransitionError, People
from app.services.subscriber import subscriber as subscriber_service


@dataclass(slots=True)
class ContactUpsertInput:
    display_name: str | None = None
    splynx_id: str | None = None
    emails: list[str] | None = None
    phones: list[str] | None = None
    whatsapp_phones: list[str] | None = None
    primary_email: str | None = None
    primary_phone: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country_code: str | None = None
    person_id: str | None = None
    organization_id: str | None = None
    notes: str | None = None
    party_status: str | None = None
    is_active: str | None = None


def _parse_bool_filter(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return str(value).lower() in {"1", "true", "yes", "on"}


def _parse_optional_int(value: str | None) -> int | None:
    return int(value) if value is not None and value.isdigit() else None


def _coerce_uuid_optional(value: str | None) -> UUID | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    return coerce_uuid(candidate)


def party_status_values() -> list[str]:
    return [item.value for item in PartyStatus]


def list_contacts_page_data(
    db: Session,
    *,
    search: str | None,
    party_status: str | None,
    is_active: str | None,
    has_channels: str | None,
    linked_to_org: str | None,
    order_by: str,
    order_dir: str,
    page: int,
    per_page: int,
) -> dict[str, Any]:
    safe_order_by = order_by if order_by in {"created_at", "display_name"} else "created_at"
    safe_order_dir = order_dir if order_dir in {"asc", "desc"} else "desc"

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
        order_by=safe_order_by,
        order_dir=safe_order_dir,
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

    people_map: dict[str, Person] = {}
    org_map: dict[str, Organization] = {}
    for contact in contacts:
        if contact.person_id and str(contact.person_id) not in people_map:
            person = db.get(Person, contact.person_id)
            if person:
                people_map[str(contact.person_id)] = person
        if contact.organization_id and str(contact.organization_id) not in org_map:
            org = db.get(Organization, contact.organization_id)
            if org:
                org_map[str(contact.organization_id)] = org

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
            channel_types = {channel.channel_type.value for channel in contact.channels}
            for channel_type in channel_types:
                channel_counts[channel_type] = channel_counts.get(channel_type, 0) + 1
    contact_stats["by_party_status"] = party_status_counts
    contact_stats["by_channel_type"] = dict(sorted(channel_counts.items(), key=lambda item: (-item[1], item[0])))
    contact_stats["linked_organization"] = linked_to_org_count
    contact_stats["unlinked_organization"] = len(all_unfiltered) - linked_to_org_count
    contact_stats["with_channels"] = with_channels_count
    contact_stats["without_channels"] = len(all_unfiltered) - with_channels_count

    return {
        "contacts": contacts,
        "people_map": people_map,
        "org_map": org_map,
        "search": search or "",
        "party_status": party_status or "",
        "party_statuses": party_status_values(),
        "is_active": "" if is_active is None else str(is_active),
        "has_channels": "" if has_channels is None else str(has_channels),
        "linked_to_org": "" if linked_to_org is None else str(linked_to_org),
        "order_by": safe_order_by,
        "order_dir": safe_order_dir,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "contact_stats": contact_stats,
    }


def merge_contacts(
    db: Session,
    *,
    source_person_id: str,
    target_person_id: str,
    merged_by_person_id: str | None,
) -> UUID:
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

    merged_by_value = coerce_uuid(merged_by_person_id) if merged_by_person_id else None
    person_service.people.merge(
        db,
        source_id=source_uuid,
        target_id=target_uuid,
        merged_by_id=merged_by_value,
    )
    return target_uuid


def merge_labels(db: Session, *, source_person_id: str, target_person_id: str) -> tuple[str, str]:
    source_label = ""
    target_label = ""

    try:
        source_person = db.get(Person, coerce_uuid(source_person_id)) if source_person_id else None
        if source_person:
            source_label = (
                source_person.display_name
                or f"{source_person.first_name or ''} {source_person.last_name or ''}".strip()
                or source_person.email
                or str(source_person.id)
            )
    except Exception:
        source_label = ""

    try:
        target_person = db.get(Person, coerce_uuid(target_person_id)) if target_person_id else None
        if target_person:
            target_label = (
                target_person.display_name
                or f"{target_person.first_name or ''} {target_person.last_name or ''}".strip()
                or target_person.email
                or str(target_person.id)
            )
    except Exception:
        target_label = ""

    return source_label, target_label


def new_contact_form_context() -> dict[str, Any]:
    return {
        "contact": {
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
        },
        "organization_label": None,
        "party_statuses": party_status_values(),
        "form_title": "New Contact",
        "submit_label": "Create Contact",
        "action_url": "/admin/crm/contacts",
        "contact_emails": [""],
        "contact_phones": [""],
        "contact_whatsapp": [],
        "primary_email_index": 0,
        "primary_phone_index": 0,
    }


def _normalize_upsert_form(form: ContactUpsertInput, *, contact_id: str | None = None) -> dict[str, str | bool]:
    payload: dict[str, str | bool] = {
        "display_name": (form.display_name or "").strip(),
        "splynx_id": (form.splynx_id or "").strip(),
        "email": "",
        "phone": "",
        "address_line1": (form.address_line1 or "").strip(),
        "address_line2": (form.address_line2 or "").strip(),
        "city": (form.city or "").strip(),
        "region": (form.region or "").strip(),
        "postal_code": (form.postal_code or "").strip(),
        "country_code": (form.country_code or "").strip(),
        "person_id": (form.person_id or "").strip(),
        "organization_id": (form.organization_id or "").strip(),
        "notes": (form.notes or "").strip(),
        "party_status": (form.party_status or "").strip(),
        "is_active": form.is_active == "true",
    }
    if contact_id:
        payload["id"] = contact_id
    return payload


def _extract_channels(form: ContactUpsertInput) -> tuple[list[str], list[str], list[str], int | None, int | None]:
    email_values = [e.strip() for e in (form.emails or []) if e and e.strip()]
    phone_values = [p.strip() for p in (form.phones or []) if p and p.strip()]
    whatsapp_values = [p.strip() for p in (form.whatsapp_phones or []) if p and p.strip()]
    primary_email_index = _parse_optional_int(form.primary_email)
    primary_phone_index = _parse_optional_int(form.primary_phone)
    return email_values, phone_values, whatsapp_values, primary_email_index, primary_phone_index


def create_contact(db: Session, form: ContactUpsertInput) -> None:
    contact = _normalize_upsert_form(form)

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

    email_values, phone_values, whatsapp_values, primary_email_index, primary_phone_index = _extract_channels(form)
    if email_values:
        primary_idx = primary_email_index if primary_email_index is not None else 0
        primary_idx = primary_idx if 0 <= primary_idx < len(email_values) else 0
        primary_email_value = email_values[primary_idx]
        existing_email_owner = db.query(Person).filter(func.lower(Person.email) == primary_email_value.lower()).first()
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


def update_contact(db: Session, contact_id: str, form: ContactUpsertInput) -> None:
    contact = _normalize_upsert_form(form, contact_id=contact_id)

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

    email_values, phone_values, whatsapp_values, primary_email_index, primary_phone_index = _extract_channels(form)
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


def contact_form_error_context(
    db: Session,
    *,
    form: ContactUpsertInput,
    mode: str,
    contact_id: str | None = None,
) -> dict[str, Any]:
    contact = _normalize_upsert_form(form, contact_id=contact_id)

    organization_label = None
    organization_value = contact.get("organization_id")
    if isinstance(organization_value, str) and organization_value:
        org = db.get(Organization, coerce_uuid(organization_value))
        if org:
            organization_label = org.name

    action_url = "/admin/crm/contacts" if mode == "create" else f"/admin/crm/contacts/{contact_id}/edit"

    return {
        "contact": contact,
        "organization_label": organization_label,
        "party_statuses": party_status_values(),
        "form_title": "New Contact" if mode == "create" else "Edit Contact",
        "submit_label": "Create Contact" if mode == "create" else "Save Contact",
        "action_url": action_url,
        "contact_emails": [e.strip() for e in (form.emails or []) if e and e.strip()] or [""],
        "contact_phones": [p.strip() for p in (form.phones or []) if p and p.strip()] or [""],
        "contact_whatsapp": [p.strip() for p in (form.whatsapp_phones or []) if p and p.strip()],
        "primary_email_index": _parse_optional_int(form.primary_email) or 0,
        "primary_phone_index": _parse_optional_int(form.primary_phone) or 0,
    }


def edit_contact_form_context(db: Session, contact_id: str) -> dict[str, Any] | None:
    contact_obj = contact_service.get_person_with_relationships(db, contact_id)
    if not contact_obj:
        return None

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

    return {
        "contact": contact,
        "organization_label": contact_obj.organization.name if contact_obj.organization else None,
        "party_statuses": party_status_values(),
        "form_title": "Edit Contact",
        "submit_label": "Save Contact",
        "action_url": f"/admin/crm/contacts/{contact_id}/edit",
        "contact_emails": emails or ([contact_obj.email] if contact_obj.email else [""]),
        "contact_phones": phones or ([contact_obj.phone] if contact_obj.phone else [""]),
        "contact_whatsapp": whatsapp_phones,
        "primary_email_index": primary_email_index if emails else 0,
        "primary_phone_index": primary_phone_index if phones else 0,
    }


def delete_contact(db: Session, contact_id: str) -> None:
    crm_service.contacts.delete(db=db, contact_id=contact_id)


def convert_contact_to_subscriber(db: Session, person_id: UUID, account_status: str) -> UUID | None:
    person = db.get(Person, person_id)
    if not person:
        return None

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
    return subscriber.id


def contact_detail_data(db: Session, contact_id: str) -> dict[str, Any] | None:
    try:
        contact_service.Contacts.get(db, contact_id)
        contact = contact_service.get_person_with_relationships(db, contact_id)
    except Exception:
        contact = None

    if not contact:
        return None

    return {
        "contact": format_contact_for_template(contact, db),
    }


__all__ = [
    "ContactUpsertInput",
    "ValidationError",
    "contact_detail_data",
    "contact_form_error_context",
    "convert_contact_to_subscriber",
    "create_contact",
    "delete_contact",
    "edit_contact_form_context",
    "list_contacts_page_data",
    "merge_contacts",
    "merge_labels",
    "new_contact_form_context",
    "update_contact",
]

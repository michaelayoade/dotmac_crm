"""Service helpers for CRM quote web routes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.crm.enums import QuoteStatus
from app.models.person import Person
from app.models.projects import ProjectType
from app.schemas.crm.sales import QuoteCreate, QuoteLineItemCreate, QuoteLineItemUpdate, QuoteUpdate
from app.services import crm as crm_service
from app.services.common import coerce_uuid


@dataclass(slots=True)
class QuoteUpsertInput:
    lead_id: str | None = None
    contact_id: str | None = None
    tax_rate_id: str | None = None
    status: str | None = None
    project_type: str | None = None
    currency: str | None = None
    subtotal: str | None = None
    tax_total: str | None = None
    total: str | None = None
    expires_at: str | None = None
    notes: str | None = None
    is_active: str | None = None
    item_description: list[str] | None = None
    item_quantity: list[str] | None = None
    item_unit_price: list[str] | None = None
    item_inventory_item_id: list[str] | None = None


def _coerce_uuid_optional(value: str | None):
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    return coerce_uuid(candidate)


def _parse_decimal(value: str | None, field: str) -> Decimal | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        return Decimal(candidate)
    except Exception as exc:
        raise ValueError(f"{field} must be a valid decimal") from exc


def _parse_optional_datetime(value: str | None):
    if value is None:
        return None
    candidate = value.strip()
    if candidate == "":
        return None
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError("expires_at must be a valid ISO datetime") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _as_quote_items(form: QuoteUpsertInput) -> list[dict[str, str]]:
    descriptions = form.item_description or []
    quantities = form.item_quantity or []
    unit_prices = form.item_unit_price or []
    inventory_ids = form.item_inventory_item_id or []

    max_len = max(len(descriptions), len(quantities), len(unit_prices), len(inventory_ids), 1)
    items: list[dict[str, str]] = []
    for idx in range(max_len):
        description = descriptions[idx].strip() if idx < len(descriptions) and descriptions[idx] else ""
        quantity = quantities[idx].strip() if idx < len(quantities) and quantities[idx] else ""
        unit_price = unit_prices[idx].strip() if idx < len(unit_prices) and unit_prices[idx] else ""
        inventory_item_id = inventory_ids[idx].strip() if idx < len(inventory_ids) and inventory_ids[idx] else ""
        if not any([description, quantity, unit_price, inventory_item_id]):
            continue
        items.append(
            {
                "description": description,
                "quantity": quantity,
                "unit_price": unit_price,
                "inventory_item_id": inventory_item_id,
            }
        )
    return items


def _parse_quote_line_items(items: list[dict[str, str]]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for item in items:
        description = item.get("description", "").strip()
        quantity_raw = item.get("quantity", "").strip()
        unit_price_raw = item.get("unit_price", "").strip()
        inventory_item_id_raw = item.get("inventory_item_id", "").strip()

        if not description:
            raise ValueError("Quote item description is required")

        quantity = _parse_decimal(quantity_raw or "1", "quantity")
        unit_price = _parse_decimal(unit_price_raw or "0", "unit_price")
        if quantity is None or quantity <= 0:
            raise ValueError("Quote item quantity must be greater than 0")
        if unit_price is None or unit_price < 0:
            raise ValueError("Quote item unit price must be 0 or greater")

        parsed.append(
            {
                "description": description,
                "quantity": quantity,
                "unit_price": unit_price,
                "inventory_item_id": _coerce_uuid_optional(inventory_item_id_raw),
            }
        )
    return parsed


def quote_status_values() -> list[str]:
    return [item.value for item in QuoteStatus]


def list_quotes_page_data(
    db: Session,
    *,
    status: str | None,
    lead_id: str | None,
    search: str | None,
    page: int,
    per_page: int,
    contacts: list,
) -> dict[str, Any]:
    offset = (page - 1) * per_page
    quotes = crm_service.quotes.list(
        db=db,
        lead_id=lead_id,
        status=status,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
        search=search,
    )
    all_quotes = crm_service.quotes.list(
        db=db,
        lead_id=lead_id,
        status=status,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
        search=search,
    )
    total = len(all_quotes)
    total_pages = (total + per_page - 1) // per_page if total else 1

    leads = crm_service.leads.list(
        db=db,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )
    lead_map = {str(item.id): item for item in leads}
    contacts_map = {str(contact.id): contact for contact in contacts}
    stats = crm_service.quotes.count_by_status(db)

    return {
        "quotes": quotes,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "status": status or "",
        "lead_id": lead_id or "",
        "search": search or "",
        "quote_statuses": quote_status_values(),
        "leads": leads,
        "lead_map": lead_map,
        "contacts_map": contacts_map,
        "stats": stats,
        "today": datetime.now(UTC),
    }


def new_quote_form_data(db: Session, *, lead_id: str | None, contacts: list, tax_rates: list) -> dict[str, Any]:
    leads = crm_service.leads.list(
        db=db,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )
    project_types = [item.value for item in ProjectType]
    lead_id_value = (lead_id or "").strip()
    contact_id_value = ""
    if lead_id_value:
        try:
            lead_obj = crm_service.leads.get(db=db, lead_id=lead_id_value)
            if lead_obj.person_id:
                contact_id_value = str(lead_obj.person_id)
        except Exception:
            lead_id_value = ""

    resolved_contacts = list(contacts)
    if contact_id_value and not any(str(contact.id) == contact_id_value for contact in resolved_contacts):
        contact_person = db.get(Person, coerce_uuid(contact_id_value))
        if contact_person:
            resolved_contacts.append(contact_person)

    quote = {
        "id": "",
        "lead_id": lead_id_value,
        "contact_id": contact_id_value,
        "tax_rate_id": "",
        "status": QuoteStatus.draft.value,
        "project_type": "",
        "currency": "NGN",
        "subtotal": "0.00",
        "tax_total": "0.00",
        "total": "0.00",
        "expires_at": "",
        "notes": "",
        "is_active": True,
    }
    return {
        "quote": quote,
        "quote_items": [],
        "tax_rates": tax_rates,
        "quote_statuses": quote_status_values(),
        "project_types": project_types,
        "leads": leads,
        "contacts": resolved_contacts,
        "inventory_items": [],
    }


def update_quote_status(db: Session, *, quote_id: str, status_value: str) -> None:
    crm_service.quotes.get(db=db, quote_id=quote_id)
    payload = QuoteUpdate.model_validate({"status": status_value})
    crm_service.quotes.update(db=db, quote_id=quote_id, payload=payload)


def _normalized_form(form: QuoteUpsertInput, *, quote_id: str | None = None) -> dict[str, str | bool]:
    data: dict[str, str | bool] = {
        "lead_id": (form.lead_id or "").strip(),
        "contact_id": (form.contact_id or "").strip(),
        "tax_rate_id": (form.tax_rate_id or "").strip(),
        "status": (form.status or "").strip(),
        "project_type": (form.project_type or "").strip(),
        "currency": (form.currency or "").strip(),
        "subtotal": (form.subtotal or "").strip(),
        "tax_total": (form.tax_total or "").strip(),
        "total": (form.total or "").strip(),
        "expires_at": (form.expires_at or "").strip(),
        "notes": (form.notes or "").strip(),
        "is_active": form.is_active == "true",
    }
    if quote_id:
        data["id"] = quote_id
    return data


def create_quote(db: Session, *, form: QuoteUpsertInput, tax_rate_get) -> None:
    quote = _normalized_form(form)
    quote_items = _as_quote_items(form)
    parsed_items = _parse_quote_line_items(quote_items)

    lead_id_value = quote["lead_id"] if isinstance(quote["lead_id"], str) else ""
    contact_id_value = quote["contact_id"] if isinstance(quote["contact_id"], str) else ""
    tax_rate_id_value = quote["tax_rate_id"] if isinstance(quote["tax_rate_id"], str) else ""
    status_value = quote["status"] if isinstance(quote["status"], str) else ""
    project_type_value = quote["project_type"] if isinstance(quote["project_type"], str) else ""
    currency_value = quote["currency"] if isinstance(quote["currency"], str) else ""
    subtotal_value = quote["subtotal"] if isinstance(quote["subtotal"], str) else ""
    tax_total_value = quote["tax_total"] if isinstance(quote["tax_total"], str) else ""
    total_value = quote["total"] if isinstance(quote["total"], str) else ""
    expires_at_value = quote["expires_at"] if isinstance(quote["expires_at"], str) else ""
    notes_value = quote["notes"] if isinstance(quote["notes"], str) else ""

    subtotal_val = _parse_decimal(subtotal_value, "subtotal") or Decimal("0.00")
    tax_val = _parse_decimal(tax_total_value, "tax_total") or Decimal("0.00")
    if tax_rate_id_value:
        try:
            rate = tax_rate_get(db, tax_rate_id_value)
            rate_value = Decimal(rate.rate or 0)
            if rate_value > 1:
                rate_value = rate_value / Decimal("100")
            tax_val = subtotal_val * rate_value
        except Exception as exc:
            raise ValueError("Invalid tax rate") from exc
    total_val = _parse_decimal(total_value, "total") or Decimal("0.00")
    if tax_rate_id_value:
        total_val = subtotal_val + tax_val

    resolved_person_id = contact_id_value or None
    if not resolved_person_id and lead_id_value:
        try:
            lead_obj = crm_service.leads.get(db=db, lead_id=lead_id_value)
            resolved_person_id = str(lead_obj.person_id) if lead_obj.person_id else None
        except Exception:
            resolved_person_id = None
    if not resolved_person_id:
        raise ValueError("Select a contact or lead to create a quote.")

    try:
        status_enum = QuoteStatus(status_value) if status_value else QuoteStatus.draft
    except ValueError:
        status_enum = QuoteStatus.draft

    payload = QuoteCreate(
        lead_id=_coerce_uuid_optional(lead_id_value),
        person_id=coerce_uuid(resolved_person_id),
        status=status_enum,
        currency=currency_value or "NGN",
        subtotal=subtotal_val,
        tax_total=tax_val,
        total=total_val,
        expires_at=_parse_optional_datetime(expires_at_value),
        notes=notes_value or None,
        metadata_={"project_type": project_type_value} if project_type_value else None,
        is_active=bool(quote["is_active"]),
    )
    quote_obj = crm_service.quotes.create(db=db, payload=payload)
    for item in parsed_items:
        item_payload = QuoteLineItemCreate(
            quote_id=quote_obj.id,
            description=item["description"],
            quantity=item["quantity"],
            unit_price=item["unit_price"],
            inventory_item_id=item["inventory_item_id"],
        )
        crm_service.quote_line_items.create(db=db, payload=item_payload)


def edit_quote_form_data(db: Session, *, quote_id: str, contacts: list) -> dict[str, Any]:
    quote_obj = crm_service.quotes.get(db=db, quote_id=quote_id)
    items = crm_service.quote_line_items.list(
        db=db,
        quote_id=quote_id,
        order_by="created_at",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    quote_items = [
        {
            "description": item.description or "",
            "quantity": str(item.quantity or Decimal("1.000")),
            "unit_price": str(item.unit_price or Decimal("0.00")),
            "inventory_item_id": str(item.inventory_item_id) if item.inventory_item_id else "",
        }
        for item in items
    ]
    metadata = quote_obj.metadata_ if isinstance(quote_obj.metadata_, dict) else {}
    quote = {
        "id": str(quote_obj.id),
        "lead_id": str(quote_obj.lead_id) if quote_obj.lead_id else "",
        "contact_id": str(quote_obj.contact_id) if quote_obj.contact_id else "",
        "status": quote_obj.status.value if quote_obj.status else "",
        "project_type": metadata.get("project_type", "") if metadata else "",
        "currency": quote_obj.currency or "",
        "subtotal": quote_obj.subtotal or Decimal("0.00"),
        "tax_total": quote_obj.tax_total or Decimal("0.00"),
        "total": quote_obj.total or Decimal("0.00"),
        "expires_at": quote_obj.expires_at.strftime("%Y-%m-%dT%H:%M") if quote_obj.expires_at else "",
        "notes": quote_obj.notes or "",
        "is_active": quote_obj.is_active,
    }

    leads = crm_service.leads.list(
        db=db,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )
    return {
        "quote": quote,
        "quote_items": quote_items,
        "quote_statuses": quote_status_values(),
        "project_types": [item.value for item in ProjectType],
        "leads": leads,
        "contacts": contacts,
        "inventory_items": [],
    }


def update_quote(db: Session, *, quote_id: str, form: QuoteUpsertInput) -> tuple[Any, Any]:
    quote = _normalized_form(form, quote_id=quote_id)
    quote_items = _as_quote_items(form)
    parsed_items = _parse_quote_line_items(quote_items)

    lead_id_value = quote["lead_id"] if isinstance(quote["lead_id"], str) else ""
    contact_id_value = quote["contact_id"] if isinstance(quote["contact_id"], str) else ""
    status_value = quote["status"] if isinstance(quote["status"], str) else ""
    project_type_value = quote["project_type"] if isinstance(quote["project_type"], str) else ""
    currency_value = quote["currency"] if isinstance(quote["currency"], str) else ""
    tax_total_value = quote["tax_total"] if isinstance(quote["tax_total"], str) else ""
    expires_at_value = quote["expires_at"] if isinstance(quote["expires_at"], str) else ""
    notes_value = quote["notes"] if isinstance(quote["notes"], str) else ""

    subtotal_from_items = sum((item["quantity"] * item["unit_price"] for item in parsed_items), Decimal("0.00"))
    tax_value = _parse_decimal(tax_total_value, "tax_total") or Decimal("0.00")
    total_from_items = subtotal_from_items + tax_value

    quote_obj = crm_service.quotes.get(db=db, quote_id=quote_id)
    resolved_person_id = contact_id_value or None
    if not resolved_person_id and lead_id_value:
        try:
            lead_obj = crm_service.leads.get(db=db, lead_id=lead_id_value)
            resolved_person_id = str(lead_obj.person_id) if lead_obj.person_id else None
        except Exception:
            resolved_person_id = None

    metadata = quote_obj.metadata_ if isinstance(quote_obj.metadata_, dict) else {}
    if project_type_value:
        metadata["project_type"] = project_type_value

    try:
        status_enum = QuoteStatus(status_value) if status_value else None
    except ValueError:
        status_enum = None

    person_id_value = coerce_uuid(resolved_person_id) if resolved_person_id else quote_obj.person_id
    if not person_id_value:
        raise ValueError("Quote must be linked to a person.")

    payload = QuoteUpdate(
        person_id=person_id_value,
        status=status_enum,
        currency=currency_value or None,
        subtotal=subtotal_from_items,
        tax_total=tax_value,
        total=total_from_items,
        expires_at=_parse_optional_datetime(expires_at_value),
        notes=notes_value or None,
        metadata_=metadata if metadata else None,
        is_active=bool(quote["is_active"]),
    )
    before = quote_obj
    updated = crm_service.quotes.update(db=db, quote_id=quote_id, payload=payload)

    existing_items = crm_service.quote_line_items.list(
        db=db,
        quote_id=quote_id,
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    for index, item in enumerate(parsed_items):
        if index < len(existing_items):
            crm_service.quote_line_items.update(
                db=db,
                item_id=str(existing_items[index].id),
                payload=QuoteLineItemUpdate(
                    description=item["description"],
                    quantity=item["quantity"],
                    unit_price=item["unit_price"],
                    inventory_item_id=item["inventory_item_id"],
                ),
            )
        else:
            crm_service.quote_line_items.create(
                db=db,
                payload=QuoteLineItemCreate(
                    quote_id=updated.id,
                    description=item["description"],
                    quantity=item["quantity"],
                    unit_price=item["unit_price"],
                    inventory_item_id=item["inventory_item_id"],
                ),
            )
    for stale_item in existing_items[len(parsed_items) :]:
        db.delete(stale_item)
    db.commit()

    return before, updated


def quote_form_error_data(
    db: Session,
    *,
    form: QuoteUpsertInput,
    mode: str,
    quote_id: str | None,
    contacts: list,
    tax_rates: list,
):
    quote = _normalized_form(form, quote_id=quote_id)
    leads = crm_service.leads.list(
        db=db,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )
    return {
        "quote": quote,
        "quote_items": _as_quote_items(form),
        "tax_rates": tax_rates,
        "quote_statuses": quote_status_values(),
        "project_types": [item.value for item in ProjectType],
        "leads": leads,
        "contacts": contacts,
        "inventory_items": [],
        "form_title": "New Quote" if mode == "create" else "Edit Quote",
        "submit_label": "Create Quote" if mode == "create" else "Save Quote",
        "action_url": "/admin/crm/quotes" if mode == "create" else f"/admin/crm/quotes/{quote_id}/edit",
    }


def delete_quote(db: Session, quote_id: str) -> None:
    crm_service.quotes.delete(db=db, quote_id=quote_id)


def bulk_status(db: Session, body_raw: bytes) -> tuple[int, dict[str, Any]]:
    try:
        body = json.loads(body_raw)
    except Exception:
        body = {}
    quote_ids = body.get("quote_ids", [])
    new_status = body.get("status", "")
    if not quote_ids or not new_status:
        return 400, {"detail": "Missing quote_ids or status"}

    for quote_id in quote_ids:
        try:
            crm_service.quotes.update(db, quote_id, QuoteUpdate(status=new_status))
        except Exception:
            continue
    return 200, {"success": True, "updated": len(quote_ids)}


def bulk_delete(db: Session, body_raw: bytes) -> tuple[int, dict[str, Any]]:
    try:
        body = json.loads(body_raw)
    except Exception:
        body = {}
    quote_ids = body.get("quote_ids", [])
    if not quote_ids:
        return 400, {"detail": "Missing quote_ids"}

    deleted = 0
    for quote_id in quote_ids:
        try:
            crm_service.quotes.delete(db, quote_id)
            deleted += 1
        except Exception:
            continue
    return 200, {"success": True, "deleted": deleted}


__all__ = [
    "QuoteUpsertInput",
    "ValidationError",
    "bulk_delete",
    "bulk_status",
    "create_quote",
    "delete_quote",
    "edit_quote_form_data",
    "list_quotes_page_data",
    "new_quote_form_data",
    "quote_form_error_data",
    "update_quote",
    "update_quote_status",
]

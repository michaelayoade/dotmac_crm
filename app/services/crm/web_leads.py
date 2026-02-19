"""Service helpers for CRM lead web routes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date as date_type
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.crm.enums import LeadStatus
from app.models.crm.sales import Pipeline, PipelineStage
from app.models.crm.team import CrmAgent
from app.models.person import Person
from app.schemas.crm.sales import LeadCreate, LeadUpdate
from app.services import crm as crm_service
from app.services.common import coerce_uuid
from app.services.crm import contact as contact_service


@dataclass(slots=True)
class LeadUpsertInput:
    person_id: str | None = None
    contact_id: str | None = None
    pipeline_id: str | None = None
    stage_id: str | None = None
    owner_agent_id: str | None = None
    title: str | None = None
    status: str | None = None
    estimated_value: str | None = None
    currency: str | None = None
    probability: str | None = None
    expected_close_date: str | None = None
    lost_reason: str | None = None
    region: str | None = None
    address: str | None = None
    notes: str | None = None
    is_active: str | None = None


def _coerce_uuid_optional(value: str | None) -> UUID | None:
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
    if candidate == "":
        return None
    try:
        return Decimal(candidate)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a valid decimal") from exc


def lead_status_values() -> list[str]:
    return [item.value for item in LeadStatus]


def list_leads_page_data(
    db: Session,
    *,
    status: str | None,
    pipeline_id: str | None,
    stage_id: str | None,
    owner_agent_id: str | None,
    page: int,
    per_page: int,
    options: dict[str, Any],
    can_write_leads: bool,
) -> dict[str, Any]:
    offset = (page - 1) * per_page
    leads = crm_service.leads.list(
        db=db,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
        owner_agent_id=owner_agent_id,
        status=status,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )
    all_leads = crm_service.leads.list(
        db=db,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
        owner_agent_id=owner_agent_id,
        status=status,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
    )

    total = len(all_leads)
    total_pages = (total + per_page - 1) // per_page if total else 1

    lead_person_ids = [lead.person_id for lead in leads if lead.person_id]
    if lead_person_ids:
        lead_contacts = db.query(Person).filter(Person.id.in_(lead_person_ids)).all()
        contacts_map = {str(contact.id): contact for contact in lead_contacts}
    else:
        contacts_map = {}

    pipeline_map = {str(pipeline.id): pipeline for pipeline in options["pipelines"]}
    stage_map = {str(stage.id): stage for stage in options["stages"]}

    all_leads_unfiltered = crm_service.leads.list(
        db=db,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
    )
    lead_stats: dict[str, Any] = {"total": len(all_leads_unfiltered)}
    status_counts: dict[str, int] = {}
    total_value = 0.0
    for lead_item in all_leads_unfiltered:
        key = lead_item.status.value if lead_item.status else LeadStatus.new.value
        status_counts[key] = status_counts.get(key, 0) + 1
        if lead_item.estimated_value:
            total_value += float(lead_item.estimated_value)
    lead_stats["by_status"] = status_counts
    lead_stats["total_value"] = total_value

    return {
        "leads": leads,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "status": status or "",
        "pipeline_id": pipeline_id or "",
        "stage_id": stage_id or "",
        "owner_agent_id": owner_agent_id or "",
        "lead_statuses": lead_status_values(),
        "contacts": options["contacts"],
        "pipelines": options["pipelines"],
        "stages": options["stages"],
        "agents": options["agents"],
        "agent_labels": options["agent_labels"],
        "contacts_map": contacts_map,
        "pipeline_map": pipeline_map,
        "stage_map": stage_map,
        "lead_stats": lead_stats,
        "can_write_leads": can_write_leads,
    }


def new_lead_form_data(
    db: Session,
    *,
    person_id: str,
    contact_id: str,
    pipeline_id: str,
    current_person_id: str | None,
    options: dict[str, Any],
    load_pipeline_stages: Callable[[Session, str | None], list[PipelineStage]],
) -> dict[str, Any]:
    resolved_person_id = person_id or (contact_id or "")
    resolved_pipeline_id = pipeline_id
    if not resolved_pipeline_id and options["pipelines"]:
        resolved_pipeline_id = str(options["pipelines"][0].id)

    stages_for_pipeline = load_pipeline_stages(db, resolved_pipeline_id)

    if resolved_person_id:
        from app.services.person import people as person_svc

        if not any(str(person.id) == resolved_person_id for person in options["people"]):
            try:
                person = person_svc.get(db, resolved_person_id)
                options["people"] = [person] + options["people"]
            except Exception:
                pass

    lead: dict[str, Any] = {
        "id": "",
        "person_id": resolved_person_id,
        "contact_id": contact_id,
        "pipeline_id": resolved_pipeline_id,
        "stage_id": str(stages_for_pipeline[0].id) if stages_for_pipeline else "",
        "owner_agent_id": "",
        "title": "",
        "status": LeadStatus.new.value,
        "estimated_value": "",
        "currency": "",
        "probability": None,
        "expected_close_date": "",
        "lost_reason": "",
        "region": "",
        "address": "",
        "notes": "",
        "is_active": True,
    }

    if current_person_id:
        agent = (
            db.query(CrmAgent)
            .filter(
                CrmAgent.person_id == coerce_uuid(current_person_id),
                CrmAgent.is_active.is_(True),
            )
            .first()
        )
        if agent:
            lead["owner_agent_id"] = str(agent.id)

    return {
        "lead": lead,
        "lead_statuses": lead_status_values(),
        "people": options["people"],
        "contacts": options["contacts"],
        "pipelines": options["pipelines"],
        "stages": options["stages"],
        "agents": options["agents"],
        "agent_labels": options["agent_labels"],
    }


def lead_detail_data(
    db: Session,
    *,
    lead_id: str,
    options: dict[str, Any],
    can_write_leads: bool,
) -> dict[str, Any]:
    lead = crm_service.leads.get(db=db, lead_id=lead_id)
    pipeline_map = {str(pipeline.id): pipeline for pipeline in options["pipelines"]}
    stage_map = {str(stage.id): stage for stage in options["stages"]}

    contact = None
    contact_id_value = lead.person_id or lead.contact_id
    if contact_id_value:
        try:
            contact = contact_service.Contacts.get(db=db, contact_id=str(contact_id_value))
        except Exception:
            contact = db.get(Person, coerce_uuid(contact_id_value))

    pipeline = pipeline_map.get(str(lead.pipeline_id)) if lead.pipeline_id else None
    stage = stage_map.get(str(lead.stage_id)) if lead.stage_id else None
    owner_label = options["agent_labels"].get(str(lead.owner_agent_id)) if lead.owner_agent_id else "-"
    status_val = lead.status.value if lead.status else LeadStatus.new.value

    return {
        "lead": lead,
        "contact": contact,
        "pipeline": pipeline,
        "stage": stage,
        "owner_label": owner_label,
        "status_val": status_val,
        "can_write_leads": can_write_leads,
    }


def update_lead_status(db: Session, *, lead_id: str, status_value: str) -> None:
    crm_service.leads.get(db=db, lead_id=lead_id)
    payload = LeadUpdate.model_validate({"status": status_value})
    crm_service.leads.update(db=db, lead_id=lead_id, payload=payload)


def _normalize_form(form: LeadUpsertInput, *, lead_id: str | None = None) -> dict[str, str | bool]:
    data: dict[str, str | bool] = {
        "person_id": (form.person_id or "").strip(),
        "contact_id": (form.contact_id or "").strip(),
        "pipeline_id": (form.pipeline_id or "").strip(),
        "stage_id": (form.stage_id or "").strip(),
        "owner_agent_id": (form.owner_agent_id or "").strip(),
        "title": (form.title or "").strip(),
        "status": (form.status or "").strip(),
        "estimated_value": (form.estimated_value or "").strip(),
        "currency": (form.currency or "").strip(),
        "probability": (form.probability or "").strip(),
        "expected_close_date": (form.expected_close_date or "").strip(),
        "lost_reason": (form.lost_reason or "").strip(),
        "region": (form.region or "").strip(),
        "address": (form.address or "").strip(),
        "notes": (form.notes or "").strip(),
        "is_active": form.is_active == "true",
    }
    if lead_id:
        data["id"] = lead_id
    return data


def create_lead(
    db: Session,
    *,
    form: LeadUpsertInput,
    current_person_id: str | None,
    load_pipeline_stages: Callable[[Session, str | None], list[PipelineStage]],
) -> None:
    lead = _normalize_form(form)

    person_id_value = lead["person_id"] if isinstance(lead["person_id"], str) else ""
    contact_id_value = lead["contact_id"] if isinstance(lead["contact_id"], str) else ""
    pipeline_id_value = lead["pipeline_id"] if isinstance(lead["pipeline_id"], str) else ""
    stage_id_value = lead["stage_id"] if isinstance(lead["stage_id"], str) else ""
    owner_agent_id_value = lead["owner_agent_id"] if isinstance(lead["owner_agent_id"], str) else ""

    if not owner_agent_id_value and current_person_id:
        agent = (
            db.query(CrmAgent)
            .filter(
                CrmAgent.person_id == coerce_uuid(current_person_id),
                CrmAgent.is_active.is_(True),
            )
            .first()
        )
        if agent:
            owner_agent_id_value = str(agent.id)

    if pipeline_id_value and not stage_id_value:
        pipeline_stages = load_pipeline_stages(db, pipeline_id_value)
        if pipeline_stages:
            stage_id_value = str(pipeline_stages[0].id)

    estimated_value_value = lead["estimated_value"] if isinstance(lead["estimated_value"], str) else ""
    probability_value = lead["probability"] if isinstance(lead["probability"], str) else ""
    expected_close_date_value = lead["expected_close_date"] if isinstance(lead["expected_close_date"], str) else ""
    status_value = lead["status"] if isinstance(lead["status"], str) else ""

    value = _parse_decimal(estimated_value_value, "estimated_value")
    prob_value = int(probability_value) if probability_value else None
    close_date = date_type.fromisoformat(expected_close_date_value) if expected_close_date_value else None

    resolved_person_id = person_id_value or contact_id_value or None
    person_uuid = _coerce_uuid_optional(resolved_person_id)
    if not person_uuid:
        raise ValueError("Person is required.")

    try:
        status_enum = LeadStatus(status_value) if status_value else LeadStatus.new
    except ValueError:
        status_enum = LeadStatus.new

    title_value = lead["title"] if isinstance(lead["title"], str) else ""
    currency_value = lead["currency"] if isinstance(lead["currency"], str) else ""
    lost_reason_value = lead["lost_reason"] if isinstance(lead["lost_reason"], str) else ""
    region_value = lead["region"] if isinstance(lead["region"], str) else ""
    address_value = lead["address"] if isinstance(lead["address"], str) else ""
    notes_value = lead["notes"] if isinstance(lead["notes"], str) else ""

    payload = LeadCreate(
        person_id=person_uuid,
        pipeline_id=_coerce_uuid_optional(pipeline_id_value),
        stage_id=_coerce_uuid_optional(stage_id_value),
        owner_agent_id=_coerce_uuid_optional(owner_agent_id_value),
        title=title_value or None,
        status=status_enum,
        estimated_value=value,
        currency=currency_value or None,
        probability=prob_value,
        expected_close_date=close_date,
        lost_reason=lost_reason_value or None,
        region=region_value or None,
        address=address_value or None,
        notes=notes_value or None,
        is_active=bool(lead["is_active"]),
    )
    crm_service.leads.create(db=db, payload=payload)


def update_lead(
    db: Session,
    *,
    lead_id: str,
    form: LeadUpsertInput,
    load_pipeline_stages: Callable[[Session, str | None], list[PipelineStage]],
) -> None:
    lead = _normalize_form(form, lead_id=lead_id)

    person_id_value = lead["person_id"] if isinstance(lead["person_id"], str) else ""
    contact_id_value = lead["contact_id"] if isinstance(lead["contact_id"], str) else ""
    pipeline_id_value = lead["pipeline_id"] if isinstance(lead["pipeline_id"], str) else ""
    stage_id_value = lead["stage_id"] if isinstance(lead["stage_id"], str) else ""
    owner_agent_id_value = lead["owner_agent_id"] if isinstance(lead["owner_agent_id"], str) else ""
    status_value = lead["status"] if isinstance(lead["status"], str) else ""
    estimated_value_value = lead["estimated_value"] if isinstance(lead["estimated_value"], str) else ""
    currency_value = lead["currency"] if isinstance(lead["currency"], str) else ""
    probability_value = lead["probability"] if isinstance(lead["probability"], str) else ""
    expected_close_date_value = lead["expected_close_date"] if isinstance(lead["expected_close_date"], str) else ""
    lost_reason_value = lead["lost_reason"] if isinstance(lead["lost_reason"], str) else ""
    region_value = lead["region"] if isinstance(lead["region"], str) else ""
    address_value = lead["address"] if isinstance(lead["address"], str) else ""
    notes_value = lead["notes"] if isinstance(lead["notes"], str) else ""
    title_value = lead["title"] if isinstance(lead["title"], str) else ""

    if pipeline_id_value and not stage_id_value:
        pipeline_stages = load_pipeline_stages(db, pipeline_id_value)
        if pipeline_stages:
            stage_id_value = str(pipeline_stages[0].id)

    value = _parse_decimal(estimated_value_value, "estimated_value")
    prob_value = int(probability_value) if probability_value else None
    close_date = date_type.fromisoformat(expected_close_date_value) if expected_close_date_value else None

    resolved_person_id = person_id_value or contact_id_value or None

    status_enum = None
    if status_value:
        try:
            status_enum = LeadStatus(status_value)
        except ValueError:
            status_enum = None

    payload = LeadUpdate(
        person_id=_coerce_uuid_optional(resolved_person_id),
        pipeline_id=_coerce_uuid_optional(pipeline_id_value),
        stage_id=_coerce_uuid_optional(stage_id_value),
        owner_agent_id=_coerce_uuid_optional(owner_agent_id_value),
        title=title_value or None,
        status=status_enum,
        estimated_value=value,
        currency=currency_value or None,
        probability=prob_value,
        expected_close_date=close_date,
        lost_reason=lost_reason_value or None,
        region=region_value or None,
        address=address_value or None,
        notes=notes_value or None,
        is_active=bool(lead["is_active"]),
    )
    crm_service.leads.update(db=db, lead_id=lead_id, payload=payload)


def lead_form_error_data(
    *,
    form: LeadUpsertInput,
    mode: str,
    lead_id: str | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    lead = _normalize_form(form, lead_id=lead_id)
    action_url = "/admin/crm/leads" if mode == "create" else f"/admin/crm/leads/{lead_id}/edit"
    return {
        "lead": lead,
        "lead_statuses": lead_status_values(),
        "people": options["people"],
        "contacts": options["contacts"],
        "pipelines": options["pipelines"],
        "stages": options["stages"],
        "agents": options["agents"],
        "agent_labels": options["agent_labels"],
        "form_title": "New Lead" if mode == "create" else "Edit Lead",
        "submit_label": "Create Lead" if mode == "create" else "Save Lead",
        "action_url": action_url,
    }


def edit_lead_form_data(
    db: Session,
    *,
    lead_id: str,
    options: dict[str, Any],
    load_pipeline_stages: Callable[[Session, str | None], list[PipelineStage]],
) -> dict[str, Any]:
    lead_obj = crm_service.leads.get(db=db, lead_id=lead_id)
    lead: dict[str, Any] = {
        "id": str(lead_obj.id),
        "person_id": str(lead_obj.person_id) if lead_obj.person_id else "",
        "contact_id": str(lead_obj.contact_id) if lead_obj.contact_id else "",
        "pipeline_id": str(lead_obj.pipeline_id) if lead_obj.pipeline_id else "",
        "stage_id": str(lead_obj.stage_id) if lead_obj.stage_id else "",
        "owner_agent_id": str(lead_obj.owner_agent_id) if lead_obj.owner_agent_id else "",
        "title": lead_obj.title or "",
        "status": lead_obj.status.value if lead_obj.status else "",
        "estimated_value": lead_obj.estimated_value or "",
        "currency": lead_obj.currency or "",
        "probability": lead_obj.probability,
        "expected_close_date": lead_obj.expected_close_date.isoformat() if lead_obj.expected_close_date else "",
        "lost_reason": lead_obj.lost_reason or "",
        "region": lead_obj.region or "",
        "address": lead_obj.address or "",
        "notes": lead_obj.notes or "",
        "is_active": lead_obj.is_active,
    }

    if not lead["pipeline_id"] and options["pipelines"]:
        lead["pipeline_id"] = str(options["pipelines"][0].id)
    if not lead["stage_id"] and lead["pipeline_id"]:
        stages_for_pipeline = load_pipeline_stages(db, str(lead["pipeline_id"]))
        if stages_for_pipeline:
            lead["stage_id"] = str(stages_for_pipeline[0].id)

    if lead_obj.person_id and not any(str(person.id) == str(lead_obj.person_id) for person in options["people"]):
        from app.services.person import people as person_svc

        try:
            person = person_svc.get(db, str(lead_obj.person_id))
            options["people"] = [person] + options["people"]
        except Exception:
            pass

    return {
        "lead": lead,
        "lead_statuses": lead_status_values(),
        "people": options["people"],
        "contacts": options["contacts"],
        "pipelines": options["pipelines"],
        "stages": options["stages"],
        "agents": options["agents"],
        "agent_labels": options["agent_labels"],
    }


def delete_lead(db: Session, lead_id: str) -> None:
    crm_service.leads.delete(db=db, lead_id=lead_id)


__all__ = [
    "LeadUpsertInput",
    "Pipeline",
    "PipelineStage",
    "ValidationError",
    "create_lead",
    "delete_lead",
    "edit_lead_form_data",
    "lead_detail_data",
    "lead_form_error_data",
    "list_leads_page_data",
    "new_lead_form_data",
    "update_lead",
    "update_lead_status",
]

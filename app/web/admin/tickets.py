"""Admin ticket management web routes."""

import json
import re
from datetime import UTC
from math import ceil
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.db import SessionLocal
from app.logging import get_logger
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, MessageDirection
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.models.service_team import ServiceTeam
from app.models.subscriber import Subscriber
from app.models.tickets import Ticket, TicketChannel, TicketPriority, TicketStatus
from app.queries.tickets import TicketQuery
from app.services import audit as audit_service
from app.services import filter_preferences as filter_preferences_service
from app.services import settings_spec
from app.services import tickets as tickets_service
from app.services.audit_helpers import diff_dicts, extract_changes, format_changes, log_audit_event, model_to_dict
from app.services.auth_dependencies import require_permission
from app.services.common import coerce_uuid
from app.services.filter_engine import parse_filter_payload_json
from app.services.subscriber import subscriber as subscriber_service

logger = get_logger(__name__)

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/support", tags=["web-admin-support"])


def _clean_text(value: object | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _first_nonempty_form_value(form_data, *keys: str) -> str | None:
    for key in keys:
        try:
            values = form_data.getlist(key) or []
        except Exception:
            values = []
        for value in values:
            cleaned = _clean_text(value)
            if cleaned:
                return cleaned
        cleaned = _clean_text(form_data.get(key))
        if cleaned:
            return cleaned
    return None


def _derive_fallback_ticket_title(
    db: Session,
    *,
    conversation_id: str | None,
    description: str | None,
) -> str | None:
    description_title = _clean_text(description)
    if description_title:
        return description_title[:200]

    if conversation_id:
        try:
            conversation = db.get(Conversation, coerce_uuid(conversation_id))
            if conversation and isinstance(conversation.subject, str):
                subject = conversation.subject.strip()
                if subject:
                    return subject[:200]
        except Exception:
            pass

    return None


async def _collect_attachment_uploads(
    request: Request,
    attachments: list[UploadFile] | None,
) -> list[UploadFile]:
    """Return *all* uploaded files for the `attachments` field.

    Always read the underlying form list too, so repeated multipart fields are
    preserved even if parameter binding shape changes.
    """

    uploads: list[UploadFile] = []
    if attachments:
        uploads.extend(attachments)

    try:
        form = await request.form()
        uploads.extend([item for item in form.getlist("attachments") if isinstance(item, UploadFile)])
    except Exception:
        pass

    # De-dupe while preserving order.
    deduped: list[UploadFile] = []
    seen: set[tuple[str, int]] = set()
    for item in uploads:
        name = getattr(item, "filename", "") or ""
        file_obj = getattr(item, "file", None)
        marker = (name, id(file_obj) if file_obj is not None else id(item))
        if marker in seen:
            continue
        seen.add(marker)
        if name:
            deduped.append(item)
    return deduped


def _get_inbound_message_bounds(db: Session, conversation_id):
    last_inbound = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.direction == MessageDirection.inbound)
        .order_by(func.coalesce(Message.received_at, Message.created_at).desc())
        .first()
    )
    first_inbound = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.direction == MessageDirection.inbound)
        .order_by(func.coalesce(Message.received_at, Message.created_at).asc())
        .first()
    )
    return last_inbound, first_inbound


def _load_ticket_types(db: Session) -> tuple[list[dict], dict[str, str]]:
    raw = settings_spec.resolve_value(db, SettingDomain.comms, "ticket_types")
    if not raw:
        return [], {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return [], {}
    if not isinstance(raw, list):
        return [], {}
    normalized: list[dict] = []
    priority_map: dict[str, str] = {}
    priority_normalizer = {
        "high": "high",
        "medium": "medium",
        "normal": "normal",
        "urgent": "urgent",
        "low": "low",
        "lower": "lower",
    }
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        priority_raw = (item.get("priority") or "").strip().lower()
        priority = priority_normalizer.get(priority_raw) if priority_raw else None
        is_active = item.get("is_active")
        is_active = True if is_active is None else bool(is_active)
        normalized.append({"name": name, "priority": priority, "is_active": is_active})
        if is_active and priority:
            priority_map[name] = priority
    return normalized, priority_map


def _load_region_ticket_assignments(db: Session) -> dict[str, dict[str, str]]:
    raw = settings_spec.resolve_value(db, SettingDomain.comms, "region_ticket_assignments")
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, str]] = {}
    for region, entry in raw.items():
        if isinstance(entry, dict):
            manager_id = entry.get("manager_person_id") or entry.get("ticket_manager_person_id") or ""
            spc_id = (
                entry.get("spc_person_id")
                or entry.get("assistant_person_id")
                or entry.get("assistant_manager_person_id")
                or ""
            )
        elif isinstance(entry, str):
            manager_id = entry
            spc_id = ""
        else:
            continue
        normalized[str(region)] = {
            "manager_person_id": str(manager_id) if manager_id else "",
            "spc_person_id": str(spc_id) if spc_id else "",
        }
    return normalized


def _list_assignment_groups(db: Session, *, limit: int = 200) -> list[dict[str, str]]:
    teams = (
        db.query(ServiceTeam)
        .filter(ServiceTeam.is_active.is_(True))
        .order_by(ServiceTeam.name.asc())
        .limit(limit)
        .all()
    )
    items: list[dict[str, str]] = []
    for team in teams:
        label = (team.name or "Group").strip() or "Group"
        items.append({"id": str(team.id), "label": label})
    return items


def _coerce_uuid_optional(value: str | None, label: str) -> UUID | None:
    if not value:
        return None
    if isinstance(value, str) and value.strip().lower() in {"none", "null"}:
        return None
    try:
        return coerce_uuid(value)
    except Exception as exc:
        raise ValueError(f"Invalid {label}.") from exc


def _coerce_uuid_list(values: list[str], label: str) -> list[UUID]:
    ids: list[UUID] = []
    for value in values:
        coerced = _coerce_uuid_optional(value, label)
        if coerced is not None:
            ids.append(coerced)
    return ids


def _resolve_ticket_reference(db: Session, ticket_ref: str) -> tuple[Ticket, bool]:
    if not ticket_ref:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket = db.query(Ticket).filter(Ticket.number == ticket_ref).first()
    if ticket:
        return ticket, False
    try:
        ticket_uuid = coerce_uuid(ticket_ref)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Ticket not found") from exc
    ticket = tickets_service.tickets.get(db=db, ticket_id=str(ticket_uuid))
    should_redirect = bool(ticket.number)
    return ticket, should_redirect


def _log_activity(
    db: Session,
    request: Request,
    action: str,
    entity_type: str,
    entity_id: str,
    actor_id: str | None,
    metadata: dict | None = None,
) -> None:
    log_audit_event(
        db=db,
        request=request,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        metadata=metadata,
    )


def _format_ticket_activity(event) -> str:
    action = (getattr(event, "action", "") or "").lower()
    metadata = getattr(event, "metadata_", None) or {}
    if action == "create":
        return "Created ticket"
    if action == "comment":
        return "Added a comment"
    if action == "comment_edit":
        return "Edited a comment"
    if action == "status_change":
        from_status = metadata.get("from")
        to_status = metadata.get("to")
        if from_status and to_status:
            return f"Changed status from {from_status} to {to_status}"
        return "Changed status"
    if action == "priority_change":
        from_priority = metadata.get("from")
        to_priority = metadata.get("to")
        if from_priority and to_priority:
            return f"Changed priority from {from_priority} to {to_priority}"
        return "Changed priority"
    if action == "update":
        return "Updated ticket"
    return action.replace("_", " ").title() or "Activity"


def _build_activity_feed(db: Session, events: list) -> list[dict]:
    actor_ids = {str(event.actor_id) for event in events if getattr(event, "actor_id", None)}
    people = {}
    if actor_ids:
        people = {str(person.id): person for person in db.query(Person).filter(Person.id.in_(actor_ids)).all()}
    activities = []
    for event in events:
        actor = people.get(str(event.actor_id)) if getattr(event, "actor_id", None) else None
        if actor:
            actor_name = f"{actor.first_name} {actor.last_name}"
            actor_url = f"/admin/crm/contacts/{actor.id}"
        else:
            actor_name = "System"
            actor_url = None
        metadata = getattr(event, "metadata_", None) or {}
        changes = extract_changes(metadata, getattr(event, "action", None))
        change_summary = format_changes(changes, max_items=2)
        activities.append(
            {
                "message": _format_ticket_activity(event),
                "change_summary": change_summary,
                "occurred_at": getattr(event, "occurred_at", None),
                "actor_name": actor_name,
                "actor_url": actor_url,
            }
        )
    return activities


def _build_subscriber_label(subscriber: Subscriber | None) -> str:
    if not subscriber:
        return ""
    label = subscriber.display_name
    if subscriber.subscriber_number:
        return f"{label} ({subscriber.subscriber_number})"
    return label or ""


def _map_channel_to_ticket(channel_type: ChannelType | None) -> str | None:
    if channel_type == ChannelType.email:
        return TicketChannel.email.value
    if channel_type in (ChannelType.whatsapp, ChannelType.facebook_messenger, ChannelType.instagram_dm):
        return TicketChannel.chat.value
    return None


_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def _extract_email_and_name(value: str) -> tuple[str | None, str | None]:
    match = _EMAIL_RE.search(value)
    if not match:
        return None, None
    email = match.group(1)
    name = value.replace(email, " ")
    name = re.sub(r"[<>\(\)\[\],;]+", " ", name)
    name = " ".join(name.split()).strip()
    return email, (name or None)


def _resolve_customer_person_id(
    db: Session,
    customer_person_id: str | None,
    customer_search: str | None,
) -> UUID | None:
    if customer_person_id:
        return _coerce_uuid_optional(customer_person_id, "customer")
    if not customer_search:
        return None
    value = customer_search.strip()
    if not value:
        return None

    email, name_hint = _extract_email_and_name(value)
    if email:
        person = db.query(Person).filter(func.lower(Person.email) == email.lower()).first()
        if person:
            return person.id
        if name_hint:
            parts = [p for p in name_hint.replace(".", " ").split() if p]
            first_name = parts[0].title() if parts else "Customer"
            last_name = " ".join(p.title() for p in parts[1:]) if len(parts) > 1 else None
            display_name = name_hint
        else:
            local_part = email.split("@", 1)[0]
            parts = [p for p in local_part.replace(".", " ").split() if p]
            first_name = parts[0].title() if parts else "Customer"
            last_name = " ".join(p.title() for p in parts[1:]) if len(parts) > 1 else None
            display_name = " ".join([p for p in [first_name, last_name] if p])
        person = Person(
            first_name=first_name,
            last_name=last_name,
            email=email,
            display_name=display_name,
        )
        db.add(person)
        db.commit()
        db.refresh(person)
        return person.id

    display_match = db.query(Person).filter(func.lower(Person.display_name) == value.lower()).first()
    if display_match:
        return display_match.id

    name_matches = (
        db.query(Person)
        .filter(func.lower(func.concat(Person.first_name, " ", Person.last_name)) == value.lower())
        .limit(2)
        .all()
    )
    if len(name_matches) == 1:
        return name_matches[0].id
    if len(name_matches) > 1:
        raise ValueError("Multiple contacts match this name. Select the correct contact from the dropdown.")
    raise ValueError("Customer not found. Select a contact from the dropdown or enter a valid email.")


def _select_subscriber_by_id(db: Session, subscriber_id: str) -> Subscriber | None:
    try:
        return subscriber_service.get(db, coerce_uuid(subscriber_id))
    except Exception:
        return None


def _resolve_subscriber_from_contact(db: Session, person: Person | None) -> Subscriber | None:
    if not person:
        return None
    matches = subscriber_service.list(
        db,
        person_id=person.id,
        organization_id=person.organization_id,
        limit=1,
        offset=0,
    )
    return matches[0] if matches else None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _person_filter_label(person: Person) -> str:
    if person.display_name:
        return person.display_name
    full_name = f"{person.first_name or ''} {person.last_name or ''}".strip()
    if full_name:
        return full_name
    return person.email or str(person.id)


def _load_ticket_pm_spc_options(db: Session) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    rows = (
        db.query(Ticket.ticket_manager_person_id, Ticket.assistant_manager_person_id)
        .filter(Ticket.is_active.is_(True))
        .all()
    )
    pm_ids = {str(manager_id) for manager_id, _ in rows if manager_id}
    spc_ids = {str(spc_id) for _, spc_id in rows if spc_id}
    all_ids = pm_ids | spc_ids
    if not all_ids:
        return [], []

    people = db.query(Person).filter(Person.id.in_([coerce_uuid(person_id) for person_id in all_ids])).all()
    labels = {str(person.id): _person_filter_label(person) for person in people}

    pm_options = [
        {"value": person_id, "label": labels[person_id]}
        for person_id in sorted(pm_ids, key=lambda pid: labels.get(pid, ""))
    ]
    spc_options = [
        {"value": person_id, "label": labels[person_id]}
        for person_id in sorted(spc_ids, key=lambda pid: labels.get(pid, ""))
    ]
    return pm_options, spc_options


@router.get(
    "/tickets",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("support:ticket:read"))],
)
def tickets_list(
    request: Request,
    search: str | None = None,
    status: str | None = None,
    ticket_type: str | None = None,
    assigned: str | None = None,
    pm: str | None = None,
    spc: str | None = None,
    subscriber: str | None = None,
    filters: str | None = None,
    order_by: str = Query("created_at"),
    order_dir: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    db: Session = Depends(get_db),
):
    """List all tickets with filters."""
    if order_by not in {"created_at", "updated_at", "status", "priority"}:
        order_by = "created_at"
    if order_dir not in {"asc", "desc"}:
        order_dir = "desc"
    offset = (page - 1) * per_page
    from app.csrf import get_csrf_token

    subscriber_display = None
    subscriber_url = None
    filters_payload = None
    try:
        filters_payload = parse_filter_payload_json(filters)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from app.web.admin import get_current_user, get_sidebar_stats

    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)
    current_person_id = current_user.get("person_id") if current_user else None
    current_person_uuid = None
    if current_person_id:
        try:
            current_person_uuid = coerce_uuid(current_person_id)
        except Exception:
            current_person_uuid = None

    query_params_map = {key: value for key, value in request.query_params.items()}
    if current_person_uuid:
        if filter_preferences_service.has_managed_params(query_params_map, filter_preferences_service.TICKETS_PAGE):
            state = filter_preferences_service.extract_managed_state(
                query_params_map,
                filter_preferences_service.TICKETS_PAGE,
            )
            filter_preferences_service.save_preference(
                db,
                current_person_uuid,
                filter_preferences_service.TICKETS_PAGE.key,
                state,
            )
        else:
            saved_state = filter_preferences_service.get_preference(
                db,
                current_person_uuid,
                filter_preferences_service.TICKETS_PAGE.key,
            )
            if saved_state:
                merged = filter_preferences_service.merge_query_with_state(
                    query_params_map,
                    filter_preferences_service.TICKETS_PAGE,
                    saved_state,
                )
                if merged != query_params_map:
                    target_url = request.url.path if not merged else f"{request.url.path}?{urlencode(merged)}"
                    return RedirectResponse(url=target_url, status_code=302)

    assigned_to_person_id = None
    if assigned == "me" and current_person_id:
        assigned_to_person_id = current_person_id

    pm_person_id = None
    if pm == "me":
        pm_person_id = current_person_id
    elif pm:
        try:
            pm_person_id = coerce_uuid(pm)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid PM filter") from exc

    spc_person_id = None
    if spc == "me":
        spc_person_id = current_person_id
    elif spc:
        try:
            spc_person_id = coerce_uuid(spc)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid SPC filter") from exc

    subscriber_id = None
    subscriber_missing = False
    if subscriber:
        try:
            subscriber_id = coerce_uuid(subscriber)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid subscriber") from exc
        subscriber_obj = subscriber_service.get(db, subscriber_id)
        if subscriber_obj:
            subscriber_display = subscriber_obj.display_name or "Subscriber"
            subscriber_url = f"/admin/subscribers/{subscriber_obj.id}"
        else:
            subscriber_missing = True

    if subscriber_missing:
        tickets = []
        total = 0
        total_pages = 1
    else:
        base_query = (
            TicketQuery(db)
            .by_subscriber(subscriber_id)
            .by_status(status if status else None)
            .by_ticket_type(ticket_type if ticket_type else None)
            .search(search if search else None)
            .by_ticket_manager(pm_person_id)
            .by_assistant_manager(spc_person_id)
            .active_only()
        )
        if assigned_to_person_id:
            if assigned == "me":
                base_query = base_query.by_assigned_to_or_team_member(assigned_to_person_id)
            else:
                base_query = base_query.by_assigned_to(assigned_to_person_id)
        if filters_payload:
            from app.services.filter_engine import apply_filter_payload

            base_query._query = apply_filter_payload(base_query._query, "Ticket", filters_payload)
        total = base_query.count()
        total_pages = max(1, ceil(total / per_page)) if per_page else 1

        tickets = base_query.with_relations().order_by(order_by, order_dir).paginate(per_page, offset).all()

    # Get stats by status
    stats = tickets_service.tickets.status_stats(db)
    ticket_types, _ticket_type_priority_map = _load_ticket_types(db)
    ticket_type_options = [item.get("name") for item in ticket_types if item.get("is_active") and item.get("name")]
    if ticket_type and ticket_type not in ticket_type_options:
        ticket_type_options = [ticket_type, *ticket_type_options]
    pm_options, spc_options = _load_ticket_pm_spc_options(db)
    csrf_token = get_csrf_token(request)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "admin/tickets/_table.html",
            {
                "request": request,
                "tickets": tickets,
                "csrf_token": csrf_token,
                "search": search,
                "status": status,
                "ticket_type": ticket_type,
                "assigned": assigned,
                "pm": pm,
                "spc": spc,
                "subscriber": subscriber,
                "subscriber_display": subscriber_display,
                "subscriber_url": subscriber_url,
                "filters": filters,
                "order_by": order_by,
                "order_dir": order_dir,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
            },
        )

    return templates.TemplateResponse(
        "admin/tickets/index.html",
        {
            "request": request,
            "tickets": tickets,
            "stats": stats,
            "csrf_token": csrf_token,
            "search": search,
            "status": status,
            "ticket_type": ticket_type,
            "ticket_type_options": ticket_type_options,
            "assigned": assigned,
            "pm": pm,
            "spc": spc,
            "pm_options": pm_options,
            "spc_options": spc_options,
            "subscriber": subscriber,
            "subscriber_display": subscriber_display,
            "subscriber_url": subscriber_url,
            "filters": filters,
            "order_by": order_by,
            "order_dir": order_dir,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "active_page": "tickets",
        },
    )


@router.get(
    "/tickets/create",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("support:ticket:create"))],
)
def ticket_create(
    request: Request,
    db: Session = Depends(get_db),
    conversation_id: str | None = Query(None),
    title: str | None = Query(None),
    description: str | None = Query(None),
    subscriber_id: str | None = Query(None),
    customer_person_id: str | None = Query(None),
    lead_id: str | None = Query(None),
    channel: str | None = Query(None),
    subscriber: str | None = Query(None),
    customer: str | None = Query(None),
    modal: bool | None = Query(False),
):
    # subscriber_service removed
    from app.services import dispatch as dispatch_service
    from app.web.admin import get_current_user, get_sidebar_stats
    from app.web.admin.projects import REGION_OPTIONS

    prefill = {
        "conversation_id": None,
        "title": None,
        "description": None,
        "subscriber_id": None,
        "subscriber_label": "",
        "lead_id": None,
        "customer_person_id": None,
        "customer_label": "",
        "channel": None,
        "customer": None,
        "region": None,
    }

    if conversation_id:
        try:
            conversation = (
                db.query(Conversation)
                .options(selectinload(Conversation.contact))
                .filter(Conversation.id == coerce_uuid(conversation_id))
                .first()
            )
        except Exception:
            conversation = None

        if conversation:
            contact = conversation.contact
            contact_name = (
                (contact.display_name if contact else None)
                or (contact.email if contact else None)
                or (contact.phone if contact else None)
                or "Customer"
            )
            last_inbound, first_inbound = _get_inbound_message_bounds(db, conversation.id)
            subject = conversation.subject
            if last_inbound and last_inbound.subject:
                subject = last_inbound.subject
            elif first_inbound and first_inbound.subject:
                subject = first_inbound.subject
            if not subject:
                snippet_source = None
                if first_inbound and first_inbound.body:
                    snippet_source = first_inbound.body
                elif last_inbound and last_inbound.body:
                    snippet_source = last_inbound.body
                if snippet_source:
                    snippet = snippet_source.strip().splitlines()[0]
                    subject = (snippet[:77] + "...") if len(snippet) > 80 else snippet
            if not subject:
                subject = f"Support request from {contact_name}"
            prefill["conversation_id"] = str(conversation.id)
            prefill["title"] = subject
            prefill["description"] = first_inbound.body.strip() if first_inbound and first_inbound.body else ""
            prefill["customer_label"] = contact_name
            if contact and getattr(contact, "id", None):
                prefill["customer_person_id"] = str(contact.id)
            resolved_subscriber = _resolve_subscriber_from_contact(db, contact)
            if resolved_subscriber:
                prefill["subscriber_id"] = str(resolved_subscriber.id)
                prefill["subscriber_label"] = _build_subscriber_label(resolved_subscriber)
            prefill["channel"] = _map_channel_to_ticket(last_inbound.channel_type if last_inbound else None)

    if title:
        prefill["title"] = title
    if description:
        prefill["description"] = description
    if customer:
        prefill["customer_label"] = customer
    if subscriber_id:
        prefill["subscriber_id"] = subscriber_id
        subscriber_obj = _select_subscriber_by_id(db, subscriber_id)
        prefill["subscriber_label"] = _build_subscriber_label(subscriber_obj)
        if subscriber_obj and subscriber_obj.service_region:
            prefill["region"] = subscriber_obj.service_region
    if customer_person_id:
        prefill["customer_person_id"] = customer_person_id
    if lead_id:
        prefill["lead_id"] = lead_id
    elif subscriber:
        subscriber_obj = _select_subscriber_by_id(db, subscriber)
        if subscriber_obj:
            prefill["subscriber_id"] = str(subscriber_obj.id)
            prefill["subscriber_label"] = _build_subscriber_label(subscriber_obj)
            if subscriber_obj.service_region:
                prefill["region"] = subscriber_obj.service_region
    if channel and channel in {c.value for c in TicketChannel}:
        prefill["channel"] = channel

    # Subscribers are resolved via typeahead
    accounts: list[dict[str, str]] = []

    # Get technicians for assignment
    technicians = dispatch_service.technicians.list(
        db=db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    technicians = sorted(
        technicians,
        key=lambda tech: (
            (tech.person.last_name or "").lower() if tech.person else "",
            (tech.person.first_name or "").lower() if tech.person else "",
        ),
    )
    ticket_types, ticket_type_priority_map = _load_ticket_types(db)
    ticket_types = [item for item in ticket_types if item.get("is_active")]

    template_name = "admin/tickets/_form_modal.html" if modal else "admin/tickets/form.html"
    return templates.TemplateResponse(
        template_name,
        {
            "request": request,
            "ticket": None,
            "accounts": accounts,
            "technicians": technicians,
            "assignment_groups": _list_assignment_groups(db),
            "region_options": REGION_OPTIONS,
            "region_ticket_assignments": _load_region_ticket_assignments(db),
            "ticket_types": ticket_types,
            "ticket_type_priority_map": ticket_type_priority_map,
            "action_url": "/admin/support/tickets",
            "prefill": prefill,
            "active_page": "tickets",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get(
    "/tickets/lookup",
    response_class=JSONResponse,
    dependencies=[Depends(require_permission("support:ticket:read"))],
)
def ticket_customer_lookup(
    request: Request,
    db: Session = Depends(get_db),
    customer_person_id: str | None = Query(default=None),
    subscriber_id: str | None = Query(default=None),
):
    _ = request

    def _format_person_address(person: Person | None) -> str | None:
        if not person:
            return None
        parts = [
            person.address_line1,
            person.address_line2,
            person.city,
            person.region,
            person.postal_code,
            person.country_code,
        ]
        return ", ".join([p for p in parts if p]) or None

    customer = None
    subscriber = None

    person = None
    if customer_person_id:
        try:
            person = db.get(Person, coerce_uuid(customer_person_id))
        except Exception:
            person = None

    if subscriber_id:
        try:
            subscriber = db.get(Subscriber, coerce_uuid(subscriber_id))
        except Exception:
            subscriber = None
        if subscriber and not person and subscriber.person_id:
            person = db.get(Person, subscriber.person_id)

    if person:
        name = person.display_name or f"{person.first_name} {person.last_name}".strip()
        customer = {
            "id": str(person.id),
            "name": name or person.email,
            "email": person.email,
            "phone": person.phone,
            "address": _format_person_address(person),
            "organization": person.organization.name if person.organization else None,
            "region": person.region,
        }

    subscriber_data = None
    if subscriber:
        subscriber_data = {
            "id": str(subscriber.id),
            "subscriber_number": subscriber.subscriber_number,
            "account_number": subscriber.account_number,
            "status": subscriber.status.value if subscriber.status else None,
            "service_plan": subscriber.service_plan,
            "service_speed": subscriber.service_speed,
            "service_address": subscriber.service_address,
            "service_region": subscriber.service_region,
        }

    return JSONResponse(
        {
            "customer": customer,
            "subscriber": subscriber_data,
        }
    )


@router.post(
    "/tickets",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("support:ticket:create"))],
)
async def ticket_create_post(
    request: Request,
    # Keep `title` optional at the FastAPI layer so missing/buggy submissions
    # re-render the HTML form instead of returning the global JSON 422 payload.
    title: str | None = Form(None),
    description: str | None = Form(None),
    subscriber_id: str | None = Form(None),
    customer_person_id: str | None = Form(None),
    customer_search: str | None = Form(None),
    lead_id: str | None = Form(None),
    assigned_to_person_id: str | None = Form(None),
    assigned_to_person_ids: list[str] | None = Form(None),
    service_team_id: str | None = Form(None),
    ticket_manager_person_id: str | None = Form(None),
    assistant_manager_person_id: str | None = Form(None),
    region: str | None = Form(None),
    ticket_type: str | None = Form(None),
    priority: str = Form("normal"),
    channel: str = Form("web"),
    status: str = Form("open"),
    due_at: str | None = Form(None),
    tags: str | None = Form(None),
    attachments: list[UploadFile] = File(default_factory=list),
    conversation_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Create a new ticket."""
    from datetime import datetime

    from app.models.tickets import TicketChannel
    from app.schemas.tickets import TicketCreate

    # subscriber_service removed
    from app.services import dispatch as dispatch_service
    from app.services import ticket_attachments as ticket_attachment_service
    from app.web.admin import get_current_user, get_sidebar_stats
    from app.web.admin.projects import REGION_OPTIONS

    prepared_attachments: list[dict] = []
    saved_attachments: list[dict] = []
    accounts: list[dict[str, str]] = []  # subscriber_service removed
    normalized_assignees: list[str] = []
    try:
        title = _clean_text(title)
        # Some clients/UI flows submit without including the `title` field (or
        # use a different name). Recover it from the raw form/json payload so we
        # can render a friendly error instead of a JSON 422.
        if not title:
            recovered_title: str | None = None
            ctype = (request.headers.get("content-type") or "").lower()
            if "application/json" in ctype:
                try:
                    payload_json = await request.json()
                except Exception:
                    payload_json = None
                if isinstance(payload_json, dict):
                    recovered_title = _clean_text(payload_json.get("title")) or _clean_text(
                        payload_json.get("subject")
                    )
            else:
                try:
                    form_raw = await request.form()
                except Exception:
                    logger.warning(
                        "ticket_create_form_parse_error content_type=%s",
                        request.headers.get("content-type"),
                        exc_info=True,
                    )
                    raise HTTPException(
                        status_code=400,
                        detail="Could not process ticket submission. Please retry (and remove any attachments if present).",
                    )
                recovered_title = _first_nonempty_form_value(form_raw, "title", "subject")
            if recovered_title:
                title = recovered_title
            if not title:
                derived_title = _derive_fallback_ticket_title(
                    db,
                    conversation_id=conversation_id,
                    description=description,
                )
                if derived_title:
                    title = derived_title
                    logger.warning(
                        "ticket_create_missing_title_autofallback content_type=%s derived_title=%s",
                        request.headers.get("content-type"),
                        title,
                    )
                else:
                    logger.warning(
                        "ticket_create_missing_title_blocked content_type=%s",
                        request.headers.get("content-type"),
                    )
                    raise HTTPException(
                        status_code=400,
                        detail="Title is required. Please re-enter ticket details and submit again.",
                    )

        upload_list = await _collect_attachment_uploads(request, attachments)
        if upload_list:
            try:
                upload_names = []
                for item in upload_list:
                    name = getattr(item, "filename", None)
                    ctype = str(getattr(item, "content_type", "") or "")
                    if name:
                        upload_names.append(f"{name} ({ctype})")
                logger.info("ticket_create_uploads count=%s files=%s", len(upload_names), upload_names)
            except Exception:
                logger.info("ticket_create_uploads parse_error")
        prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(upload_list)
        saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)
        logger.info("ticket_create_saved_attachments count=%s", len(saved_attachments))

        # Parse enums
        priority_map = {
            "lower": TicketPriority.lower,
            "low": TicketPriority.low,
            "medium": TicketPriority.medium,
            "normal": TicketPriority.normal,
            "high": TicketPriority.high,
            "urgent": TicketPriority.urgent,
        }
        channel_map = {
            "web": TicketChannel.web,
            "email": TicketChannel.email,
            "phone": TicketChannel.phone,
            "chat": TicketChannel.chat,
            "api": TicketChannel.api,
        }
        status_map = {
            "new": TicketStatus.new,
            "open": TicketStatus.open,
            "pending": TicketStatus.pending,
            "waiting_on_customer": TicketStatus.waiting_on_customer,
            "lastmile_rerun": TicketStatus.lastmile_rerun,
            "site_under_construction": TicketStatus.site_under_construction,
            "on_hold": TicketStatus.on_hold,
            "resolved": TicketStatus.resolved,
            "closed": TicketStatus.closed,
        }

        # Parse tags
        tag_list = None
        if tags:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        # Parse due_at
        due_datetime = None
        if due_at:
            due_datetime = datetime.fromisoformat(due_at)

        metadata: dict[str, object] = {}
        if saved_attachments:
            metadata["attachments"] = saved_attachments
        metadata_value: dict[str, object] | None = metadata or None

        current_user = get_current_user(request)
        resolved_customer_person_id = _resolve_customer_person_id(
            db,
            customer_person_id,
            customer_search,
        )
        form_assignee_ids: list[str] = []
        try:
            form = await request.form()
            form_service_team_id = form.get("service_team_id")
            if isinstance(form_service_team_id, str) and form_service_team_id.strip():
                service_team_id = form_service_team_id.strip()
            form_assignee_ids = [
                item
                for item in (form.getlist("assigned_to_person_ids[]") or form.getlist("assigned_to_person_ids"))
                if isinstance(item, str)
            ]
            group_tokens = [item for item in form_assignee_ids if item.startswith("group:")]
            if group_tokens and not service_team_id:
                service_team_id = group_tokens[0].split(":", 1)[1].strip() or None
            assigned_to_person_ids = [item for item in form_assignee_ids if not item.startswith("group:")]
        except Exception:
            logger.debug("Failed to parse ticket assignees from form.", exc_info=True)
        normalized_assignees = [item for item in (assigned_to_person_ids or []) if item]
        assignee_ids = _coerce_uuid_list(normalized_assignees, "technician")
        primary_assignee_id = (
            assignee_ids[0] if assignee_ids else _coerce_uuid_optional(assigned_to_person_id, "technician")
        )
        payload_data: dict[str, Any] = {
            "title": title,
            "description": description if description else None,
            "subscriber_id": _coerce_uuid_optional(subscriber_id, "subscriber"),
            "lead_id": _coerce_uuid_optional(lead_id, "lead"),
            "customer_person_id": resolved_customer_person_id,
            "assigned_to_person_id": primary_assignee_id,
            "service_team_id": _coerce_uuid_optional(service_team_id, "user group"),
            "ticket_manager_person_id": _coerce_uuid_optional(ticket_manager_person_id, "ticket_manager"),
            "assistant_manager_person_id": _coerce_uuid_optional(assistant_manager_person_id, "assistant_manager"),
            "region": region.strip() if region else None,
            "created_by_person_id": _coerce_uuid_optional(
                current_user.get("person_id") if current_user else None,
                "user",
            ),
            "priority": priority_map.get(priority, TicketPriority.medium),
            "ticket_type": ticket_type.strip() if ticket_type else None,
            "channel": channel_map.get(channel, TicketChannel.web),
            "status": status_map.get(status, TicketStatus.open),
            "due_at": due_datetime,
            "tags": tag_list,
            "metadata_": metadata_value,
        }
        if assigned_to_person_ids is not None:
            payload_data["assigned_to_person_ids"] = assignee_ids
        payload = TicketCreate(**payload_data)
        ticket = tickets_service.tickets.create(db=db, payload=payload)
        if conversation_id:
            try:
                conversation = db.get(Conversation, coerce_uuid(conversation_id))
                if conversation:
                    conversation.ticket_id = ticket.id
                    db.commit()
            except Exception:
                db.rollback()
        actor_id = str(current_user.get("person_id")) if current_user else None
        _log_activity(
            db=db,
            request=request,
            action="create",
            entity_type="ticket",
            entity_id=str(ticket.id),
            actor_id=actor_id,
            metadata={"title": ticket.title},
        )
        return RedirectResponse(url=f"/admin/support/tickets/{ticket.id}", status_code=303)
    except Exception as e:
        ticket_attachment_service.delete_ticket_attachments(prepared_attachments)

        error_msg = getattr(e, "detail", str(e)) if isinstance(e, HTTPException) else str(e)
        error_status = e.status_code if isinstance(e, HTTPException) else 400

        # Re-fetch data for form
        technicians = dispatch_service.technicians.list(
            db=db,
            person_id=None,
            region=None,
            is_active=True,
            order_by="created_at",
            order_dir="asc",
            limit=500,
            offset=0,
        )
        technicians = sorted(
            technicians,
            key=lambda tech: (
                (tech.person.last_name or "").lower() if tech.person else "",
                (tech.person.first_name or "").lower() if tech.person else "",
            ),
        )
        ticket_types, ticket_type_priority_map = _load_ticket_types(db)
        ticket_types = [item for item in ticket_types if item.get("is_active")]
        prefill = {
            "title": title or "",
            "description": description or "",
            "subscriber_id": subscriber_id or None,
            "subscriber_label": "",
            "customer_person_id": customer_person_id or None,
            "customer_label": "",
            "lead_id": lead_id or None,
            "conversation_id": conversation_id or None,
            "channel": channel or None,
            "region": region or None,
            "assigned_to_person_ids": normalized_assignees,
            "service_team_id": service_team_id or None,
            "ticket_manager_person_id": ticket_manager_person_id or None,
            "assistant_manager_person_id": assistant_manager_person_id or None,
            "ticket_type": ticket_type or None,
            "priority": priority or "normal",
            "status": status or "open",
            "due_at": due_at or "",
            "tags": tags or "",
        }
        return templates.TemplateResponse(
            "admin/tickets/form.html",
            {
                "request": request,
                "ticket": None,
                "accounts": accounts,
                "technicians": technicians,
                "assignment_groups": _list_assignment_groups(db),
                "region_options": REGION_OPTIONS,
                "region_ticket_assignments": _load_region_ticket_assignments(db),
                "ticket_types": ticket_types,
                "ticket_type_priority_map": ticket_type_priority_map,
                "action_url": "/admin/support/tickets",
                "prefill": prefill,
                "error": error_msg,
                "active_page": "tickets",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=error_status,
        )


@router.get(
    "/tickets/{ticket_ref}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def ticket_edit(
    request: Request,
    ticket_ref: str,
    db: Session = Depends(get_db),
):
    """Edit support ticket form."""
    # subscriber_service removed
    from app.models.tickets import TicketStatus
    from app.services import dispatch as dispatch_service
    from app.web.admin import get_current_user, get_sidebar_stats
    from app.web.admin.projects import REGION_OPTIONS

    try:
        ticket, should_redirect = _resolve_ticket_reference(db, ticket_ref)
        if should_redirect:
            return RedirectResponse(
                url=f"/admin/support/tickets/{ticket.number}/edit",
                status_code=302,
            )
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "Ticket not found"},
            status_code=404,
        )

    accounts: list[dict[str, str]] = []  # subscriber_service removed
    technicians = dispatch_service.technicians.list(
        db=db,
        person_id=None,
        region=None,
        is_active=True,
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    technicians = sorted(
        technicians,
        key=lambda tech: (
            (tech.person.last_name or "").lower() if tech.person else "",
            (tech.person.first_name or "").lower() if tech.person else "",
        ),
    )
    ticket_types, ticket_type_priority_map = _load_ticket_types(db)
    ticket_types = [item for item in ticket_types if item.get("is_active")]
    if ticket and ticket.ticket_type and not any(item.get("name") == ticket.ticket_type for item in ticket_types):
        ticket_types = [{"name": ticket.ticket_type, "priority": None, "is_active": False}, *ticket_types]

    error_message = None
    if ticket.status in {TicketStatus.closed, TicketStatus.canceled}:
        error_message = "This ticket is closed or canceled and cannot be edited."

    return templates.TemplateResponse(
        "admin/tickets/form.html",
        {
            "request": request,
            "ticket": ticket,
            "accounts": accounts,
            "technicians": technicians,
            "assignment_groups": _list_assignment_groups(db),
            "region_options": REGION_OPTIONS,
            "region_ticket_assignments": _load_region_ticket_assignments(db),
            "ticket_types": ticket_types,
            "ticket_type_priority_map": ticket_type_priority_map,
            "action_url": f"/admin/support/tickets/{ticket.number or ticket.id}/edit",
            "error": error_message,
            "active_page": "tickets",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post(
    "/tickets/{ticket_ref}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
async def ticket_edit_post(
    request: Request,
    ticket_ref: str,
    # Keep optional so the HTML form can re-render on missing fields (instead of JSON 422).
    title: str | None = Form(None),
    description: str | None = Form(None),
    subscriber_id: str | None = Form(None),
    customer_person_id: str | None = Form(None),
    customer_search: str | None = Form(None),
    assigned_to_person_id: str | None = Form(None),
    assigned_to_person_ids: list[str] | None = Form(None),
    service_team_id: str | None = Form(None),
    ticket_manager_person_id: str | None = Form(None),
    assistant_manager_person_id: str | None = Form(None),
    region: str | None = Form(None),
    ticket_type: str | None = Form(None),
    priority: str | None = Form(None),
    channel: str | None = Form(None),
    status: str | None = Form(None),
    due_at: str | None = Form(None),
    tags: str | None = Form(None),
    attachments: list[UploadFile] = File(default_factory=list),
    db: Session = Depends(get_db),
):
    """Update a support ticket."""
    from datetime import datetime

    from app.models.tickets import TicketChannel, TicketStatus
    from app.schemas.tickets import TicketUpdate

    # subscriber_service removed
    from app.services import dispatch as dispatch_service
    from app.services import ticket_attachments as ticket_attachment_service
    from app.web.admin import get_current_user, get_sidebar_stats
    from app.web.admin.projects import REGION_OPTIONS

    prepared_attachments: list[dict] = []
    saved_attachments: list[dict] = []
    accounts: list[dict[str, str]] = []  # subscriber_service removed
    try:
        ticket, _should_redirect = _resolve_ticket_reference(db, ticket_ref)
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "Ticket not found"},
            status_code=404,
        )

    if ticket.status in {TicketStatus.closed, TicketStatus.canceled}:
        technicians = dispatch_service.technicians.list(
            db=db,
            person_id=None,
            region=None,
            is_active=True,
            order_by="created_at",
            order_dir="asc",
            limit=500,
            offset=0,
        )
        technicians = sorted(
            technicians,
            key=lambda tech: (
                (tech.person.last_name or "").lower() if tech.person else "",
                (tech.person.first_name or "").lower() if tech.person else "",
            ),
        )
        ticket_types, ticket_type_priority_map = _load_ticket_types(db)
        ticket_types = [item for item in ticket_types if item.get("is_active")]
        if ticket.ticket_type and not any(item.get("name") == ticket.ticket_type for item in ticket_types):
            ticket_types = [{"name": ticket.ticket_type, "priority": None, "is_active": False}, *ticket_types]
        return templates.TemplateResponse(
            "admin/tickets/form.html",
            {
                "request": request,
                "ticket": ticket,
                "accounts": accounts,
                "technicians": technicians,
                "assignment_groups": _list_assignment_groups(db),
                "region_options": REGION_OPTIONS,
                "region_ticket_assignments": _load_region_ticket_assignments(db),
                "ticket_types": ticket_types,
                "ticket_type_priority_map": ticket_type_priority_map,
                "action_url": f"/admin/support/tickets/{ticket.number or ticket.id}/edit",
                "error": "This ticket is closed or canceled and cannot be edited.",
                "active_page": "tickets",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )

    try:
        title = _clean_text(title)
        if not title:
            recovered_title: str | None = None
            ctype = (request.headers.get("content-type") or "").lower()
            if "application/json" in ctype:
                try:
                    payload_json = await request.json()
                except Exception:
                    payload_json = None
                if isinstance(payload_json, dict):
                    recovered_title = _clean_text(payload_json.get("title")) or _clean_text(
                        payload_json.get("subject")
                    )
            else:
                try:
                    form_raw = await request.form()
                except Exception:
                    logger.warning(
                        "ticket_edit_form_parse_error content_type=%s ticket_ref=%s",
                        request.headers.get("content-type"),
                        ticket_ref,
                        exc_info=True,
                    )
                    raise HTTPException(
                        status_code=400,
                        detail="Could not process ticket submission. Please retry (and remove any attachments if present).",
                    )
                recovered_title = _first_nonempty_form_value(form_raw, "title", "subject")
            if recovered_title:
                title = recovered_title
            if not title and isinstance(ticket.title, str) and ticket.title.strip():
                logger.warning(
                    "ticket_edit_missing_title_fallback ticket_ref=%s content_type=%s",
                    ticket_ref,
                    request.headers.get("content-type"),
                )
                title = ticket.title.strip()
            if not title:
                raise HTTPException(status_code=400, detail="Title is required.")

        def _assignee_ids(current_ticket):
            if current_ticket.assignees:
                return [str(assignee.person_id) for assignee in current_ticket.assignees]
            if current_ticket.assigned_to_person_id:
                return [str(current_ticket.assigned_to_person_id)]
            return []

        before_assignee_ids = _assignee_ids(ticket)
        before_service_team_id = str(ticket.service_team_id) if ticket.service_team_id else None
        before_service_team_label = ticket.service_team.name if ticket.service_team else None
        before_state = model_to_dict(
            ticket,
            include={
                "subscriber_id",
                "customer_person_id",
                "created_by_person_id",
                "assigned_to_person_id",
                "ticket_manager_person_id",
                "assistant_manager_person_id",
                "service_team_id",
                "region",
                "title",
                "description",
                "status",
                "priority",
                "ticket_type",
                "channel",
                "tags",
                "due_at",
                "resolved_at",
                "closed_at",
                "is_active",
            },
        )

        upload_list = await _collect_attachment_uploads(request, attachments)

        prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(upload_list)
        saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)

        priority_map = {
            "lower": TicketPriority.lower,
            "low": TicketPriority.low,
            "medium": TicketPriority.medium,
            "normal": TicketPriority.normal,
            "high": TicketPriority.high,
            "urgent": TicketPriority.urgent,
        }
        channel_map = {
            "web": TicketChannel.web,
            "email": TicketChannel.email,
            "phone": TicketChannel.phone,
            "chat": TicketChannel.chat,
            "api": TicketChannel.api,
        }
        status_map = {
            "new": TicketStatus.new,
            "open": TicketStatus.open,
            "pending": TicketStatus.pending,
            "waiting_on_customer": TicketStatus.waiting_on_customer,
            "lastmile_rerun": TicketStatus.lastmile_rerun,
            "site_under_construction": TicketStatus.site_under_construction,
            "on_hold": TicketStatus.on_hold,
            "resolved": TicketStatus.resolved,
            "closed": TicketStatus.closed,
            "canceled": TicketStatus.canceled,
        }

        metadata_update = None
        metadata_changed = False
        if saved_attachments:
            existing_metadata = (
                dict(ticket.metadata_) if ticket.metadata_ and isinstance(ticket.metadata_, dict) else {}
            )
            new_metadata = dict(existing_metadata)
            if saved_attachments:
                existing_attachments = existing_metadata.get("attachments")
                attachment_list = list(existing_attachments) if isinstance(existing_attachments, list) else []
                attachment_list.extend(saved_attachments)
                new_metadata["attachments"] = attachment_list
                metadata_changed = True

            if metadata_changed:
                metadata_update = new_metadata if new_metadata else None

        resolved_customer_person_id = ticket.customer_person_id
        if customer_person_id is not None or customer_search is not None:
            resolved_customer_person_id = _resolve_customer_person_id(
                db,
                customer_person_id,
                customer_search,
            )
        form_assignee_ids: list[str] = []
        assignee_field_present = assigned_to_person_ids is not None or assigned_to_person_id is not None
        service_team_field_present = service_team_id is not None
        try:
            form = await request.form()
            form_service_team_id = form.get("service_team_id")
            if "service_team_id" in form:
                service_team_field_present = True
                if isinstance(form_service_team_id, str):
                    service_team_id = form_service_team_id.strip() or None
            form_assignee_ids = [
                item
                for item in (form.getlist("assigned_to_person_ids[]") or form.getlist("assigned_to_person_ids"))
                if isinstance(item, str)
            ]
            if "assigned_to_person_ids[]" in form or "assigned_to_person_ids" in form:
                assignee_field_present = True
            group_tokens = [item for item in form_assignee_ids if item.startswith("group:")]
            if group_tokens and not service_team_id:
                service_team_id = group_tokens[0].split(":", 1)[1].strip() or None
                service_team_field_present = True
            assigned_to_person_ids = [item for item in form_assignee_ids if not item.startswith("group:")]
        except Exception:
            logger.debug("Failed to parse ticket assignees from edit form.", exc_info=True)
        normalized_assignees = [item for item in (assigned_to_person_ids or []) if item]
        assignee_ids = _coerce_uuid_list(normalized_assignees, "technician")
        if assignee_field_present:
            primary_assignee_id = (
                assignee_ids[0] if assignee_ids else _coerce_uuid_optional(assigned_to_person_id, "technician")
            )
        else:
            primary_assignee_id = ticket.assigned_to_person_id
        effective_priority = priority_map.get(priority or "", ticket.priority)
        effective_channel = channel_map.get(channel or "", ticket.channel)
        effective_status = status_map.get(status or "", ticket.status)
        subscriber_value = ticket.subscriber_id
        if subscriber_id is not None:
            subscriber_value = _coerce_uuid_optional(subscriber_id, "subscriber")
        ticket_manager_value = ticket.ticket_manager_person_id
        if ticket_manager_person_id is not None:
            ticket_manager_value = _coerce_uuid_optional(ticket_manager_person_id, "ticket_manager")
        assistant_manager_value = ticket.assistant_manager_person_id
        if assistant_manager_person_id is not None:
            assistant_manager_value = _coerce_uuid_optional(assistant_manager_person_id, "assistant_manager")
        service_team_value = ticket.service_team_id
        if service_team_field_present:
            service_team_value = _coerce_uuid_optional(service_team_id, "user group")
        region_value = ticket.region
        if region is not None:
            region_value = region.strip() or None
        ticket_type_value = ticket.ticket_type
        if ticket_type is not None:
            ticket_type_value = ticket_type.strip() or None
        description_value = ticket.description
        if description is not None:
            description_value = description if description else None
        due_datetime_value = ticket.due_at
        if due_at is not None:
            due_datetime_value = datetime.fromisoformat(due_at) if due_at else None
        tags_value = ticket.tags
        if tags is not None:
            tags_value = [t.strip() for t in tags.split(",") if t.strip()] or None
        update_data: dict[str, Any] = {
            "title": title,
            "description": description_value,
            "subscriber_id": subscriber_value,
            "customer_person_id": resolved_customer_person_id,
            "assigned_to_person_id": primary_assignee_id,
            "service_team_id": service_team_value,
            "ticket_manager_person_id": ticket_manager_value,
            "assistant_manager_person_id": assistant_manager_value,
            "region": region_value,
            "priority": effective_priority,
            "ticket_type": ticket_type_value,
            "channel": effective_channel,
            "status": effective_status,
            "due_at": due_datetime_value,
            "tags": tags_value,
        }
        if assignee_field_present:
            update_data["assigned_to_person_ids"] = assignee_ids

        if effective_status == TicketStatus.resolved and not ticket.resolved_at:
            update_data["resolved_at"] = datetime.now(UTC)
        if effective_status == TicketStatus.closed and not ticket.closed_at:
            update_data["closed_at"] = datetime.now(UTC)

        if metadata_changed:
            update_data["metadata_"] = metadata_update

        payload = TicketUpdate(**update_data)
        tickets_service.tickets.update(db=db, ticket_id=str(ticket.id), payload=payload)
        updated_ticket = tickets_service.tickets.get(db=db, ticket_id=str(ticket.id))
        after_assignee_ids = _assignee_ids(updated_ticket)
        after_state = model_to_dict(
            updated_ticket,
            include={
                "subscriber_id",
                "customer_person_id",
                "created_by_person_id",
                "assigned_to_person_id",
                "ticket_manager_person_id",
                "assistant_manager_person_id",
                "service_team_id",
                "region",
                "title",
                "description",
                "status",
                "priority",
                "ticket_type",
                "channel",
                "tags",
                "due_at",
                "resolved_at",
                "closed_at",
                "is_active",
            },
        )
        changes = diff_dicts(before_state, after_state)
        if set(before_assignee_ids) != set(after_assignee_ids):
            all_ids = set(before_assignee_ids) | set(after_assignee_ids)
            people_map = {}
            if all_ids:
                people_map = {
                    str(person.id): person for person in db.query(Person).filter(Person.id.in_(all_ids)).all()
                }

            def _labels(ids):
                labels = []
                for pid in ids:
                    person = people_map.get(pid)
                    if person:
                        label = person.display_name or f"{person.first_name} {person.last_name}".strip() or person.email
                        labels.append(label)
                return labels

            before_labels = _labels(before_assignee_ids)
            after_labels = _labels(after_assignee_ids)
            changes["assignees"] = {
                "from": ", ".join(before_labels) if before_labels else "Unassigned",
                "to": ", ".join(after_labels) if after_labels else "Unassigned",
            }
        after_service_team_id = str(updated_ticket.service_team_id) if updated_ticket.service_team_id else None
        after_service_team_label = updated_ticket.service_team.name if updated_ticket.service_team else None
        if before_service_team_id != after_service_team_id:
            changes["service_team"] = {
                "from": before_service_team_label or "Unassigned",
                "to": after_service_team_label or "Unassigned",
            }
        metadata_payload = {"changes": changes} if changes else None
        current_user = get_current_user(request)
        _log_activity(
            db=db,
            request=request,
            action="update",
            entity_type="ticket",
            entity_id=str(ticket.id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata=metadata_payload,
        )
        return RedirectResponse(url=f"/admin/support/tickets/{ticket.number or ticket.id}", status_code=303)
    except Exception as e:
        ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        accounts = []  # subscriber_service removed
        technicians = dispatch_service.technicians.list(
            db=db,
            person_id=None,
            region=None,
            is_active=True,
            order_by="created_at",
            order_dir="asc",
            limit=500,
            offset=0,
        )
        technicians = sorted(
            technicians,
            key=lambda tech: (
                (tech.person.last_name or "").lower() if tech.person else "",
                (tech.person.first_name or "").lower() if tech.person else "",
            ),
        )
        ticket_types, ticket_type_priority_map = _load_ticket_types(db)
        ticket_types = [item for item in ticket_types if item.get("is_active")]
        if ticket and ticket.ticket_type and not any(item.get("name") == ticket.ticket_type for item in ticket_types):
            ticket_types = [{"name": ticket.ticket_type, "priority": None, "is_active": False}, *ticket_types]
        return templates.TemplateResponse(
            "admin/tickets/form.html",
            {
                "request": request,
                "ticket": ticket,
                "accounts": accounts,
                "technicians": technicians,
                "assignment_groups": _list_assignment_groups(db),
                "region_options": REGION_OPTIONS,
                "region_ticket_assignments": _load_region_ticket_assignments(db),
                "ticket_types": ticket_types,
                "ticket_type_priority_map": ticket_type_priority_map,
                "action_url": f"/admin/support/tickets/{ticket.number or ticket.id}/edit",
                "error": str(e),
                "active_page": "tickets",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )


@router.get(
    "/tickets/{ticket_ref}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("support:ticket:read"))],
)
def ticket_detail(
    request: Request,
    ticket_ref: str,
    db: Session = Depends(get_db),
):
    """View ticket details."""
    try:
        ticket, should_redirect = _resolve_ticket_reference(db, ticket_ref)
        if should_redirect:
            return RedirectResponse(
                url=f"/admin/support/tickets/{ticket.number}",
                status_code=302,
            )
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "Ticket not found"},
            status_code=404,
        )

    # Get comments for this ticket
    comments = tickets_service.ticket_comments.list(
        db=db,
        ticket_id=str(ticket.id),
        is_internal=None,  # Show both internal and external comments
        order_by="created_at",
        order_dir="asc",
        limit=100,
        offset=0,
    )

    from app.web.admin import get_current_user, get_sidebar_stats

    audit_events = audit_service.audit_events.list(
        db=db,
        actor_id=None,
        actor_type=None,
        action=None,
        entity_type="ticket",
        entity_id=str(ticket.id),
        request_id=None,
        is_success=None,
        status_code=None,
        is_active=None,
        order_by="occurred_at",
        order_dir="desc",
        limit=10,
        offset=0,
    )
    activities = _build_activity_feed(db, audit_events)
    sidebar_stats = get_sidebar_stats(db)
    current_user = get_current_user(request)

    # Fetch expense totals from ERP (cached to avoid blocking)
    expense_totals = None
    try:
        from app.services.dotmac_erp.cache import get_cached_expense_totals

        expense_totals = get_cached_expense_totals(db, "ticket", str(ticket.id))
    except Exception:
        logger.debug("ERP expense totals unavailable for ticket.", exc_info=True)

    # Fetch material requests linked to this ticket
    ticket_material_requests = []
    try:
        from app.services.material_requests import material_requests as mr_service

        ticket_material_requests = mr_service.list(
            db, ticket_id=str(ticket.id), order_by="created_at", order_dir="desc", limit=20, offset=0
        )
    except Exception:
        logger.debug("ERP expense totals fetch failed for ticket.", exc_info=True)

    ticket_attachments: list[dict[str, Any]] = []
    try:
        metadata = ticket.metadata_ if isinstance(ticket.metadata_, dict) else {}
        attachments = metadata.get("attachments")
        if isinstance(attachments, list):
            ticket_attachments = [item for item in attachments if isinstance(item, dict)]
        elif isinstance(attachments, dict):
            ticket_attachments = [attachments]
    except Exception:
        ticket_attachments = []

    # Keep ticket comments attachment payload shape consistent for templates.
    for comment in comments:
        raw_attachments = getattr(comment, "attachments", None)
        if isinstance(raw_attachments, list):
            comment.attachments = [item for item in raw_attachments if isinstance(item, dict)]
        elif isinstance(raw_attachments, dict):
            comment.attachments = [raw_attachments]
        else:
            comment.attachments = []

    def _format_person_address(person: Person | None) -> str | None:
        if not person:
            return None
        parts = [
            person.address_line1,
            person.address_line2,
            person.city,
            person.region,
            person.postal_code,
            person.country_code,
        ]
        return ", ".join([p for p in parts if p]) or None

    customer_details = None
    subscriber_details = None
    try:
        person = ticket.customer if getattr(ticket, "customer", None) else None
        if not person and ticket.customer_person_id:
            person = db.get(Person, ticket.customer_person_id)
        if person:
            name = person.display_name or f"{person.first_name} {person.last_name}".strip()
            customer_details = {
                "id": str(person.id),
                "name": name or person.email,
                "email": person.email,
                "phone": person.phone,
                "address": _format_person_address(person),
                "organization": person.organization.name if person.organization else None,
            }
    except Exception:
        customer_details = None

    try:
        subscriber = ticket.subscriber if getattr(ticket, "subscriber", None) else None
        if not subscriber and ticket.subscriber_id:
            subscriber = db.get(Subscriber, ticket.subscriber_id)
        if subscriber:
            subscriber_details = {
                "id": str(subscriber.id),
                "subscriber_number": subscriber.subscriber_number,
                "account_number": subscriber.account_number,
                "status": subscriber.status.value if subscriber.status else None,
                "service_plan": subscriber.service_plan,
                "service_speed": subscriber.service_speed,
                "service_address": subscriber.service_address,
            }
    except Exception:
        subscriber_details = None

    from app.services.ticket_mentions import list_ticket_mention_users

    mention_agents = list_ticket_mention_users(db)

    return templates.TemplateResponse(
        "admin/tickets/detail.html",
        {
            "request": request,
            "ticket": ticket,
            "comments": comments,
            "activities": activities,
            "expense_totals": expense_totals,
            "material_requests": ticket_material_requests,
            "ticket_attachments": ticket_attachments,
            "customer_details": customer_details,
            "subscriber_details": subscriber_details,
            "mention_agents": mention_agents,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "active_page": "tickets",
        },
    )


@router.post(
    "/tickets/{ticket_ref}/delete",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("support:ticket:delete"))],
)
def ticket_delete(
    request: Request,
    ticket_ref: str,
    db: Session = Depends(get_db),
):
    """Soft-delete a ticket (is_active = False)."""
    from app.web.admin import get_current_user

    try:
        ticket, _should_redirect = _resolve_ticket_reference(db, ticket_ref)
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "Ticket not found"},
            status_code=404,
        )

    tickets_service.tickets.delete(db=db, ticket_id=str(ticket.id))
    current_user = get_current_user(request)
    _log_activity(
        db=db,
        request=request,
        action="delete",
        entity_type="ticket",
        entity_id=str(ticket.id),
        actor_id=str(current_user.get("person_id")) if current_user else None,
        metadata={"title": ticket.title},
    )

    if request.headers.get("HX-Request"):
        return HTMLResponse(content="", headers={"HX-Redirect": "/admin/support/tickets"})
    return RedirectResponse(url="/admin/support/tickets", status_code=303)


@router.post(
    "/tickets/{ticket_ref}/status",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def update_ticket_status(
    request: Request,
    ticket_ref: str,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    """Update ticket status."""
    from datetime import datetime

    from app.schemas.tickets import TicketUpdate
    from app.web.admin import get_current_user

    try:
        status_map = {
            "new": TicketStatus.new,
            "open": TicketStatus.open,
            "pending": TicketStatus.pending,
            "waiting_on_customer": TicketStatus.waiting_on_customer,
            "lastmile_rerun": TicketStatus.lastmile_rerun,
            "site_under_construction": TicketStatus.site_under_construction,
            "on_hold": TicketStatus.on_hold,
            "resolved": TicketStatus.resolved,
            "closed": TicketStatus.closed,
            "canceled": TicketStatus.canceled,
        }
        new_status = status_map.get(status, TicketStatus.open)
        ticket, _should_redirect = _resolve_ticket_reference(db, ticket_ref)
        old_status = ticket.status.value if ticket.status else None

        resolved_at = datetime.now(UTC) if status == "resolved" else None
        closed_at = datetime.now(UTC) if status == "closed" else None
        payload = TicketUpdate(status=new_status, resolved_at=resolved_at, closed_at=closed_at)
        tickets_service.tickets.update(db=db, ticket_id=str(ticket.id), payload=payload)
        current_user = get_current_user(request)
        _log_activity(
            db=db,
            request=request,
            action="status_change",
            entity_type="ticket",
            entity_id=str(ticket.id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata={"from": old_status, "to": new_status.value if new_status else None},
        )

        if request.headers.get("HX-Request"):
            return HTMLResponse(
                content="",
                headers={"HX-Redirect": f"/admin/support/tickets/{ticket.number or ticket.id}"},
            )
        return RedirectResponse(url=f"/admin/support/tickets/{ticket.number or ticket.id}", status_code=303)
    except Exception as e:
        from app.web.admin import get_current_user, get_sidebar_stats

        sidebar_stats = get_sidebar_stats(db)
        current_user = get_current_user(request)
        return templates.TemplateResponse(
            "admin/errors/500.html",
            {"request": request, "error": str(e), "current_user": current_user, "sidebar_stats": sidebar_stats},
            status_code=500,
        )


@router.post(
    "/tickets/{ticket_ref}/priority",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def update_ticket_priority(
    request: Request,
    ticket_ref: str,
    priority: str = Form(...),
    db: Session = Depends(get_db),
):
    """Update ticket priority."""
    from app.schemas.tickets import TicketUpdate
    from app.web.admin import get_current_user

    try:
        priority_map = {
            "lower": TicketPriority.lower,
            "low": TicketPriority.low,
            "medium": TicketPriority.medium,
            "normal": TicketPriority.normal,
            "high": TicketPriority.high,
            "urgent": TicketPriority.urgent,
        }
        new_priority = priority_map.get(priority, TicketPriority.medium)
        ticket, _should_redirect = _resolve_ticket_reference(db, ticket_ref)
        old_priority = ticket.priority.value if ticket.priority else None

        payload = TicketUpdate(priority=new_priority)
        tickets_service.tickets.update(db=db, ticket_id=str(ticket.id), payload=payload)
        current_user = get_current_user(request)
        _log_activity(
            db=db,
            request=request,
            action="priority_change",
            entity_type="ticket",
            entity_id=str(ticket.id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata={"from": old_priority, "to": new_priority.value if new_priority else None},
        )

        if request.headers.get("HX-Request"):
            return HTMLResponse(
                content="",
                headers={"HX-Redirect": f"/admin/support/tickets/{ticket.number or ticket.id}"},
            )
        return RedirectResponse(url=f"/admin/support/tickets/{ticket.number or ticket.id}", status_code=303)
    except Exception as e:
        from app.web.admin import get_current_user, get_sidebar_stats

        sidebar_stats = get_sidebar_stats(db)
        current_user = get_current_user(request)
        return templates.TemplateResponse(
            "admin/errors/500.html",
            {"request": request, "error": str(e), "current_user": current_user, "sidebar_stats": sidebar_stats},
            status_code=500,
        )


@router.post(
    "/tickets/{ticket_ref}/comments",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
async def add_ticket_comment(
    request: Request,
    ticket_ref: str,
    body: str = Form(...),
    is_internal: str | None = Form(None),
    mentions: str | None = Form(None),
    attachments: list[UploadFile] = File(default_factory=list),
    db: Session = Depends(get_db),
):
    """Add a comment to a ticket."""
    from uuid import UUID

    from app.schemas.tickets import TicketCommentCreate
    from app.services import ticket_attachments as ticket_attachment_service
    from app.web.admin import get_current_user

    prepared_attachments: list[dict] = []
    try:
        upload_list = await _collect_attachment_uploads(request, attachments)
        prepared_attachments = ticket_attachment_service.prepare_ticket_attachments(upload_list)
        saved_attachments = ticket_attachment_service.save_ticket_attachments(prepared_attachments)
        ticket, _should_redirect = _resolve_ticket_reference(db, ticket_ref)
        current_user = get_current_user(request)
        actor_id = str(current_user.get("person_id")) if current_user else None
        payload = TicketCommentCreate(
            ticket_id=UUID(str(ticket.id)),
            author_person_id=UUID(actor_id) if actor_id else None,
            body=body,
            is_internal=is_internal == "true",
            attachments=saved_attachments or None,
        )
        tickets_service.ticket_comments.create(db=db, payload=payload)

        # Best-effort @mention notifications (does not affect comment creation).
        if mentions:
            try:
                import json

                from app.services.ticket_mentions import notify_ticket_comment_mentions

                parsed = json.loads(mentions)
                mentioned_agent_ids = parsed if isinstance(parsed, list) else []
                preview = (body or "").strip()
                if len(preview) > 140:
                    preview = preview[:137].rstrip() + "..."
                notify_ticket_comment_mentions(
                    db,
                    ticket_id=str(ticket.id),
                    ticket_number=ticket.number,
                    ticket_title=ticket.title,
                    comment_preview=preview or None,
                    mentioned_agent_ids=list(mentioned_agent_ids),
                    actor_person_id=actor_id,
                )
            except Exception:
                pass

        _log_activity(
            db=db,
            request=request,
            action="comment",
            entity_type="ticket",
            entity_id=str(ticket.id),
            actor_id=actor_id,
            metadata={"internal": is_internal == "true"},
        )

        if request.headers.get("HX-Request"):
            return HTMLResponse(
                content="",
                headers={"HX-Redirect": f"/admin/support/tickets/{ticket.number or ticket.id}"},
            )
        return RedirectResponse(url=f"/admin/support/tickets/{ticket.number or ticket.id}", status_code=303)
    except Exception as e:
        ticket_attachment_service.delete_ticket_attachments(prepared_attachments)
        logger.exception("ticket_comment_create_failed ticket_ref=%s", ticket_ref)
        from app.web.admin import get_current_user, get_sidebar_stats

        sidebar_stats = get_sidebar_stats(db)
        current_user = get_current_user(request)
        return templates.TemplateResponse(
            "admin/errors/500.html",
            {"request": request, "error": str(e), "current_user": current_user, "sidebar_stats": sidebar_stats},
            status_code=500,
        )


@router.post(
    "/tickets/{ticket_ref}/comments/{comment_id}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("support:ticket:update"))],
)
def edit_ticket_comment(
    request: Request,
    ticket_ref: str,
    comment_id: str,
    body: str = Form(...),
    mentions: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Edit a ticket comment body."""
    from app.schemas.tickets import TicketCommentUpdate
    from app.web.admin import get_current_user, get_sidebar_stats

    body_clean = (body or "").strip()
    if not body_clean:
        return RedirectResponse(url=f"/admin/support/tickets/{ticket_ref}", status_code=303)

    try:
        ticket, _should_redirect = _resolve_ticket_reference(db, ticket_ref)
        comment = tickets_service.ticket_comments.get(db=db, comment_id=comment_id)
        if str(comment.ticket_id) != str(ticket.id):
            return templates.TemplateResponse(
                "admin/errors/404.html",
                {"request": request, "message": "Comment not found"},
                status_code=404,
            )

        payload = TicketCommentUpdate(body=body_clean)
        tickets_service.ticket_comments.update(db=db, comment_id=comment_id, payload=payload)
        current_user = get_current_user(request)
        actor_id = str(current_user.get("person_id")) if current_user else None

        if mentions:
            try:
                import json

                from app.services.ticket_mentions import notify_ticket_comment_mentions

                parsed = json.loads(mentions)
                mentioned_agent_ids = parsed if isinstance(parsed, list) else []
                preview = body_clean
                if len(preview) > 140:
                    preview = preview[:137].rstrip() + "..."
                notify_ticket_comment_mentions(
                    db,
                    ticket_id=str(ticket.id),
                    ticket_number=ticket.number,
                    ticket_title=ticket.title,
                    comment_preview=preview or None,
                    mentioned_agent_ids=list(mentioned_agent_ids),
                    actor_person_id=actor_id,
                )
            except Exception:
                pass

        _log_activity(
            db=db,
            request=request,
            action="comment_edit",
            entity_type="ticket",
            entity_id=str(ticket.id),
            actor_id=actor_id,
        )

        if request.headers.get("HX-Request"):
            return HTMLResponse(
                content="",
                headers={"HX-Redirect": f"/admin/support/tickets/{ticket.number or ticket.id}"},
            )
        return RedirectResponse(url=f"/admin/support/tickets/{ticket.number or ticket.id}", status_code=303)
    except Exception as e:
        sidebar_stats = get_sidebar_stats(db)
        current_user = get_current_user(request)
        return templates.TemplateResponse(
            "admin/errors/500.html",
            {"request": request, "error": str(e), "current_user": current_user, "sidebar_stats": sidebar_stats},
            status_code=500,
        )

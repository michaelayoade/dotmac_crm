import builtins
import html
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.crm.sales import Lead
from app.models.domain_settings import SettingDomain
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember
from app.models.tickets import (
    Ticket,
    TicketAssignee,
    TicketComment,
    TicketLink,
    TicketMerge,
    TicketPriority,
    TicketSlaEvent,
    TicketStatus,
)
from app.models.workflow import (
    SlaBreach,
    SlaBreachStatus,
    SlaClock,
    SlaClockStatus,
    SlaPolicy,
    SlaTarget,
    WorkflowEntityType,
)
from app.models.workforce import WorkOrder, WorkOrderPriority, WorkOrderStatus, WorkOrderType
from app.queries.tickets import TicketCommentQuery, TicketQuery, TicketSlaEventQuery
from app.schemas.tickets import (
    TicketCommentBulkCreateRequest,
    TicketCommentCreate,
    TicketCommentUpdate,
    TicketCreate,
    TicketSlaEventCreate,
    TicketSlaEventUpdate,
    TicketUpdate,
)
from app.services import settings_spec
from app.services.common import (
    coerce_uuid,
)
from app.services.events import emit_event
from app.services.events.types import EventType
from app.services.numbering import generate_number
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)

RELATED_OUTAGE_LINK_TYPE = "related_outage"
TICKET_SLA_POLICY_NAME = "Ticket Resolution SLA"
TICKET_SLA_TERMINAL_STATUSES = {
    TicketStatus.resolved,
    TicketStatus.closed,
    TicketStatus.canceled,
    TicketStatus.merged,
}


def _notify_ticket_role_assignment_in_app(
    db: Session,
    *,
    ticket: Ticket,
    role_assignments: dict[str, object],
) -> set[str]:
    """Create in-app notifications for internal roles on a ticket.

    role_assignments maps "role label" -> person_id (UUID or str).
    We de-dupe by recipient email so one person with multiple roles receives one notification.
    """
    # Group roles by person_id and discard empty.
    roles_by_person_id: dict[UUID, list[str]] = {}
    person_ids: list[UUID] = []
    for role_label, person_id in role_assignments.items():
        if not person_id:
            continue
        try:
            person_uuid = coerce_uuid(person_id)
        except (ValueError, AttributeError):
            continue
        if person_uuid not in roles_by_person_id:
            roles_by_person_id[person_uuid] = []
            person_ids.append(person_uuid)
        if role_label not in roles_by_person_id[person_uuid]:
            roles_by_person_id[person_uuid].append(role_label)

    if not person_ids:
        return set()

    people = db.query(Person).filter(Person.id.in_(person_ids)).all()
    people_by_id = {p.id: p for p in people}

    from app.services import email as email_service

    base_url = (email_service.get_app_url(db) or "").rstrip("/")
    ticket_ref = ticket.number or str(ticket.id)
    ticket_url = (
        f"{base_url}/admin/support/tickets/{ticket_ref}" if base_url else f"/admin/support/tickets/{ticket_ref}"
    )

    subject = f"New Ticket Assignment: {ticket.title}"
    site = (ticket.region or "").strip()
    now = datetime.now(UTC)

    created_for: set[str] = set()
    for person_id, roles in roles_by_person_id.items():
        person = people_by_id.get(person_id)
        if not person or not isinstance(person.email, str) or not person.email.strip():
            continue
        recipient = person.email.strip()
        if recipient in created_for:
            continue
        created_for.add(recipient)

        roles_label = ", ".join(roles)
        body_lines = [f"You have been assigned as {roles_label} for this ticket."]
        if site:
            body_lines.append(f"Site: {site}.")
        body_lines.append(f"Open: {ticket_url}")

        db.add(
            Notification(
                channel=NotificationChannel.push,
                recipient=recipient,
                subject=subject,
                body="\n".join(body_lines),
                status=NotificationStatus.delivered,
                sent_at=now,
            )
        )

    db.commit()
    return created_for


def _notify_ticket_service_team_assignment(
    db: Session,
    *,
    ticket: Ticket,
    service_team_id: object,
    exclude_recipients: set[str] | None = None,
) -> set[str]:
    """Notify active members when a ticket is assigned to a service team.

    Sends both:
    - in-app push notifications
    - queued email notifications
    """
    if not service_team_id:
        return set()
    try:
        team_uuid = coerce_uuid(service_team_id)
    except Exception:
        return set()

    team = db.get(ServiceTeam, team_uuid)
    if not team or not team.is_active:
        return set()

    member_rows = (
        db.query(Person)
        .join(
            ServiceTeamMember,
            ServiceTeamMember.person_id == Person.id,
        )
        .filter(ServiceTeamMember.team_id == team_uuid)
        .filter(ServiceTeamMember.is_active.is_(True))
        .filter(Person.is_active.is_(True))
        .all()
    )
    if not member_rows:
        return set()

    from app.services import email as email_service

    base_url = (email_service.get_app_url(db) or "").rstrip("/")
    ticket_ref = ticket.number or str(ticket.id)
    ticket_url = (
        f"{base_url}/admin/support/tickets/{ticket_ref}" if base_url else f"/admin/support/tickets/{ticket_ref}"
    )
    subject = f"New Ticket Assignment: {ticket.title}"
    site = (ticket.region or "").strip()
    group_name = (team.name or "User Group").strip()
    now = datetime.now(UTC)

    excluded = set(exclude_recipients or set())
    notified: set[str] = set()
    for person in member_rows:
        if not isinstance(person.email, str) or not person.email.strip():
            continue
        recipient = person.email.strip()
        if recipient in excluded or recipient in notified:
            continue
        notified.add(recipient)
        body_lines = [f"A ticket has been assigned to your group ({group_name})."]
        if site:
            body_lines.append(f"Site: {site}.")
        body_lines.append(f"Open: {ticket_url}")
        push_body = "\n".join(body_lines)
        safe_group = html.escape(group_name)
        safe_site = html.escape(site) if site else ""
        safe_ticket_url = html.escape(ticket_url, quote=True)
        email_body_parts = [f"<p>A ticket has been assigned to your group ({safe_group}).</p>"]
        if safe_site:
            email_body_parts.append(f"<p>Site: {safe_site}.</p>")
        email_body_parts.append(f'<p>Open: <a href="{safe_ticket_url}">{safe_ticket_url}</a></p>')
        email_body = "".join(email_body_parts)
        db.add(
            Notification(
                channel=NotificationChannel.push,
                recipient=recipient,
                subject=subject,
                body=push_body,
                status=NotificationStatus.delivered,
                sent_at=now,
            )
        )
        db.add(
            Notification(
                channel=NotificationChannel.email,
                recipient=recipient,
                subject=subject,
                body=email_body,
                status=NotificationStatus.queued,
            )
        )

    db.commit()
    return notified


def _ensure_person(db: Session, person_id: str):
    person = db.get(Person, coerce_uuid(person_id))
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")


def _ensure_lead(db: Session, lead_id: str):
    lead = db.get(Lead, coerce_uuid(lead_id))
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")


def _ensure_service_team(db: Session, service_team_id: str):
    team = db.get(ServiceTeam, coerce_uuid(service_team_id))
    if not team:
        raise HTTPException(status_code=404, detail="User group not found")


def _has_field_visit_tag(tags: list | None) -> bool:
    """Check if tags contain 'field_visit'."""
    if not tags:
        return False
    return "field_visit" in tags


def _is_truthy(value: object | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _normalize_assignee_ids(assignee_ids: list[str] | None) -> list[str]:
    if not assignee_ids:
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in assignee_ids:
        if not raw:
            continue
        try:
            coerced = str(coerce_uuid(raw))
        except Exception:
            coerced = None
        if not coerced:
            continue
        if coerced not in seen:
            seen.add(coerced)
            normalized.append(coerced)
    return normalized


def _resolve_merge_chain(ticket: Ticket | None) -> Ticket | None:
    current = ticket
    seen: set[UUID] = set()
    while current and current.merged_into_ticket is not None:
        if current.id in seen:
            raise HTTPException(status_code=409, detail="Ticket merge chain is invalid")
        seen.add(current.id)
        current = current.merged_into_ticket
    return current


def _ensure_ticket_not_merged_source(ticket: Ticket) -> None:
    if ticket.merged_into_ticket_id:
        raise HTTPException(status_code=409, detail="This ticket has already been merged into another ticket")


def _ticket_ref(ticket: Ticket) -> str:
    return ticket.number or str(ticket.id)


def _comment_merge_note(source: Ticket, target: Ticket, reason: str | None, direction: str) -> str:
    target_ref = _ticket_ref(target)
    source_ref = _ticket_ref(source)
    if direction == "source":
        body = f"System: Ticket merged into {target_ref}."
    else:
        body = f"System: Merged ticket {source_ref} into this ticket."
    if reason:
        body += f" Reason: {reason.strip()}"
    if direction == "source":
        body += f" Open {target_ref} for future updates."
    return body


def _dedupe_attachment_payload(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        marker = (
            str(item.get("stored_name") or ""),
            str(item.get("key") or ""),
            str(item.get("url") or ""),
        )
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(item)
    return deduped


def _ticket_attachment_payload(ticket: Ticket) -> list[dict[str, Any]]:
    metadata = ticket.metadata_ if isinstance(ticket.metadata_, dict) else {}
    attachments = metadata.get("attachments")
    if isinstance(attachments, list):
        return [item for item in attachments if isinstance(item, dict)]
    if isinstance(attachments, dict):
        return [attachments]
    return []


def _set_ticket_attachments(ticket: Ticket, attachments: list[dict[str, Any]]) -> None:
    metadata = dict(ticket.metadata_ or {})
    if attachments:
        metadata["attachments"] = attachments
    else:
        metadata.pop("attachments", None)
    ticket.metadata_ = metadata or None


def _merge_ticket_attachments(target: Ticket, source: Ticket) -> None:
    combined = _ticket_attachment_payload(target) + _ticket_attachment_payload(source)
    _set_ticket_attachments(target, _dedupe_attachment_payload(combined))


def _upsert_internal_comment(
    db: Session,
    *,
    ticket_id: UUID,
    author_person_id: UUID | None,
    body: str,
) -> None:
    db.add(
        TicketComment(
            ticket_id=ticket_id,
            author_person_id=author_person_id,
            body=body,
            is_internal=True,
        )
    )


def _sync_ticket_assignees(db: Session, ticket: Ticket, assignee_ids: list[str] | None) -> None:
    if assignee_ids is None:
        return
    normalized = _normalize_assignee_ids(assignee_ids)
    for person_id in normalized:
        _ensure_person(db, person_id)

    ticket.assigned_to_person_id = coerce_uuid(normalized[0]) if normalized else None

    current_ids = {str(assignee.person_id) for assignee in ticket.assignees}
    target_ids = set(normalized)

    for person_id in target_ids - current_ids:
        ticket.assignees.append(TicketAssignee(ticket_id=ticket.id, person_id=coerce_uuid(person_id)))
    if target_ids != current_ids:
        for assignee in list(ticket.assignees):
            if str(assignee.person_id) not in target_ids:
                ticket.assignees.remove(assignee)


def _auto_create_work_order_for_ticket(db: Session, ticket: Ticket) -> WorkOrder | None:
    """Auto-create a work order when a ticket has field_visit tag.

    Returns the existing work order if one already exists, or creates a new one.
    """
    # Check if work order already exists for this ticket
    existing = (
        db.query(WorkOrder).filter(WorkOrder.ticket_id == ticket.id).filter(WorkOrder.is_active.is_(True)).first()
    )
    if existing:
        return existing

    # Map ticket priority to work order priority
    priority_map = {
        TicketPriority.lower: WorkOrderPriority.lower,
        TicketPriority.low: WorkOrderPriority.low,
        TicketPriority.medium: WorkOrderPriority.medium,
        TicketPriority.normal: WorkOrderPriority.normal,
        TicketPriority.high: WorkOrderPriority.high,
        TicketPriority.urgent: WorkOrderPriority.urgent,
    }
    wo_priority = priority_map.get(ticket.priority, WorkOrderPriority.normal)

    # Truncate title if needed
    title_prefix = "Field Visit - "
    max_title_len = 200 - len(title_prefix)
    ticket_title = (ticket.title or "")[:max_title_len]

    work_order = WorkOrder(
        title=f"{title_prefix}{ticket_title}",
        work_type=WorkOrderType.repair,
        status=WorkOrderStatus.draft,
        priority=wo_priority,
        subscriber_id=ticket.subscriber_id,
        ticket_id=ticket.id,
    )
    db.add(work_order)
    return work_order


def _resolve_customer_name(ticket: Ticket, db: Session) -> str | None:
    if ticket.customer:
        return ticket.customer.display_name or ticket.customer.email
    if ticket.subscriber and ticket.subscriber.person:
        person = ticket.subscriber.person
        return person.display_name or person.email
    if ticket.lead_id:
        lead = db.get(Lead, ticket.lead_id)
        if lead and lead.person:
            return lead.person.display_name or lead.person.email
    return None


def _resolve_customer_email(ticket: Ticket, db: Session) -> str | None:
    if ticket.customer and ticket.customer.email:
        email = ticket.customer.email
        if isinstance(email, str) and email.strip():
            return email.strip()
    if ticket.subscriber and ticket.subscriber.person:
        email = ticket.subscriber.person.email
        if isinstance(email, str) and email.strip():
            return email.strip()
    if ticket.lead_id:
        lead = db.get(Lead, ticket.lead_id)
        if lead and lead.person and lead.person.email:
            email = lead.person.email
            if isinstance(email, str) and email.strip():
                return email.strip()
    return None


def _resolve_technician_contact(db: Session, person_id) -> dict | None:
    if not person_id:
        return None
    technician = db.get(Person, person_id)
    if not technician:
        return None
    name = (
        technician.display_name or f"{technician.first_name or ''} {technician.last_name or ''}".strip() or "Technician"
    )
    email: str | None = technician.email if isinstance(technician.email, str) else None
    email = email.strip() if email else None
    return {
        "name": name,
        "email": email,
    }


def _ticket_has_technician(ticket: Ticket, person_id: UUID) -> bool:
    if ticket.assigned_to_person_id == person_id:
        return True
    return any(assignee.person_id == person_id for assignee in ticket.assignees or [])


def _fallback_customer_update_message(comment_body: str | None) -> str:
    text = " ".join((comment_body or "").split()).strip()
    if not text:
        return "There is an update on your support ticket."
    return text[:4000]


def _get_region_ticket_assignments(db: Session, region: str | None) -> tuple[str | None, str | None]:
    """Look up project manager + SPC person_id for the given region from settings."""
    if not region:
        return None, None
    region_ticket_map = settings_spec.resolve_value(db, SettingDomain.comms, "region_ticket_assignments")
    if not region_ticket_map or not isinstance(region_ticket_map, dict):
        return None, None
    entry = region_ticket_map.get(region)
    manager_id: str | None = None
    spc_id: str | None = None
    if isinstance(entry, dict):
        manager_id = entry.get("manager_person_id") or entry.get("ticket_manager_person_id")
        spc_id = (
            entry.get("spc_person_id") or entry.get("assistant_person_id") or entry.get("assistant_manager_person_id")
        )
    elif isinstance(entry, str):
        manager_id = entry
    if manager_id:
        person = db.get(Person, coerce_uuid(manager_id))
        if not person:
            manager_id = None
        else:
            manager_id = str(person.id)
    if spc_id:
        person = db.get(Person, coerce_uuid(spc_id))
        if not person:
            spc_id = None
        else:
            spc_id = str(person.id)
    return manager_id, spc_id


def _maybe_auto_assign_ticket(db: Session, ticket: Ticket):
    """Apply rule-based ticket auto-assignment when enabled."""
    if ticket.assigned_to_person_id:
        return None
    enabled = _is_truthy(
        settings_spec.resolve_value(db, SettingDomain.workflow, "ticket_auto_assignment_enabled"),
        False,
    )
    if not enabled:
        return None

    from app.services.audit_helpers import log_audit_event
    from app.services.ticket_assignment import auto_assign_ticket

    actor_id = str(ticket.created_by_person_id) if ticket.created_by_person_id else None
    result = auto_assign_ticket(
        db,
        str(ticket.id),
        trigger="create",
        actor_person_id=actor_id,
    )
    if result:
        action = "ticket_auto_assigned" if result.assigned else "ticket_auto_assign_noop"
        db.refresh(ticket)
        log_audit_event(
            db,
            None,
            action=action,
            entity_type="ticket",
            entity_id=str(ticket.id),
            actor_id=actor_id,
            metadata={
                "assigned": bool(result.assigned),
                "rule_id": result.rule_id,
                "rule_name": result.rule_name,
                "strategy": result.strategy,
                "candidate_count": result.candidate_count,
                "assignee_person_id": result.assignee_person_id,
                "fallback_service_team_id": result.fallback_service_team_id,
                "reason": result.reason,
            },
            status_code=200,
            is_success=True,
        )
    return result


def _ticket_sla_policy(db: Session) -> SlaPolicy | None:
    return (
        db.query(SlaPolicy)
        .filter(SlaPolicy.entity_type == WorkflowEntityType.ticket)
        .filter(SlaPolicy.name == TICKET_SLA_POLICY_NAME)
        .filter(SlaPolicy.is_active.is_(True))
        .first()
    )


def _latest_ticket_sla_clock(db: Session, ticket_id: UUID) -> SlaClock | None:
    return (
        db.query(SlaClock)
        .filter(SlaClock.entity_type == WorkflowEntityType.ticket, SlaClock.entity_id == ticket_id)
        .order_by(SlaClock.created_at.desc())
        .first()
    )


def _resolve_ticket_sla_target(db: Session, policy_id: UUID, priority: str | None) -> SlaTarget | None:
    query = db.query(SlaTarget).filter(SlaTarget.policy_id == policy_id).filter(SlaTarget.is_active.is_(True))
    if priority:
        target = query.filter(SlaTarget.priority == priority).first()
        if target:
            return target
    return query.filter(SlaTarget.priority.is_(None)).first()


def _complete_ticket_sla_clock(ticket: Ticket, clock: SlaClock, completed_at: datetime) -> None:
    if clock.status == SlaClockStatus.completed:
        if clock.completed_at is None:
            clock.completed_at = completed_at
        return
    clock.status = SlaClockStatus.completed
    clock.completed_at = completed_at
    clock.paused_at = None


def _reopen_ticket_sla_breaches(db: Session, clock: SlaClock) -> None:
    open_breaches = (
        db.query(SlaBreach)
        .filter(SlaBreach.clock_id == clock.id)
        .filter(SlaBreach.status != SlaBreachStatus.resolved)
        .all()
    )
    for breach in open_breaches:
        breach.status = SlaBreachStatus.resolved


def _sync_ticket_sla_clock(db: Session, ticket: Ticket, *, reset_started_at: bool = False) -> None:
    policy = _ticket_sla_policy(db)
    if not policy:
        return

    clock = _latest_ticket_sla_clock(db, ticket.id)
    now = datetime.now(UTC)
    completed_at = ticket.closed_at or ticket.resolved_at or now

    if ticket.status in TICKET_SLA_TERMINAL_STATUSES:
        if clock:
            _complete_ticket_sla_clock(ticket, clock, completed_at)
        return

    priority = ticket.priority.value if ticket.priority else None
    target = _resolve_ticket_sla_target(db, policy.id, priority)
    if not target:
        return

    if not clock or clock.status == SlaClockStatus.completed:
        started_at = now if reset_started_at and clock else (ticket.created_at or now)
        db.add(
            SlaClock(
                policy_id=policy.id,
                entity_type=WorkflowEntityType.ticket,
                entity_id=ticket.id,
                priority=priority,
                status=SlaClockStatus.running,
                started_at=started_at,
                due_at=started_at + timedelta(minutes=target.target_minutes),
            )
        )
        return

    due_at = clock.started_at + timedelta(minutes=target.target_minutes, seconds=clock.total_paused_seconds)
    if clock.status == SlaClockStatus.breached:
        if due_at > now:
            _reopen_ticket_sla_breaches(db, clock)
            clock.breached_at = None
            clock.status = SlaClockStatus.running
            clock.paused_at = None
        clock.priority = priority
        clock.completed_at = None
        clock.due_at = due_at
        return
    if clock.status in {SlaClockStatus.paused, SlaClockStatus.breached}:
        clock.status = SlaClockStatus.running
        clock.paused_at = None
    clock.priority = priority
    clock.completed_at = None
    clock.due_at = due_at


class Tickets(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: TicketCreate):
        if payload.created_by_person_id:
            _ensure_person(db, str(payload.created_by_person_id))
        if payload.assigned_to_person_id:
            _ensure_person(db, str(payload.assigned_to_person_id))
        if payload.assigned_to_person_ids:
            for person_id in payload.assigned_to_person_ids:
                _ensure_person(db, str(person_id))
        if payload.ticket_manager_person_id:
            _ensure_person(db, str(payload.ticket_manager_person_id))
        if payload.assistant_manager_person_id:
            _ensure_person(db, str(payload.assistant_manager_person_id))
        if payload.lead_id:
            _ensure_lead(db, str(payload.lead_id))
        if payload.customer_person_id:
            _ensure_person(db, str(payload.customer_person_id))
        if payload.service_team_id:
            _ensure_service_team(db, str(payload.service_team_id))

        from app.services.ticket_validation import validate_ticket_creation

        validate_ticket_creation(db, payload)

        data = payload.model_dump(exclude={"assigned_to_person_ids"})
        fields_set = payload.model_fields_set
        assignee_ids: list[str] | None = None
        if "assigned_to_person_ids" in fields_set:
            assignee_ids = [str(value) for value in (payload.assigned_to_person_ids or [])]
        elif payload.assigned_to_person_id:
            assignee_ids = [str(payload.assigned_to_person_id)]
        number = generate_number(
            db=db,
            domain=SettingDomain.numbering,
            sequence_key="ticket_number",
            enabled_key="ticket_number_enabled",
            prefix_key="ticket_number_prefix",
            padding_key="ticket_number_padding",
            start_key="ticket_number_start",
        )
        if number:
            data["number"] = number
        # Auto-assign project manager + SPC based on region if not already specified
        if data.get("region"):
            auto_manager, auto_spc = _get_region_ticket_assignments(db, data["region"])
            if auto_manager and not data.get("ticket_manager_person_id"):
                data["ticket_manager_person_id"] = coerce_uuid(auto_manager)
            if auto_spc and not data.get("assistant_manager_person_id"):
                data["assistant_manager_person_id"] = coerce_uuid(auto_spc)
        ticket = Ticket(**data)
        db.add(ticket)
        db.flush()  # Get ticket.id before creating work order
        _sync_ticket_assignees(db, ticket, assignee_ids)

        # Auto-create work order if field_visit tag is present
        if _has_field_visit_tag(payload.tags):
            _auto_create_work_order_for_ticket(db, ticket)

        # Create SLA clock based on ticket type, priority, and channel
        try:
            from app.services.sla_assignment import check_sla_breaches, create_sla_clock_for_ticket

            create_sla_clock_for_ticket(db, ticket)
            check_sla_breaches(db, ticket.id)
        except Exception:
            logger.exception("sla_clock_creation_failed ticket_id=%s", ticket.id)

        db.commit()
        db.refresh(ticket)
        _maybe_auto_assign_ticket(db, ticket)

        # In-app notifications for internal ticket roles.
        # Ticket has already been committed above, so failures here won't roll back creation.
        try:
            role_recipients = _notify_ticket_role_assignment_in_app(
                db,
                ticket=ticket,
                role_assignments={
                    "Technician": ticket.assigned_to_person_id,
                    "Ticket Manager": ticket.ticket_manager_person_id,
                    "Site Project Coordinator": ticket.assistant_manager_person_id,
                },
            )
            if ticket.service_team_id:
                _notify_ticket_service_team_assignment(
                    db,
                    ticket=ticket,
                    service_team_id=ticket.service_team_id,
                    exclude_recipients=role_recipients,
                )
        except Exception:
            db.rollback()
            # Keep creating ticket even if notification creation fails.
            logger.exception("ticket_created_in_app_notifications_failed ticket_id=%s", ticket.id)

        customer_name = _resolve_customer_name(ticket, db)
        customer_email = _resolve_customer_email(ticket, db)

        # Emit ticket.created event
        emit_event(
            db,
            EventType.ticket_created,
            {
                "ticket_id": str(ticket.id),
                "title": ticket.title,
                "subject": ticket.title,
                "status": ticket.status.value if ticket.status else None,
                "priority": ticket.priority.value if ticket.priority else None,
                "channel": ticket.channel.value if ticket.channel else None,
                "customer_name": customer_name,
                "email": customer_email,
                "doc": {
                    "custom_customer_name": customer_name,
                    "name": str(ticket.id),
                    "subject": ticket.title,
                    "status": ticket.status.value if ticket.status else None,
                },
            },
            ticket_id=ticket.id,
            subscriber_id=ticket.subscriber_id,
        )

        technician_contact = _resolve_technician_contact(db, ticket.assigned_to_person_id)
        if technician_contact and technician_contact.get("email"):
            from app.services import email as email_service

            app_url = (email_service.get_app_url(db) or "").rstrip("/")
            ticket_ref = ticket.number or str(ticket.id)
            ticket_url = (
                f"{app_url}/admin/support/tickets/{ticket_ref}" if app_url else f"/admin/support/tickets/{ticket_ref}"
            )
            emit_event(
                db,
                EventType.ticket_assigned,
                {
                    "ticket_id": str(ticket.id),
                    "ticket_url": ticket_url,
                    "title": ticket.title,
                    "subject": ticket.title,
                    "status": ticket.status.value if ticket.status else None,
                    "priority": ticket.priority.value if ticket.priority else None,
                    "channel": ticket.channel.value if ticket.channel else None,
                    "customer_name": customer_name,
                    "technician_name": technician_contact["name"],
                    "email": technician_contact["email"],
                    "technician_email": technician_contact["email"],
                    "technician_doc": {
                        "custom_customer_name": technician_contact["name"],
                        "name": str(ticket.id),
                        "subject": ticket.title,
                        "status": ticket.status.value if ticket.status else None,
                    },
                },
                ticket_id=ticket.id,
                subscriber_id=ticket.subscriber_id,
            )

        return ticket

    @staticmethod
    def auto_assign_manual(db: Session, ticket_id: str, actor_id: str | None = None):
        ticket = db.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

        from app.services.audit_helpers import log_audit_event
        from app.services.ticket_assignment import auto_assign_ticket

        result = auto_assign_ticket(
            db,
            str(ticket.id),
            trigger="manual",
            actor_person_id=actor_id,
        )
        db.refresh(ticket)
        log_audit_event(
            db,
            None,
            action="ticket_auto_assign_manual",
            entity_type="ticket",
            entity_id=str(ticket.id),
            actor_id=actor_id,
            metadata={
                "assigned": bool(result.assigned),
                "rule_id": result.rule_id,
                "rule_name": result.rule_name,
                "strategy": result.strategy,
                "candidate_count": result.candidate_count,
                "assignee_person_id": result.assignee_person_id,
                "fallback_service_team_id": result.fallback_service_team_id,
                "reason": result.reason,
            },
            status_code=200,
            is_success=True,
        )
        return ticket

    @staticmethod
    def get(db: Session, ticket_id: str):
        ticket = db.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return ticket

    @staticmethod
    def get_by_number(db: Session, number: str):
        if not number:
            raise HTTPException(status_code=404, detail="Ticket not found")
        ticket = db.query(Ticket).filter(Ticket.number == number).first()
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return ticket

    @staticmethod
    def get_canonical(db: Session, ticket_id: str):
        ticket = Tickets.get(db, ticket_id)
        canonical = _resolve_merge_chain(ticket)
        if canonical is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return canonical

    @staticmethod
    def resolve_reference(db: Session, ticket_ref: str) -> Ticket:
        if not ticket_ref:
            raise HTTPException(status_code=404, detail="Ticket not found")
        ticket = db.query(Ticket).filter(Ticket.number == ticket_ref).first()
        if ticket:
            return ticket
        return Tickets.get(db, ticket_ref)

    @staticmethod
    def link_related_outage(
        db: Session,
        *,
        from_ticket_id: str,
        to_ticket_id: str,
        actor_id: str | None = None,
    ) -> TicketLink:
        from_ticket = Tickets.get(db, from_ticket_id)
        to_ticket = Tickets.get(db, to_ticket_id)
        _ensure_ticket_not_merged_source(from_ticket)
        canonical_target = _resolve_merge_chain(to_ticket)
        if canonical_target is None:
            raise HTTPException(status_code=404, detail="Target ticket not found")
        if canonical_target.id == from_ticket.id:
            raise HTTPException(status_code=400, detail="A ticket cannot be linked to itself")

        existing = (
            db.query(TicketLink)
            .filter(TicketLink.from_ticket_id == from_ticket.id)
            .filter(TicketLink.link_type == RELATED_OUTAGE_LINK_TYPE)
            .first()
        )
        actor_uuid = coerce_uuid(actor_id) if actor_id else None
        if existing:
            existing.to_ticket_id = canonical_target.id
            if actor_uuid:
                existing.created_by_person_id = actor_uuid
            link = existing
        else:
            link = TicketLink(
                from_ticket_id=from_ticket.id,
                to_ticket_id=canonical_target.id,
                link_type=RELATED_OUTAGE_LINK_TYPE,
                created_by_person_id=actor_uuid,
            )
            db.add(link)

        _upsert_internal_comment(
            db,
            ticket_id=from_ticket.id,
            author_person_id=actor_uuid,
            body=f"System: Linked this ticket to outage ticket {_ticket_ref(canonical_target)}.",
        )
        _upsert_internal_comment(
            db,
            ticket_id=canonical_target.id,
            author_person_id=actor_uuid,
            body=f"System: Linked ticket {_ticket_ref(from_ticket)} to this outage ticket.",
        )

        db.commit()
        db.refresh(link)
        return link

    @staticmethod
    def merge(
        db: Session,
        *,
        source_ticket_id: str,
        target_ticket_id: str,
        actor_id: str | None = None,
        reason: str | None = None,
    ) -> Ticket:
        source = Tickets.get(db, source_ticket_id)
        target = Tickets.get(db, target_ticket_id)
        _ensure_ticket_not_merged_source(source)
        canonical_target = _resolve_merge_chain(target)
        if canonical_target is None:
            raise HTTPException(status_code=404, detail="Target ticket not found")
        if source.id == canonical_target.id:
            raise HTTPException(status_code=400, detail="A ticket cannot be merged into itself")

        actor_uuid = coerce_uuid(actor_id) if actor_id else None

        existing_target_assignees = {str(assignee.person_id) for assignee in canonical_target.assignees}
        source_assignees = {str(assignee.person_id) for assignee in source.assignees}
        if source.assigned_to_person_id:
            source_assignees.add(str(source.assigned_to_person_id))
        merged_assignees = sorted(existing_target_assignees | source_assignees)
        _sync_ticket_assignees(db, canonical_target, merged_assignees)

        target_tags = list(canonical_target.tags or [])
        for tag in source.tags or []:
            if tag not in target_tags:
                target_tags.append(tag)
        canonical_target.tags = target_tags or None

        if not canonical_target.subscriber_id and source.subscriber_id:
            canonical_target.subscriber_id = source.subscriber_id
        if not canonical_target.customer_person_id and source.customer_person_id:
            canonical_target.customer_person_id = source.customer_person_id
        if not canonical_target.lead_id and source.lead_id:
            canonical_target.lead_id = source.lead_id
        if not canonical_target.service_team_id and source.service_team_id:
            canonical_target.service_team_id = source.service_team_id
        if not canonical_target.ticket_manager_person_id and source.ticket_manager_person_id:
            canonical_target.ticket_manager_person_id = source.ticket_manager_person_id
        if not canonical_target.assistant_manager_person_id and source.assistant_manager_person_id:
            canonical_target.assistant_manager_person_id = source.assistant_manager_person_id

        _merge_ticket_attachments(canonical_target, source)

        for comment in list(source.comments or []):
            db.add(
                TicketComment(
                    ticket_id=canonical_target.id,
                    author_person_id=comment.author_person_id,
                    body=comment.body,
                    is_internal=comment.is_internal,
                    attachments=comment.attachments,
                    created_at=comment.created_at,
                )
            )

        merge_links = (
            db.query(TicketLink)
            .filter(or_(TicketLink.from_ticket_id == source.id, TicketLink.to_ticket_id == source.id))
            .all()
        )
        for link in merge_links:
            new_from = canonical_target.id if link.from_ticket_id == source.id else link.from_ticket_id
            new_to = canonical_target.id if link.to_ticket_id == source.id else link.to_ticket_id
            if new_from == new_to:
                db.delete(link)
                continue
            duplicate = (
                db.query(TicketLink)
                .filter(TicketLink.id != link.id)
                .filter(TicketLink.from_ticket_id == new_from)
                .filter(TicketLink.to_ticket_id == new_to)
                .filter(TicketLink.link_type == link.link_type)
                .first()
            )
            if duplicate:
                db.delete(link)
                continue
            link.from_ticket_id = new_from
            link.to_ticket_id = new_to

        source.status = TicketStatus.merged
        source.merged_into_ticket_id = canonical_target.id
        source.closed_at = source.closed_at or datetime.now(UTC)
        _sync_ticket_sla_clock(db, source)

        db.add(
            TicketMerge(
                source_ticket_id=source.id,
                target_ticket_id=canonical_target.id,
                reason=reason.strip() if reason else None,
                merged_by_person_id=actor_uuid,
            )
        )
        _upsert_internal_comment(
            db,
            ticket_id=source.id,
            author_person_id=actor_uuid,
            body=_comment_merge_note(source, canonical_target, reason, "source"),
        )
        _upsert_internal_comment(
            db,
            ticket_id=canonical_target.id,
            author_person_id=actor_uuid,
            body=_comment_merge_note(source, canonical_target, reason, "target"),
        )

        db.commit()
        db.refresh(canonical_target)
        return canonical_target

    @staticmethod
    def related_outage_context(db: Session, *, ticket_id: str) -> dict[str, Any]:
        ticket = Tickets.get(db, ticket_id)
        parent_link = (
            db.query(TicketLink)
            .filter(TicketLink.from_ticket_id == ticket.id)
            .filter(TicketLink.link_type == RELATED_OUTAGE_LINK_TYPE)
            .first()
        )
        child_links = (
            db.query(TicketLink)
            .filter(TicketLink.to_ticket_id == ticket.id)
            .filter(TicketLink.link_type == RELATED_OUTAGE_LINK_TYPE)
            .all()
        )

        primary_ticket = None
        sibling_tickets: list[Ticket] = []
        linked_tickets: list[Ticket] = []

        if parent_link:
            primary_ticket = Tickets.get(db, str(parent_link.to_ticket_id))
            if primary_ticket:
                sibling_ids = [
                    link.from_ticket_id
                    for link in db.query(TicketLink)
                    .filter(TicketLink.to_ticket_id == primary_ticket.id)
                    .filter(TicketLink.link_type == RELATED_OUTAGE_LINK_TYPE)
                    .filter(TicketLink.from_ticket_id != ticket.id)
                    .all()
                ]
                if sibling_ids:
                    sibling_tickets = (
                        db.query(Ticket).filter(Ticket.id.in_(sibling_ids)).order_by(Ticket.created_at.asc()).all()
                    )

        if child_links:
            linked_ids = [link.from_ticket_id for link in child_links]
            if linked_ids:
                linked_tickets = (
                    db.query(Ticket).filter(Ticket.id.in_(linked_ids)).order_by(Ticket.created_at.asc()).all()
                )

        return {
            "primary_ticket": primary_ticket,
            "linked_tickets": linked_tickets,
            "sibling_tickets": sibling_tickets,
        }

    @staticmethod
    def list(
        db: Session,
        subscriber_id: str | None,
        status: str | None,
        priority: str | None,
        channel: str | None,
        search: str | None,
        created_by_person_id: str | None,
        assigned_to_person_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
        filters_payload: list[Any] | None = None,
    ):
        # Use query builder for cleaner, composable filtering
        query = (
            TicketQuery(db)
            .by_subscriber(subscriber_id)
            .by_status(status)
            .by_priority(priority)
            .by_channel(channel)
            .search(search)
            .by_created_by(created_by_person_id)
            .by_assigned_to_or_team_member(assigned_to_person_id)
        )
        # Apply active filter
        if is_active is None:
            query = query.active_only()
        elif is_active:
            query = query.active_only(True)
        else:
            query = query.active_only(False)
        if filters_payload:
            from app.services.filter_engine import apply_filter_payload

            query._query = apply_filter_payload(query._query, "Ticket", filters_payload)

        return (
            query.with_relations()  # Eager load relationships to avoid N+1
            .order_by(order_by, order_dir)
            .paginate(limit, offset)
            .all()
        )

    @staticmethod
    def status_stats(db: Session) -> dict:
        """Get ticket counts by status."""
        from sqlalchemy import func

        rows = (
            db.query(Ticket.status, func.count(Ticket.id))
            .filter(Ticket.is_active.is_(True))
            .group_by(Ticket.status)
            .all()
        )
        counts = {status.value if status else "unknown": count for status, count in rows}
        total = sum(counts.values())
        return {
            "total": total,
            "new": counts.get("new", 0),
            "open": counts.get("open", 0),
            "pending": counts.get("pending", 0),
            "on_hold": counts.get("on_hold", 0),
            "resolved": counts.get("resolved", 0),
            "closed": counts.get("closed", 0),
            "merged": counts.get("merged", 0),
        }

    @staticmethod
    def update(db: Session, ticket_id: str, payload: TicketUpdate):
        ticket = db.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        _ensure_ticket_not_merged_source(ticket)
        previous_status = ticket.status
        previous_priority = ticket.priority
        previous_assigned_to = ticket.assigned_to_person_id
        previous_ticket_manager = ticket.ticket_manager_person_id
        previous_assistant_manager = ticket.assistant_manager_person_id
        previous_service_team = ticket.service_team_id
        data = payload.model_dump(exclude_unset=True)
        fields_set = payload.model_fields_set
        assignee_ids: list[str] | None = None
        if "assigned_to_person_ids" in payload.model_fields_set:
            assignee_ids = [str(value) for value in (payload.assigned_to_person_ids or [])]
        elif "assigned_to_person_id" in data:
            if data.get("assigned_to_person_id"):
                assignee_ids = [str(data["assigned_to_person_id"])]
            else:
                assignee_ids = []
        data.pop("assigned_to_person_ids", None)
        if data.get("created_by_person_id"):
            _ensure_person(db, str(data["created_by_person_id"]))
        if data.get("assigned_to_person_id"):
            _ensure_person(db, str(data["assigned_to_person_id"]))
        if data.get("ticket_manager_person_id"):
            _ensure_person(db, str(data["ticket_manager_person_id"]))
        if data.get("assistant_manager_person_id"):
            _ensure_person(db, str(data["assistant_manager_person_id"]))
        if data.get("lead_id"):
            _ensure_lead(db, str(data["lead_id"]))
        if data.get("customer_person_id"):
            _ensure_person(db, str(data["customer_person_id"]))
        if data.get("service_team_id"):
            _ensure_service_team(db, str(data["service_team_id"]))

        # Auto-assign project manager + SPC based on region if region changes and no manager is set
        new_region = data.get("region")
        manager_explicit = "ticket_manager_person_id" in fields_set
        assistant_explicit = "assistant_manager_person_id" in fields_set
        current_manager = (
            data.get("ticket_manager_person_id")
            if "ticket_manager_person_id" in data
            else ticket.ticket_manager_person_id
        )
        current_assistant = (
            data.get("assistant_manager_person_id")
            if "assistant_manager_person_id" in data
            else ticket.assistant_manager_person_id
        )
        if new_region:
            auto_manager, auto_spc = _get_region_ticket_assignments(db, new_region)
            if auto_manager and not current_manager and not manager_explicit:
                data["ticket_manager_person_id"] = coerce_uuid(auto_manager)
            if auto_spc and not current_assistant and not assistant_explicit:
                data["assistant_manager_person_id"] = coerce_uuid(auto_spc)

        # Check if field_visit tag is being added
        had_field_visit = _has_field_visit_tag(ticket.tags)
        new_tags = data.get("tags")
        will_have_field_visit = _has_field_visit_tag(new_tags) if new_tags is not None else had_field_visit

        for key, value in data.items():
            setattr(ticket, key, value)
        _sync_ticket_assignees(db, ticket, assignee_ids)

        # Auto-create work order if field_visit tag is newly added
        if will_have_field_visit and not had_field_visit:
            _auto_create_work_order_for_ticket(db, ticket)

        try:
            from app.services.sla_assignment import check_sla_breaches, update_sla_clocks_for_status_change

            # Status transitions can pause/resume/complete clocks, but every runtime update
            # should also evaluate whether any active clock has already crossed due_at.
            if previous_status != ticket.status:
                update_sla_clocks_for_status_change(db, ticket, previous_status, ticket.status)
            check_sla_breaches(db, ticket.id)
        except Exception:
            logger.exception("sla_clock_update_failed ticket_id=%s", ticket.id)

        db.commit()
        db.refresh(ticket)

        # In-app notifications when internal roles change.
        try:
            changed_roles: dict[str, object] = {}
            if ticket.assigned_to_person_id and ticket.assigned_to_person_id != previous_assigned_to:
                changed_roles["Technician"] = ticket.assigned_to_person_id
            if ticket.ticket_manager_person_id and ticket.ticket_manager_person_id != previous_ticket_manager:
                changed_roles["Ticket Manager"] = ticket.ticket_manager_person_id
            if ticket.assistant_manager_person_id and ticket.assistant_manager_person_id != previous_assistant_manager:
                changed_roles["Site Project Coordinator"] = ticket.assistant_manager_person_id
            role_recipients: set[str] = set()
            if changed_roles:
                role_recipients = _notify_ticket_role_assignment_in_app(
                    db, ticket=ticket, role_assignments=changed_roles
                )
            if ticket.service_team_id and ticket.service_team_id != previous_service_team:
                _notify_ticket_service_team_assignment(
                    db,
                    ticket=ticket,
                    service_team_id=ticket.service_team_id,
                    exclude_recipients=role_recipients,
                )
        except Exception:
            db.rollback()
            logger.exception("ticket_role_change_in_app_notifications_failed ticket_id=%s", ticket.id)

        # Emit ticket events based on status transitions
        new_status = ticket.status
        new_priority = ticket.priority
        event_payload: dict[str, object | None] = {
            "ticket_id": str(ticket.id),
            "title": ticket.title,
            "subject": ticket.title,
            "from_status": previous_status.value if previous_status else None,
            "to_status": new_status.value if new_status else None,
            "status": new_status.value if new_status else None,
        }

        if previous_status != new_status and new_status == TicketStatus.closed:
            customer_name = _resolve_customer_name(ticket, db)
            customer_email = _resolve_customer_email(ticket, db)
            event_payload["customer_name"] = customer_name
            event_payload["email"] = customer_email
            event_payload["doc"] = {
                "custom_customer_name": customer_name,
                "name": str(ticket.id),
                "subject": ticket.title,
                "status": new_status.value if new_status else None,
            }
            technician_contact = _resolve_technician_contact(db, ticket.assigned_to_person_id)
            if technician_contact and technician_contact.get("email"):
                event_payload["technician_name"] = technician_contact["name"]
                event_payload["technician_email"] = technician_contact["email"]
                event_payload["technician_doc"] = {
                    "custom_customer_name": technician_contact["name"],
                    "name": str(ticket.id),
                    "subject": ticket.title,
                    "status": new_status.value if new_status else None,
                }
            emit_event(
                db,
                EventType.ticket_resolved,
                event_payload,
                subscriber_id=ticket.subscriber_id,
                ticket_id=ticket.id,
            )

        if ticket.assigned_to_person_id and ticket.assigned_to_person_id != previous_assigned_to:
            customer_name = _resolve_customer_name(ticket, db)
            technician_contact = _resolve_technician_contact(db, ticket.assigned_to_person_id)
            if technician_contact and technician_contact.get("email"):
                from app.services import email as email_service

                app_url = (email_service.get_app_url(db) or "").rstrip("/")
                ticket_ref = ticket.number or str(ticket.id)
                ticket_url = (
                    f"{app_url}/admin/support/tickets/{ticket_ref}"
                    if app_url
                    else f"/admin/support/tickets/{ticket_ref}"
                )
                emit_event(
                    db,
                    EventType.ticket_assigned,
                    {
                        "ticket_id": str(ticket.id),
                        "ticket_url": ticket_url,
                        "title": ticket.title,
                        "subject": ticket.title,
                        "status": ticket.status.value if ticket.status else None,
                        "priority": ticket.priority.value if ticket.priority else None,
                        "channel": ticket.channel.value if ticket.channel else None,
                        "customer_name": customer_name,
                        "technician_name": technician_contact["name"],
                        "email": technician_contact["email"],
                        "technician_email": technician_contact["email"],
                        "technician_doc": {
                            "custom_customer_name": technician_contact["name"],
                            "name": str(ticket.id),
                            "subject": ticket.title,
                            "status": ticket.status.value if ticket.status else None,
                        },
                    },
                    subscriber_id=ticket.subscriber_id,
                    ticket_id=ticket.id,
                )
        # Emit escalated event if priority increased to critical
        if (
            previous_priority != new_priority
            and new_priority == TicketPriority.urgent
            and previous_priority != TicketPriority.urgent
        ):
            emit_event(
                db,
                EventType.ticket_escalated,
                event_payload,
                subscriber_id=ticket.subscriber_id,
                ticket_id=ticket.id,
            )
        # Emit generic update event for ERP sync (if not already emitting resolved/escalated)
        elif previous_status != new_status or len(data) > 1:
            emit_event(
                db,
                EventType.ticket_updated,
                {
                    **event_payload,
                    "changed_fields": list(data.keys()),
                },
                subscriber_id=ticket.subscriber_id,
                ticket_id=ticket.id,
            )

        return ticket

    @staticmethod
    def notify_customer_of_public_technician_comment(
        db: Session,
        *,
        ticket_id: str,
        comment_id: str,
        actor_person_id: str | None,
        request=None,
    ) -> dict | None:
        if not actor_person_id:
            return None

        ticket = db.get(Ticket, coerce_uuid(ticket_id))
        comment = db.get(TicketComment, coerce_uuid(comment_id))
        if not ticket or not comment or comment.is_internal:
            return None

        actor_uuid = coerce_uuid(actor_person_id)
        if comment.author_person_id != actor_uuid or not _ticket_has_technician(ticket, actor_uuid):
            return None

        customer_name = _resolve_customer_name(ticket, db)
        customer_email = _resolve_customer_email(ticket, db)
        technician_contact = _resolve_technician_contact(db, actor_uuid)

        try:
            from app.services.ai.use_cases.ticket_customer_update import draft_customer_ticket_update

            draft = draft_customer_ticket_update(
                db,
                request=request,
                ticket_id=str(ticket.id),
                comment_id=str(comment.id),
                actor_person_id=actor_person_id,
            )
            update_message = draft.update_message
            ai_meta = draft.meta
        except Exception:
            logger.exception("ticket_customer_update_ai_failed ticket_id=%s comment_id=%s", ticket.id, comment.id)
            update_message = _fallback_customer_update_message(comment.body)
            ai_meta = {"fallback": True}

        from app.services import email as email_service
        from app.services.branding import get_branding

        app_url = (email_service.get_app_url(db) or "").rstrip("/")
        ticket_ref = ticket.number or str(ticket.id)
        ticket_url = (
            f"{app_url}/admin/support/tickets/{ticket_ref}" if app_url else f"/admin/support/tickets/{ticket_ref}"
        )
        company_name = get_branding(db).get("company_name") or "Dotmac"

        emit_event(
            db,
            EventType.ticket_customer_update,
            {
                "ticket_id": str(ticket.id),
                "ticket_number": ticket_ref,
                "ticket_subject": ticket.title,
                "title": ticket.title,
                "subject": ticket.title,
                "status": ticket.status.value if ticket.status else None,
                "customer_name": customer_name,
                "email": customer_email,
                "update_message": update_message,
                "ticket_link": ticket_url,
                "ticket_url": ticket_url,
                "company_name": company_name,
                "comment_id": str(comment.id),
                "technician_name": technician_contact["name"] if technician_contact else None,
                "ai_meta": ai_meta,
            },
            actor=actor_person_id,
            ticket_id=ticket.id,
            subscriber_id=ticket.subscriber_id,
        )
        return {
            "ticket_id": str(ticket.id),
            "comment_id": str(comment.id),
            "update_message": update_message,
            "ai_meta": ai_meta,
        }

    @staticmethod
    def bulk_update(db: Session, ticket_ids: builtins.list[str], payload: TicketUpdate) -> int:
        if not ticket_ids:
            raise HTTPException(status_code=400, detail="ticket_ids required")
        data = payload.model_dump(exclude_unset=True)
        if not data:
            raise HTTPException(status_code=400, detail="Update payload required")
        if data.get("created_by_person_id"):
            _ensure_person(db, str(data["created_by_person_id"]))
        if data.get("assigned_to_person_id"):
            _ensure_person(db, str(data["assigned_to_person_id"]))
        ids = [coerce_uuid(ticket_id) for ticket_id in ticket_ids]
        tickets = db.query(Ticket).filter(Ticket.id.in_(ids)).all()
        if len(tickets) != len(ids):
            raise HTTPException(status_code=404, detail="One or more tickets not found")
        for ticket in tickets:
            for key, value in data.items():
                setattr(ticket, key, value)
        db.commit()
        return len(tickets)

    @staticmethod
    def bulk_update_response(db: Session, ticket_ids: builtins.list[str], payload: TicketUpdate) -> dict:
        updated = Tickets.bulk_update(db, ticket_ids, payload)
        return {"updated": updated}

    @staticmethod
    def delete(db: Session, ticket_id: str):
        ticket = db.get(Ticket, ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        ticket.is_active = False
        db.commit()


class TicketComments(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: TicketCommentCreate):
        ticket = db.get(Ticket, payload.ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        _ensure_ticket_not_merged_source(ticket)
        if payload.author_person_id:
            _ensure_person(db, str(payload.author_person_id))
        comment = TicketComment(**payload.model_dump())
        db.add(comment)
        db.commit()
        db.refresh(comment)
        return comment

    @staticmethod
    def bulk_create(db: Session, payload: TicketCommentBulkCreateRequest) -> list[TicketComment]:
        if not payload.ticket_ids:
            raise HTTPException(status_code=400, detail="ticket_ids required")
        if payload.author_person_id:
            _ensure_person(db, str(payload.author_person_id))
        ids = [coerce_uuid(ticket_id) for ticket_id in payload.ticket_ids]
        tickets = db.query(Ticket).filter(Ticket.id.in_(ids)).all()
        if len(tickets) != len(ids):
            raise HTTPException(status_code=404, detail="One or more tickets not found")
        comments: list[TicketComment] = []
        for ticket in tickets:
            comment = TicketComment(
                ticket_id=ticket.id,
                author_person_id=payload.author_person_id,
                body=payload.body,
                is_internal=payload.is_internal,
                attachments=payload.attachments,
            )
            db.add(comment)
            comments.append(comment)
        db.commit()
        for comment in comments:
            db.refresh(comment)
        return comments

    @staticmethod
    def bulk_create_response(db: Session, payload: TicketCommentBulkCreateRequest) -> dict:
        comments = TicketComments.bulk_create(db, payload)
        return {"created": len(comments), "comment_ids": [comment.id for comment in comments]}

    @staticmethod
    def get(db: Session, comment_id: str):
        comment = db.get(TicketComment, comment_id)
        if not comment:
            raise HTTPException(status_code=404, detail="Ticket comment not found")
        return comment

    @staticmethod
    def list(
        db: Session,
        ticket_id: str | None,
        is_internal: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        return (
            TicketCommentQuery(db)
            .by_ticket(ticket_id)
            .is_internal(is_internal)
            .with_author()  # Eager load author to avoid N+1
            .order_by(order_by, order_dir)
            .paginate(limit, offset)
            .all()
        )

    @staticmethod
    def update(db: Session, comment_id: str, payload: TicketCommentUpdate):
        comment = db.get(TicketComment, comment_id)
        if not comment:
            raise HTTPException(status_code=404, detail="Ticket comment not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(comment, key, value)
        db.commit()
        db.refresh(comment)
        return comment

    @staticmethod
    def delete(db: Session, comment_id: str):
        comment = db.get(TicketComment, comment_id)
        if not comment:
            raise HTTPException(status_code=404, detail="Ticket comment not found")
        db.delete(comment)
        db.commit()


class TicketSlaEvents(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: TicketSlaEventCreate):
        ticket = db.get(Ticket, payload.ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        event = TicketSlaEvent(**payload.model_dump())
        db.add(event)
        db.commit()
        db.refresh(event)
        return event

    @staticmethod
    def get(db: Session, event_id: str):
        event = db.get(TicketSlaEvent, event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Ticket SLA event not found")
        return event

    @staticmethod
    def list(
        db: Session,
        ticket_id: str | None,
        event_type: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        return (
            TicketSlaEventQuery(db)
            .by_ticket(ticket_id)
            .by_event_type(event_type)
            .order_by(order_by, order_dir)
            .paginate(limit, offset)
            .all()
        )

    @staticmethod
    def update(db: Session, event_id: str, payload: TicketSlaEventUpdate):
        event = db.get(TicketSlaEvent, event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Ticket SLA event not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(event, key, value)
        db.commit()
        db.refresh(event)
        return event

    @staticmethod
    def delete(db: Session, event_id: str):
        event = db.get(TicketSlaEvent, event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Ticket SLA event not found")
        db.delete(event)
        db.commit()


tickets = Tickets()
ticket_comments = TicketComments()
ticket_sla_events = TicketSlaEvents()

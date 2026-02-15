from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.dispatch import WorkOrderAssignmentQueue
from app.models.person import Person
from app.models.workforce import WorkOrder, WorkOrderAssignment, WorkOrderNote, WorkOrderStatus
from app.services.ai.redaction import redact_text
from app.services.common import coerce_uuid


def gather_dispatch_context(db: Session, params: dict[str, Any]) -> str:
    work_order_id = params.get("work_order_id")
    if not work_order_id:
        raise ValueError("work_order_id is required")

    wo = db.get(WorkOrder, coerce_uuid(work_order_id))
    if not wo:
        raise ValueError("Work order not found")

    max_notes = min(int(params.get("max_notes", 6)), 20)
    max_chars = int(params.get("max_chars", 600))

    def _person_name(person_id) -> str | None:
        if not person_id:
            return None
        p = db.get(Person, person_id)
        if not p:
            return None
        return redact_text(p.display_name or "", max_chars=120) or None

    status = wo.status.value if isinstance(wo.status, WorkOrderStatus) else str(wo.status)
    lines: list[str] = [
        f"Work order ID: {str(wo.id)[:8]}",
        f"Title: {redact_text(wo.title or '', max_chars=200)}",
        f"Status: {status}",
        f"Priority: {wo.priority.value if hasattr(wo.priority, 'value') else str(wo.priority)}",
        f"Type: {wo.work_type.value if hasattr(wo.work_type, 'value') else str(wo.work_type)}",
        f"Created: {wo.created_at.isoformat() if wo.created_at else 'unknown'}",
        f"Updated: {wo.updated_at.isoformat() if wo.updated_at else 'unknown'}",
        f"Scheduled start: {wo.scheduled_start.isoformat() if wo.scheduled_start else 'unknown'}",
        f"Scheduled end: {wo.scheduled_end.isoformat() if wo.scheduled_end else 'unknown'}",
        f"Estimated duration minutes: {int(wo.estimated_duration_minutes) if wo.estimated_duration_minutes else 'unknown'}",
        f"Estimated arrival: {wo.estimated_arrival_at.isoformat() if wo.estimated_arrival_at else 'unknown'}",
        f"Required skills: {', '.join([str(s) for s in (wo.required_skills or [])][:12]) or 'none'}",
        f"Description: {redact_text(wo.description or '', max_chars=900)}",
        f"Ticket ID: {str(wo.ticket_id)[:8] if wo.ticket_id else 'none'}",
        f"Project ID: {str(wo.project_id)[:8] if wo.project_id else 'none'}",
    ]

    assignee = _person_name(wo.assigned_to_person_id)
    lines.append(f"Assigned to: {assignee or 'UNASSIGNED'}")

    queue_entry = (
        db.query(WorkOrderAssignmentQueue)
        .filter(WorkOrderAssignmentQueue.work_order_id == wo.id)
        .order_by(WorkOrderAssignmentQueue.created_at.desc())
        .first()
    )
    if queue_entry:
        lines.append(f"Dispatch queue status: {queue_entry.status.value}")
        if queue_entry.reason:
            lines.append(f"Dispatch queue reason: {redact_text(queue_entry.reason, max_chars=240)}")

    assignments = (
        db.query(WorkOrderAssignment)
        .filter(WorkOrderAssignment.work_order_id == wo.id)
        .order_by(WorkOrderAssignment.assigned_at.desc())
        .limit(8)
        .all()
    )
    if assignments:
        lines.append("Assignments:")
        for a in assignments:
            who = _person_name(a.person_id) or str(a.person_id)[:8]
            role = redact_text(a.role or "", max_chars=80) or "unknown_role"
            when = a.assigned_at.isoformat() if a.assigned_at else "unknown"
            primary = " primary" if a.is_primary else ""
            lines.append(f"  - {when} {who} role={role}{primary}")

    notes = (
        db.query(WorkOrderNote)
        .filter(WorkOrderNote.work_order_id == wo.id)
        .order_by(WorkOrderNote.created_at.desc())
        .limit(max(0, max_notes))
        .all()
    )
    if notes:
        lines.append("Recent notes:")
        for n in reversed(notes):
            who = _person_name(n.author_person_id) or "unknown"
            when = n.created_at.isoformat() if n.created_at else "unknown"
            kind = "internal" if n.is_internal else "public"
            lines.append(f"  - [{kind}] {when} {who}: {redact_text(n.body or '', max_chars=max_chars)}")

    return "\n".join([line for line in lines if line.strip()])

"""Field job transitions — the mobile execution state machine.

This service composes the existing engines rather than replacing them:
- ``workflow.transition_work_order()`` owns status changes, transition rules,
  and ``started_at``/``completed_at`` timestamps.
- ``workforce.emit_work_order_status_events()`` owns domain events (ERP sync,
  webhooks, surveys, automation all hang off those).

What this layer adds: caller authorization (primary tech only), offline
idempotency via ``client_event_id``, GPS-stamped ``WorkOrderEvent`` facts,
audit rows, and the completion evidence gate.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit import AuditActorType, AuditEvent
from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.field import FieldAttachment, FieldAttachmentKind, FieldJobEvent, WorkOrderEvent
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.schemas.workflow import StatusTransitionRequest
from app.services import workflow as workflow_service
from app.services.common import coerce_uuid, validate_enum
from app.services.field.jobs import get_scoped_work_order
from app.services.workforce import emit_work_order_status_events

_CLOCK_SKEW_FLAG_SECONDS = 15 * 60

# Mobile events → target WorkOrderStatus. ``accept`` and ``resume`` are
# recorded as facts without a status change; ``hold`` keeps in_progress (the
# job stays open overnight) and is visible through the event stream.
_EVENT_TO_STATUS: dict[FieldJobEvent, WorkOrderStatus | None] = {
    FieldJobEvent.accept: None,
    FieldJobEvent.en_route: WorkOrderStatus.dispatched,
    FieldJobEvent.start: WorkOrderStatus.in_progress,
    FieldJobEvent.hold: None,
    FieldJobEvent.resume: None,
    FieldJobEvent.complete: WorkOrderStatus.completed,
    # A failed visit cancels the order (with a reason); dispatch reschedules.
    FieldJobEvent.unable_to_complete: WorkOrderStatus.canceled,
}

_TRANSITION_ALLOWED_FROM: dict[FieldJobEvent, set[WorkOrderStatus]] = {
    FieldJobEvent.accept: {WorkOrderStatus.scheduled, WorkOrderStatus.dispatched},
    FieldJobEvent.en_route: {WorkOrderStatus.scheduled, WorkOrderStatus.dispatched},
    FieldJobEvent.start: {WorkOrderStatus.scheduled, WorkOrderStatus.dispatched},
    FieldJobEvent.hold: {WorkOrderStatus.in_progress},
    FieldJobEvent.resume: {WorkOrderStatus.in_progress},
    FieldJobEvent.complete: {WorkOrderStatus.in_progress},
    # Allowed any time the tech has the job in hand but can't finish it.
    FieldJobEvent.unable_to_complete: {
        WorkOrderStatus.scheduled,
        WorkOrderStatus.dispatched,
        WorkOrderStatus.in_progress,
    },
}

# Structured outcomes for a failed visit; kept on the event payload so dispatch
# can triage (and so reporting can aggregate) rather than free text alone.
_UNABLE_REASONS = {"customer_absent", "no_access", "site_not_ready", "needs_parts", "unsafe", "other"}


def _completion_gate_enabled(db: Session) -> bool:
    row = (
        db.query(DomainSetting)
        .filter(DomainSetting.domain == SettingDomain.field)
        .filter(DomainSetting.key == "completion_requires_evidence")
        .filter(DomainSetting.is_active.is_(True))
        .first()
    )
    if not row:
        return True  # default on: evidence is the point of the app
    value = row.value_json if row.value_json is not None else row.value_text
    return str(value).lower() not in ("false", "0", "no")


def _check_completion_gate(db: Session, work_order: WorkOrder, payload: dict | None) -> None:
    if not _completion_gate_enabled(db):
        return
    attachments = (
        db.query(FieldAttachment)
        .filter(FieldAttachment.work_order_id == work_order.id)
        .filter(FieldAttachment.is_active.is_(True))
        .all()
    )
    has_photo = any(a.kind == FieldAttachmentKind.photo for a in attachments)
    has_signature = any(a.kind == FieldAttachmentKind.signature for a in attachments)
    signature_fallback = bool((payload or {}).get("signature_unavailable_reason"))
    if not has_photo:
        raise HTTPException(status_code=422, detail="Completion requires at least one photo")
    if not has_signature and not signature_fallback:
        raise HTTPException(
            status_code=422,
            detail="Completion requires a customer signature or a signature_unavailable_reason",
        )


def _is_primary_actor(work_order: WorkOrder, person_uuid) -> bool:
    if work_order.assigned_to_person_id == person_uuid:
        return True
    return any(a.person_id == person_uuid and a.is_primary for a in work_order.assignments)


class FieldTransitions:
    @staticmethod
    def apply(
        db: Session,
        person_id: str,
        work_order_id: str,
        *,
        event: str,
        client_event_id: str,
        occurred_at: str | datetime | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        note: str | None = None,
        payload: dict | None = None,
    ) -> dict:
        event_value = validate_enum(event, FieldJobEvent, "event")
        client_uuid = coerce_uuid(client_event_id)
        if not client_uuid:
            raise HTTPException(status_code=422, detail="client_event_id is required")

        # Idempotent replay: same client_event_id returns the original result.
        # Still enforce caller access so a replayed/guessed client_event_id can't
        # leak another technician's work order (uniform 404 on mismatch).
        existing = db.query(WorkOrderEvent).filter(WorkOrderEvent.client_event_id == client_uuid).first()
        if existing:
            work_order = get_scoped_work_order(db, person_id, str(existing.work_order_id))
            return {"work_order": work_order, "event": existing, "replayed": True}

        work_order = get_scoped_work_order(db, person_id, work_order_id)
        person_uuid = coerce_uuid(person_id)
        if not _is_primary_actor(work_order, person_uuid):
            raise HTTPException(status_code=403, detail="Only the assigned technician can transition this job")

        if work_order.status not in _TRANSITION_ALLOWED_FROM[event_value]:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot {event_value.value} a job in status {work_order.status.value}",
            )

        # hold/resume are event-only (no status change), so the allowed-from
        # check above can't catch a redundant repeat (hold→hold, resume→resume)
        # from a double-tap with a fresh client_event_id. Collapse those to an
        # idempotent no-op against the latest pause fact so they don't litter
        # the event stream or skew duration reporting.
        if event_value in (FieldJobEvent.hold, FieldJobEvent.resume):
            latest_pause = (
                db.query(WorkOrderEvent)
                .filter(WorkOrderEvent.work_order_id == work_order.id)
                .filter(WorkOrderEvent.event.in_([FieldJobEvent.hold, FieldJobEvent.resume]))
                .order_by(WorkOrderEvent.occurred_at.desc(), WorkOrderEvent.received_at.desc())
                .first()
            )
            if latest_pause is not None and latest_pause.event == event_value:
                return {"work_order": work_order, "event": latest_pause, "replayed": True}

        now = datetime.now(UTC)
        occurred = _coerce_occurred_at(occurred_at) or now
        event_payload = dict(payload or {})
        if note:
            event_payload["note"] = note
        skew = abs((now - occurred).total_seconds())
        if skew > _CLOCK_SKEW_FLAG_SECONDS:
            event_payload["clock_skew_seconds"] = int(skew)

        if event_value == FieldJobEvent.unable_to_complete:
            reason = event_payload.get("reason")
            reason = reason.strip() if isinstance(reason, str) else reason
            if not reason:
                raise HTTPException(status_code=422, detail="unable_to_complete requires a reason")
            if reason not in _UNABLE_REASONS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid reason '{reason}'. Allowed: {', '.join(sorted(_UNABLE_REASONS))}",
                )
            event_payload["reason"] = reason

        if event_value == FieldJobEvent.complete:
            _check_completion_gate(db, work_order, event_payload)

        previous_status = work_order.status
        target_status = _EVENT_TO_STATUS[event_value]
        if target_status is not None and target_status != previous_status:
            # A cancel always carries a note (the reason) so a configured
            # requires-note transition rule never blocks the field outcome.
            transition_note = note
            if event_value == FieldJobEvent.unable_to_complete:
                transition_note = note or f"Unable to complete: {event_payload.get('reason')}"
            # The workflow engine owns rules + started_at/completed_at.
            work_order = workflow_service.transition_work_order(
                db,
                str(work_order.id),
                StatusTransitionRequest(to_status=target_status.value, note=transition_note),
            )
            emit_work_order_status_events(db, work_order, previous_status)

        order_event = WorkOrderEvent(
            work_order_id=work_order.id,
            event=event_value,
            actor_person_id=person_uuid,
            latitude=latitude,
            longitude=longitude,
            occurred_at=occurred,
            received_at=now,
            client_event_id=client_uuid,
            payload=event_payload or None,
        )
        db.add(order_event)
        db.add(
            AuditEvent(
                actor_type=AuditActorType.user,
                actor_id=str(person_uuid),
                action=f"field:job:{event_value.value}",
                entity_type="WorkOrder",
                entity_id=str(work_order.id),
                status_code=200,
                is_success=True,
                metadata_={"client_event_id": str(client_uuid)},
            )
        )
        try:
            db.commit()
        except IntegrityError:
            # Concurrent replay raced us on client_event_id.
            db.rollback()
            existing = db.query(WorkOrderEvent).filter(WorkOrderEvent.client_event_id == client_uuid).first()
            if existing:
                # Fresh name: work_order is already narrowed to WorkOrder above, and
                # db.get returns WorkOrder | None — reusing it would be a type error.
                replayed_work_order = db.get(WorkOrder, existing.work_order_id)
                return {"work_order": replayed_work_order, "event": existing, "replayed": True}
            raise
        db.refresh(order_event)

        if event_value in (FieldJobEvent.hold, FieldJobEvent.complete, FieldJobEvent.unable_to_complete):
            # Timers must not run overnight, past completion, or after an aborted
            # visit. Best-effort:
            # the transition is already committed, so a worklog-stop failure
            # must not 500 an otherwise-successful completion.
            from app.services.field.worklogs import stop_open_worklog

            try:
                stop_open_worklog(db, work_order.id, person_uuid, stopped_at=now)
            except Exception:
                logging.getLogger(__name__).exception(
                    "field_worklog_autostop_failed work_order_id=%s event=%s",
                    work_order.id,
                    event_value.value,
                )

        _notify_customer_for_event(db, work_order, event_value)

        return {"work_order": work_order, "event": order_event, "replayed": False}


def _notify_customer_for_event(db: Session, work_order: WorkOrder, event: FieldJobEvent) -> None:
    """Customer-facing notifications for field events.

    Fired only for fresh events (idempotent replays return before reaching
    this), and failures never break the transition — same contract as the
    workforce assignment notifications.
    """
    import logging

    logger = logging.getLogger(__name__)
    try:
        from app.services import eta_notifications

        if event == FieldJobEvent.en_route:
            # Only the FIRST en_route notifies the customer — a tech tapping
            # "on my way" twice (dispatched→dispatched is allowed) must not send
            # two "on the way" messages. This event row is already committed, so
            # exactly one en_route event means this is the first.
            en_route_count = (
                db.query(WorkOrderEvent)
                .filter(WorkOrderEvent.work_order_id == work_order.id)
                .filter(WorkOrderEvent.event == FieldJobEvent.en_route)
                .count()
            )
            if en_route_count <= 1:
                eta_notifications.send_eta_notification(db, str(work_order.id))
        elif event == FieldJobEvent.complete:
            eta_notifications.send_work_order_completed_notification(db, str(work_order.id))
        elif event == FieldJobEvent.unable_to_complete:
            eta_notifications.send_unable_to_complete_notification(db, str(work_order.id))
    except Exception:
        logger.exception(
            "field_transition_customer_notification_failed work_order_id=%s event=%s",
            work_order.id,
            event.value,
        )


def _coerce_occurred_at(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid occurred_at timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


field_transitions = FieldTransitions()

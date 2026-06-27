"""Customer-facing "Track My Visit" — tokenized public read + limited actions.

The work-order ``WorkOrderEvent`` stream is the customer-facing source of truth
(ticket status stays internal). A magic-link token — no login, the unguessable
token *is* the capability, exactly like survey invitations — authorizes a read
of the live status/timeline plus two routed actions: **confirm** the appointment
and **request a reschedule**. Per the project principle, those actions surface to
dispatch; they never auto-mutate the schedule.

The technician's live position is exposed only through a strict privacy gate
(:func:`public_live_position`): on-the-way only, sharing opted-in, fresh fix, and
first name only — never last name, phone, or shift/break status.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.attributes import flag_modified

from app.models.field import FieldJobEvent, WorkOrderEvent
from app.models.field_location import FieldTechPresence, WorkOrderAccessToken
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.person import Person
from app.models.workforce import WorkOrder, WorkOrderNote, WorkOrderStatus
from app.services.field.location import cached_job_location, resolve_job_location

logger = logging.getLogger(__name__)

# Hard cap on a link's life from when it is minted; the page also closes a
# completed visit after a short grace window (below).
_TOKEN_TTL_DAYS = 30
_COMPLETED_GRACE_DAYS = 7
# A live position is only "live" within this window of the last fix.
_LIVE_STALE_SECONDS = 120


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# ── Token manager ────────────────────────────────────────────────────────────


class WorkOrderAccessTokens:
    """Get-or-create one active magic-link token per work order."""

    @staticmethod
    def get_or_create(db: Session, work_order: WorkOrder) -> WorkOrderAccessToken:
        existing = (
            db.query(WorkOrderAccessToken)
            .filter(WorkOrderAccessToken.work_order_id == work_order.id)
            .filter(WorkOrderAccessToken.is_active.is_(True))
            .order_by(WorkOrderAccessToken.created_at.desc())
            .first()
        )
        if existing:
            return existing
        token = WorkOrderAccessToken(
            work_order_id=work_order.id,
            token=secrets.token_urlsafe(32),
            expires_at=_now() + timedelta(days=_TOKEN_TTL_DAYS),
        )
        db.add(token)
        # Persist immediately: the link is handed to the customer right after this
        # call, so the token must be valid even if the surrounding best-effort
        # notification flow later fails.
        db.commit()
        db.refresh(token)
        return token

    @staticmethod
    def get_by_token(db: Session, token: str) -> WorkOrderAccessToken | None:
        return (
            db.query(WorkOrderAccessToken)
            .options(joinedload(WorkOrderAccessToken.work_order))
            .filter(WorkOrderAccessToken.token == token)
            .first()
        )

    @staticmethod
    def mark_accessed(db: Session, token_row: WorkOrderAccessToken) -> None:
        if token_row.accessed_at is None:
            token_row.accessed_at = _now()
            db.commit()


tokens = WorkOrderAccessTokens()


def token_state(token_row: WorkOrderAccessToken | None) -> str:
    """Classify a token for the public route: ok | not_found | expired | closed."""
    if token_row is None or not token_row.is_active:
        return "not_found" if token_row is None else "expired"
    work_order = token_row.work_order
    if work_order is None or not work_order.is_active:
        return "not_found"
    expires_at = _aware(token_row.expires_at)
    if expires_at is not None and expires_at < _now():
        return "expired"
    completed_at = _aware(work_order.completed_at)
    if completed_at is not None and _now() > completed_at + timedelta(days=_COMPLETED_GRACE_DAYS):
        return "closed"
    return "ok"


# ── Read model ───────────────────────────────────────────────────────────────


def _events_by_type(db: Session, work_order_id) -> dict[FieldJobEvent, datetime]:
    """Earliest occurred_at per event type for a work order."""
    rows = (
        db.query(WorkOrderEvent)
        .filter(WorkOrderEvent.work_order_id == work_order_id)
        .order_by(WorkOrderEvent.occurred_at.asc())
        .all()
    )
    seen: dict[FieldJobEvent, datetime] = {}
    for row in rows:
        occurred = _aware(row.occurred_at)  # occurred_at is non-nullable
        if occurred is not None:
            seen.setdefault(row.event, occurred)
    return seen


def build_timeline(
    db: Session, work_order: WorkOrder, *, events: dict[FieldJobEvent, datetime] | None = None
) -> list[dict]:
    """Customer-facing status steps, derived from server-confirmed facts only.

    Each step is {key, label, state, at} where state is
    done | current | upcoming | failed. Pass ``events`` to reuse one query.
    """
    if events is None:
        events = _events_by_type(db, work_order.id)
    status = work_order.status
    canceled = status == WorkOrderStatus.canceled or FieldJobEvent.unable_to_complete in events
    completed = status == WorkOrderStatus.completed or FieldJobEvent.complete in events
    en_route_at = events.get(FieldJobEvent.en_route)
    started_at = events.get(FieldJobEvent.start) or _aware(work_order.started_at)

    def step(key: str, label: str, *, done: bool, at: datetime | None, current: bool = False) -> dict:
        state = "done" if done else ("current" if current else "upcoming")
        return {"key": key, "label": label, "state": state, "at": at}

    timeline = [
        step(
            "booked",
            "Appointment booked",
            done=True,
            at=_aware(work_order.scheduled_start) or _aware(work_order.created_at),
        ),
        step(
            "assigned",
            "Technician assigned",
            done=work_order.assigned_to_person_id is not None,
            at=None,
            current=work_order.assigned_to_person_id is not None and status == WorkOrderStatus.scheduled,
        ),
        step(
            "en_route",
            "On the way",
            done=en_route_at is not None,
            at=en_route_at,
            current=status == WorkOrderStatus.dispatched,
        ),
        step(
            "started",
            "Technician arrived",
            done=started_at is not None,
            at=started_at,
            current=status == WorkOrderStatus.in_progress,
        ),
    ]
    if canceled:
        timeline.append(
            {
                "key": "missed",
                "label": "Visit could not be completed — we'll reschedule",
                "state": "failed",
                "at": events.get(FieldJobEvent.unable_to_complete),
            }
        )
    else:
        timeline.append(
            step(
                "completed",
                "Visit completed",
                done=completed,
                at=events.get(FieldJobEvent.complete) or _aware(work_order.completed_at),
            )
        )
    return timeline


def public_live_position(
    db: Session, work_order: WorkOrder, *, events: dict[FieldJobEvent, datetime] | None = None
) -> dict | None:
    """The privacy gate for the moving-technician pin.

    Returns the technician's coords ONLY when they are genuinely on the way:
    work order is ``dispatched``, an ``en_route`` event exists, and the assigned
    technician has location-sharing enabled with a fresh fix. Exposes the
    technician's first name only — never last name, phone, or shift status.
    Returns ``None`` (page shows destination + status only) otherwise.
    """
    if work_order.assigned_to_person_id is None:
        return None
    if work_order.status != WorkOrderStatus.dispatched:
        return None
    if events is None:
        events = _events_by_type(db, work_order.id)
    if FieldJobEvent.en_route not in events:
        return None

    presence = (
        db.query(FieldTechPresence).filter(FieldTechPresence.person_id == work_order.assigned_to_person_id).first()
    )
    if not presence or not presence.location_sharing_enabled:
        return None
    last_at = _aware(presence.last_location_at)
    if last_at is None or last_at < _now() - timedelta(seconds=_LIVE_STALE_SECONDS):
        return None
    if presence.last_latitude is None or presence.last_longitude is None:
        return None

    person = db.get(Person, work_order.assigned_to_person_id)
    first_name = None
    if person:
        first_name = person.first_name or (person.display_name or "").split(" ")[0] or None
    return {
        "latitude": float(presence.last_latitude),
        "longitude": float(presence.last_longitude),
        "accuracy_m": float(presence.last_location_accuracy_m)
        if presence.last_location_accuracy_m is not None
        else None,
        "updated_at": last_at.isoformat(),
        "name": first_name or "Your technician",
    }


def _technician_first_name(db: Session, work_order: WorkOrder) -> str | None:
    if not work_order.assigned_to_person_id:
        return None
    person = db.get(Person, work_order.assigned_to_person_id)
    if not person:
        return None
    return person.first_name or (person.display_name or "").split(" ")[0] or None


def public_state(db: Session, work_order: WorkOrder, *, geocode: bool = True) -> dict:
    """Assemble the full customer-facing state for the page + the /live poll.

    ``geocode`` is True only on the initial page load — the destination is
    static, so the high-frequency, unauthenticated ``/live`` poll passes
    ``geocode=False`` to read cached coordinates and never call the geocoder.
    """
    location = resolve_job_location(db, work_order) if geocode else (cached_job_location(work_order) or {})
    eta = _aware(work_order.estimated_arrival_at) or _aware(work_order.scheduled_start)
    meta = work_order.metadata_ or {}
    events = _events_by_type(db, work_order.id)  # one query, shared below
    return {
        "status": work_order.status.value if work_order.status else None,
        "eta": eta.isoformat() if eta else None,
        "timeline": [
            {**step, "at": step["at"].isoformat() if step["at"] else None}
            for step in build_timeline(db, work_order, events=events)
        ],
        "technician": {"name": _technician_first_name(db, work_order)},
        "destination": {
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "address_text": location.get("address_text"),
        },
        "tech_position": public_live_position(db, work_order, events=events),
        "customer_confirmed": bool(meta.get("customer_confirmed_at")),
        "reschedule_pending": _open_reschedule_request(work_order) is not None,
    }


# ── Routed customer actions ──────────────────────────────────────────────────


def _open_reschedule_request(work_order: WorkOrder) -> dict | None:
    meta = work_order.metadata_ or {}
    request = meta.get("reschedule_request")
    if isinstance(request, dict) and request.get("status") == "open":
        return request
    return None


def _notify_dispatch(db: Session, work_order: WorkOrder, subject: str, body: str) -> None:
    """Best-effort in-app notice to the assigned technician + ticket manager."""
    recipients: set[str] = set()
    for person_id in (
        work_order.assigned_to_person_id,
        work_order.ticket.ticket_manager_person_id if work_order.ticket else None,
    ):
        if not person_id:
            continue
        person = db.get(Person, person_id)
        if person and isinstance(person.email, str) and person.email.strip():
            recipients.add(person.email.strip())
    for recipient in recipients:
        db.add(
            Notification(
                channel=NotificationChannel.push,
                recipient=recipient,
                subject=subject,
                body=body,
                status=NotificationStatus.delivered,
                sent_at=_now(),
            )
        )


def confirm_appointment(db: Session, token_row: WorkOrderAccessToken) -> dict:
    """Customer confirms they'll be present. Idempotent; routes a notice to dispatch."""
    work_order = token_row.work_order
    meta = dict(work_order.metadata_ or {})
    if meta.get("customer_confirmed_at"):
        return {"confirmed": True, "already": True}
    meta["customer_confirmed_at"] = _now().isoformat()
    work_order.metadata_ = meta
    flag_modified(work_order, "metadata_")
    db.add(
        WorkOrderNote(
            work_order_id=work_order.id,
            body="Customer confirmed the appointment via Track My Visit.",
            is_internal=True,
        )
    )
    _notify_dispatch(
        db,
        work_order,
        subject="Appointment confirmed by customer",
        body=f"The customer confirmed work order {work_order.title}.",
    )
    db.commit()
    return {"confirmed": True, "already": False}


def request_reschedule(
    db: Session,
    token_row: WorkOrderAccessToken,
    *,
    note: str | None = None,
    preferred_window: str | None = None,
) -> dict:
    """Customer asks to reschedule. Records a *request* for dispatch to action —
    never mutates ``scheduled_start`` from this unauthenticated surface.
    """
    work_order = token_row.work_order
    if _open_reschedule_request(work_order) is not None:
        raise HTTPException(status_code=409, detail="A reschedule request is already pending.")

    clean_note = (note or "").strip()[:500] or None
    clean_window = (preferred_window or "").strip()[:120] or None
    meta = dict(work_order.metadata_ or {})
    meta["reschedule_request"] = {
        "status": "open",
        "requested_at": _now().isoformat(),
        "note": clean_note,
        "preferred_window": clean_window,
    }
    work_order.metadata_ = meta
    flag_modified(work_order, "metadata_")
    detail = clean_window or clean_note or "no preference given"
    db.add(
        WorkOrderNote(
            work_order_id=work_order.id,
            body=f"Customer requested a reschedule via Track My Visit ({detail}).",
            is_internal=True,
        )
    )
    _notify_dispatch(
        db,
        work_order,
        subject="Reschedule requested by customer",
        body=f"The customer requested a reschedule for work order {work_order.title}: {detail}.",
    )
    db.commit()
    return {"requested": True}

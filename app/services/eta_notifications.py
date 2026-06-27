"""ETA notification service for field service work orders.

Sends SMS and email notifications to customers when technicians are dispatched
or when ETA is updated.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.workforce import WorkOrder
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


def _person_to_contact(person) -> dict | None:
    """Convert a Person model to a contact dict if it has at least email or phone."""
    if not person:
        return None
    contact = {
        "name": person.display_name or f"{person.first_name} {person.last_name}".strip(),
        "email": person.email,
        "phone": person.phone,
    }
    if contact.get("email") or contact.get("phone"):
        return contact
    return None


def _resolve_customer_contact(db: Session, work_order: WorkOrder) -> dict | None:
    """Resolve customer contact information from work order.

    Tries multiple paths: subscriber -> person, then ticket -> customer person.
    Returns dict with name, email, phone if found, otherwise None.
    """
    # Path 1: work_order -> subscriber -> person
    if work_order.subscriber and work_order.subscriber.person:
        contact = _person_to_contact(work_order.subscriber.person)
        if contact:
            return contact

    # Path 2: work_order -> ticket -> customer_person
    if work_order.ticket and work_order.ticket.customer_person_id:
        from app.models.person import Person

        customer = db.get(Person, work_order.ticket.customer_person_id)
        contact = _person_to_contact(customer)
        if contact:
            return contact

    # Path 3: work_order -> ticket -> subscriber -> person
    if work_order.ticket and work_order.ticket.subscriber_id:
        from app.models.subscriber import Subscriber

        subscriber = db.get(Subscriber, work_order.ticket.subscriber_id)
        if subscriber and subscriber.person:
            contact = _person_to_contact(subscriber.person)
            if contact:
                return contact

    return None


def _track_url(db: Session, work_order: WorkOrder) -> str | None:
    """Mint (or reuse) the customer's "Track My Visit" magic-link for this job.

    Best-effort: a token/link failure must never block the underlying message.
    """
    try:
        from app.services.email import get_app_url
        from app.services.field.tracking import tokens

        token_row = tokens.get_or_create(db, work_order)
        base = (get_app_url(db) or "").rstrip("/")
        return f"{base}/track/{token_row.token}"
    except Exception:
        logger.exception("track_url_failed work_order_id=%s", work_order.id)
        db.rollback()  # leave the session usable so the message can still send
        return None


def _track_line(track_url: str | None) -> str:
    return f"\n\nTrack your visit live: {track_url}" if track_url else ""


def _completion_summary(work_order: WorkOrder) -> str:
    """Latest customer-safe note body for the completion email.

    ``work_order.notes`` is a relationship (list of WorkOrderNote); rendering it
    directly printed a Python list repr. Internal dispatch notes (e.g. the
    customer's own reschedule/confirm notes) must never appear in a customer
    email, so they are excluded here.
    """
    customer_notes = sorted(
        (n for n in (work_order.notes or []) if not n.is_internal and isinstance(n.body, str) and n.body.strip()),
        key=lambda n: n.created_at,
        reverse=True,
    )
    return customer_notes[0].body.strip() if customer_notes else "Work completed successfully."


def send_eta_notification(db: Session, work_order_id: str) -> bool:
    """Send ETA notification to customer for a work order.

    Args:
        db: Database session
        work_order_id: The work order UUID

    Returns:
        True if notification was sent successfully
    """
    from app.services import email as email_service
    from app.services import sms as sms_service

    work_order = db.get(WorkOrder, coerce_uuid(work_order_id))
    if not work_order:
        logger.error(f"Work order not found: {work_order_id}")
        return False

    # Get estimated arrival time
    eta = work_order.estimated_arrival_at
    if not eta:
        # Try to calculate from scheduled start
        if work_order.scheduled_start:
            eta = work_order.scheduled_start
        else:
            logger.warning(f"No ETA available for work order {work_order_id}")
            return False

    # Get customer contact
    contact = _resolve_customer_contact(db, work_order)
    if not contact:
        logger.warning(f"No customer contact found for work order {work_order_id}")
        return False

    # Get technician name
    technician_name = "Our technician"
    if work_order.assigned_to_person_id:
        from app.models.person import Person

        technician = db.get(Person, work_order.assigned_to_person_id)
        if technician:
            technician_name = technician.display_name or technician.first_name or "Our technician"

    # Format ETA time
    eta_time = eta.strftime("%H:%M") if eta else "soon"

    track = _track_url(db, work_order)
    context = {
        "customer_name": contact.get("name", "Valued Customer"),
        "technician_name": technician_name,
        "eta_time": eta_time,
        "work_order_title": work_order.title or "Service Visit",
        "track_url": track or "",
    }

    sent = False

    # Send SMS if phone available
    if contact.get("phone"):
        try:
            result = sms_service.send_with_template(
                db=db,
                template_code="technician_eta",
                to_phone=contact["phone"],
                context=context,
            )
            if result:
                logger.info(f"ETA SMS sent to {contact['phone']} for work order {work_order_id}")
                sent = True
        except Exception as exc:
            logger.error(f"Failed to send ETA SMS: {exc}")

    # Send email if available
    if contact.get("email"):
        try:
            from app.models.notification import NotificationChannel, NotificationTemplate

            template = (
                db.query(NotificationTemplate)
                .filter(NotificationTemplate.code == "technician_assigned")
                .filter(NotificationTemplate.channel == NotificationChannel.email)
                .filter(NotificationTemplate.is_active.is_(True))
                .first()
            )

            subject = f"Your technician {technician_name} is on the way!"
            body = f"""Dear {context["customer_name"]},

Your technician {technician_name} is on the way and will arrive at approximately {eta_time}.

Service: {context["work_order_title"]}

Please ensure someone is available at the service location.{_track_line(track)}

Thank you for your patience!"""

            if template:
                subject = template.subject or subject
                body = template.body
                for key, value in context.items():
                    body = body.replace(f"{{{{{key}}}}}", str(value))
                    body = body.replace(f"{{{{ {key} }}}}", str(value))

            email_service.send_email(
                db=db,
                to_email=contact["email"],
                subject=subject,
                body_html=body,
                body_text=body,
            )
            logger.info(f"ETA email sent to {contact['email']} for work order {work_order_id}")
            sent = True
        except Exception as exc:
            logger.error(f"Failed to send ETA email: {exc}")

    return sent


def send_technician_assigned_notification(db: Session, work_order_id: str) -> bool:
    """Send notification when technician is assigned to work order.

    Args:
        db: Database session
        work_order_id: The work order UUID

    Returns:
        True if notification was sent successfully
    """
    from app.services import email as email_service
    from app.services import sms as sms_service

    work_order = db.get(WorkOrder, coerce_uuid(work_order_id))
    if not work_order:
        logger.error(f"Work order not found: {work_order_id}")
        return False

    # Get customer contact
    contact = _resolve_customer_contact(db, work_order)
    if not contact:
        logger.warning(f"No customer contact found for work order {work_order_id}")
        return False

    # Get technician name
    technician_name = "A technician"
    if work_order.assigned_to_person_id:
        from app.models.person import Person

        technician = db.get(Person, work_order.assigned_to_person_id)
        if technician:
            technician_name = technician.display_name or technician.first_name or "A technician"

    # Format scheduled time
    scheduled_date = "To be confirmed"
    scheduled_time = ""
    if work_order.scheduled_start:
        scheduled_date = work_order.scheduled_start.strftime("%B %d, %Y")
        scheduled_time = work_order.scheduled_start.strftime("%H:%M")

    track = _track_url(db, work_order)
    context = {
        "customer_name": contact.get("name", "Valued Customer"),
        "technician_name": technician_name,
        "scheduled_date": scheduled_date,
        "scheduled_time": scheduled_time,
        "work_order_title": work_order.title or "Service Visit",
        "track_url": track or "",
    }

    sent = False

    # Send SMS
    if contact.get("phone"):
        try:
            result = sms_service.send_with_template(
                db=db,
                template_code="technician_assigned_sms",
                to_phone=contact["phone"],
                context=context,
            )
            if result:
                sent = True
        except Exception as exc:
            logger.error(f"Failed to send technician assigned SMS: {exc}")

    # Send email
    if contact.get("email"):
        try:
            from app.models.notification import NotificationChannel, NotificationTemplate

            template = (
                db.query(NotificationTemplate)
                .filter(NotificationTemplate.code == "technician_assigned")
                .filter(NotificationTemplate.channel == NotificationChannel.email)
                .filter(NotificationTemplate.is_active.is_(True))
                .first()
            )

            subject = "Your Technician Has Been Assigned"
            body = f"""Dear {context["customer_name"]},

A technician has been assigned to your service request.

Technician: {technician_name}
Date: {scheduled_date}
Time: {scheduled_time}

Service: {context["work_order_title"]}

You'll receive an update when the technician is on their way.{_track_line(track)}

Thank you for your patience!"""

            if template:
                subject = template.subject or subject
                body = template.body
                for key, value in context.items():
                    body = body.replace(f"{{{{{key}}}}}", str(value))
                    body = body.replace(f"{{{{ {key} }}}}", str(value))

            email_service.send_email(
                db=db,
                to_email=contact["email"],
                subject=subject,
                body_html=body,
                body_text=body,
            )
            sent = True
        except Exception as exc:
            logger.error(f"Failed to send technician assigned email: {exc}")

    return sent


def send_work_order_completed_notification(db: Session, work_order_id: str) -> bool:
    """Send notification when work order is completed.

    Args:
        db: Database session
        work_order_id: The work order UUID

    Returns:
        True if notification was sent successfully
    """
    from app.services import email as email_service

    work_order = db.get(WorkOrder, coerce_uuid(work_order_id))
    if not work_order:
        logger.error(f"Work order not found: {work_order_id}")
        return False

    # Get customer contact
    contact = _resolve_customer_contact(db, work_order)
    if not contact or not contact.get("email"):
        logger.warning(f"No customer email found for work order {work_order_id}")
        return False

    # Get technician name
    technician_name = "Our technician"
    if work_order.assigned_to_person_id:
        from app.models.person import Person

        technician = db.get(Person, work_order.assigned_to_person_id)
        if technician:
            technician_name = technician.display_name or technician.first_name or "Our technician"

    track = _track_url(db, work_order)
    context = {
        "customer_name": contact.get("name", "Valued Customer"),
        "work_order_number": str(work_order.id)[:8].upper(),
        "work_order_title": work_order.title or "Service Visit",
        "technician_name": technician_name,
        "completed_at": datetime.now(UTC).strftime("%B %d, %Y at %H:%M"),
        "completion_notes": _completion_summary(work_order),
        "track_url": track or "",
    }

    try:
        from app.models.notification import NotificationChannel, NotificationTemplate

        template = (
            db.query(NotificationTemplate)
            .filter(NotificationTemplate.code == "work_order_completed")
            .filter(NotificationTemplate.channel == NotificationChannel.email)
            .filter(NotificationTemplate.is_active.is_(True))
            .first()
        )

        subject = f"Service Completed - Work Order #{context['work_order_number']}"
        body = f"""Dear {context["customer_name"]},

Your service has been completed.

Work Order: #{context["work_order_number"]}
Service: {context["work_order_title"]}
Completed By: {technician_name}
Completed At: {context["completed_at"]}

Summary:
{context["completion_notes"]}{_track_line(track)}

If you have any questions or concerns about the work performed, please contact us.

Thank you for choosing us!"""

        if template:
            subject = template.subject or subject
            body = template.body
            for key, value in context.items():
                body = body.replace(f"{{{{{key}}}}}", str(value))
                body = body.replace(f"{{{{ {key} }}}}", str(value))

        email_service.send_email(
            db=db,
            to_email=contact["email"],
            subject=subject,
            body_html=body,
            body_text=body,
        )
        logger.info(f"Work order completed email sent for {work_order_id}")
        return True
    except Exception as exc:
        logger.error(f"Failed to send work order completed email: {exc}")
        return False


def send_unable_to_complete_notification(db: Session, work_order_id: str) -> bool:
    """Tell the customer a visit could not be completed and invite a reschedule.

    Pairs with the Track My Visit reschedule action: the message carries the
    tracking link so the customer can request a new time in one tap.
    """
    from app.services import email as email_service
    from app.services import sms as sms_service

    work_order = db.get(WorkOrder, coerce_uuid(work_order_id))
    if not work_order:
        logger.error(f"Work order not found: {work_order_id}")
        return False

    contact = _resolve_customer_contact(db, work_order)
    if not contact:
        logger.warning(f"No customer contact found for work order {work_order_id}")
        return False

    track = _track_url(db, work_order)
    customer_name = contact.get("name", "Valued Customer")
    service = work_order.title or "your service visit"
    sent = False

    if contact.get("phone"):
        try:
            sms_body = (
                f"Hi {customer_name}, we're sorry we couldn't complete {service} today. "
                f"We'll be in touch to reschedule.{_track_line(track)}"
            )
            if sms_service.send_sms(db=db, to_phone=contact["phone"], body=sms_body):
                sent = True
        except Exception as exc:
            logger.error(f"Failed to send unable-to-complete SMS: {exc}")

    if contact.get("email"):
        try:
            subject = "We missed you — let's reschedule your visit"
            body = (
                f"Dear {customer_name},\n\n"
                f"Unfortunately we were unable to complete {service} today. "
                f"We'd like to find a new time that works for you.{_track_line(track)}\n\n"
                "You can request a preferred time from the tracking page above, or reply to this message.\n\n"
                "Thank you for your patience."
            )
            email_service.send_email(
                db=db,
                to_email=contact["email"],
                subject=subject,
                body_html=body,
                body_text=body,
            )
            sent = True
        except Exception as exc:
            logger.error(f"Failed to send unable-to-complete email: {exc}")

    return sent

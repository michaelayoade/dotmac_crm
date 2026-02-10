"""Notification handler for the event system.

Queues customer notifications based on configured notification templates.
"""

import logging
import re

from sqlalchemy.orm import Session

from app.models.crm.sales import Lead
from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationTemplate,
)
from app.models.person import Person
from app.models.projects import Project
from app.models.tickets import Ticket
from app.services.events.types import Event, EventType

logger = logging.getLogger(__name__)


# Mapping from EventType to notification template codes
# These codes are used to look up templates in the notification_templates table
EVENT_TYPE_TO_TEMPLATE = {
    # Subscription events
    EventType.subscription_created: "subscription_created",
    EventType.subscription_activated: "subscription_activated",
    EventType.subscription_suspended: "subscription_suspended",
    EventType.subscription_canceled: "subscription_canceled",
    EventType.subscription_expiring: "subscription_expiring",
    # Billing events
    EventType.invoice_created: "invoice_created",
    EventType.invoice_sent: "invoice_sent",
    EventType.invoice_overdue: "invoice_overdue",
    EventType.payment_received: "payment_received",
    EventType.payment_failed: "payment_failed",
    # Usage events
    EventType.usage_warning: "usage_warning",
    EventType.usage_exhausted: "usage_exhausted",
    # Provisioning events
    EventType.provisioning_completed: "provisioning_completed",
    EventType.provisioning_failed: "provisioning_failed",
    # Ticket events
    EventType.ticket_created: "ticket_created",
    EventType.ticket_resolved: ["ticket_resolved", "ticket_completed_technician"],
    EventType.ticket_assigned: "ticket_assigned_technician",
    # Project events
    EventType.project_created: "project_created",
    EventType.project_completed: "project_completed",
}

TECHNICIAN_TEMPLATE_CODES = {
    "ticket_assigned_technician",
    "ticket_completed_technician",
}


class NotificationHandler:
    """Handler that queues customer notifications."""

    def handle(self, db: Session, event: Event) -> None:
        """Process an event by creating notifications.

        Looks up the notification template for the event type. If found
        and active, creates a Notification record for each configured channel.

        Args:
            db: Database session
            event: The event to process
        """
        # Get template code for this event type
        template_code = EVENT_TYPE_TO_TEMPLATE.get(event.event_type)
        if template_code is None:
            return

        template_codes = (
            list(template_code)
            if isinstance(template_code, list | tuple | set)
            else [template_code]
        )

        for code in template_codes:
            # Look up template
            template = (
                db.query(NotificationTemplate)
                .filter(NotificationTemplate.code == code)
                .filter(NotificationTemplate.is_active.is_(True))
                .first()
            )

            if not template:
                logger.debug(
                    f"No active notification template for code {code}"
                )
                continue

            # Get recipient from event context
            recipient = self._resolve_recipient_for_template(db, event, code)
            if not recipient:
                logger.debug(
                    f"Cannot determine recipient for event {event.event_type.value}"
                )
                continue

            # Create notification
            # Include event context in the body for traceability
            payload = self._build_template_payload(event, code)
            body = self._render_body(template, payload, event)

            notification = Notification(
                template_id=template.id,
                channel=template.channel or NotificationChannel.email,
                recipient=recipient,
                subject=self._render_subject(template, payload, event),
                body=body,
                status=NotificationStatus.queued,
            )
            db.add(notification)

            logger.info(
                f"Queued notification for event {event.event_type.value} "
                f"to {recipient}"
            )

    def _resolve_recipient_for_template(
        self, db: Session, event: Event, template_code: str
    ) -> str | None:
        """Resolve the notification recipient from event context."""
        if template_code in TECHNICIAN_TEMPLATE_CODES:
            email = event.payload.get("technician_email")
            if isinstance(email, str) and email.strip():
                return email.strip()

            ticket_id = event.ticket_id or event.payload.get("ticket_id")
            if ticket_id:
                ticket = db.get(Ticket, ticket_id)
                if ticket and ticket.assigned_to_person_id:
                    technician = db.get(Person, ticket.assigned_to_person_id)
                    if technician and isinstance(technician.email, str) and technician.email.strip():
                        return technician.email.strip()
            return None

        # Check if email is in payload
        if "email" in event.payload:
            return event.payload["email"]

        ticket_id = event.ticket_id or event.payload.get("ticket_id")
        if ticket_id:
            ticket = db.get(Ticket, ticket_id)
            if ticket and ticket.customer_person_id:
                customer = db.get(Person, ticket.customer_person_id)
                if customer and customer.email:
                    email = customer.email
                    if isinstance(email, str) and email.strip():
                        return email.strip()
            if ticket and ticket.subscriber and ticket.subscriber.person:
                email = ticket.subscriber.person.email
                if isinstance(email, str) and email.strip():
                    return email.strip()
            if ticket and ticket.lead_id:
                lead = db.get(Lead, ticket.lead_id)
                if lead and lead.person and lead.person.email:
                    email = lead.person.email
                    if isinstance(email, str) and email.strip():
                        return email.strip()

        project_id = event.project_id or event.payload.get("project_id")
        if project_id:
            project = db.get(Project, project_id)
            if project and project.subscriber and project.subscriber.person:
                email = project.subscriber.person.email
                if isinstance(email, str) and email.strip():
                    return email.strip()
            if project and project.lead_id:
                lead = db.get(Lead, project.lead_id)
                if lead and lead.person and lead.person.email:
                    email = lead.person.email
                    if isinstance(email, str) and email.strip():
                        return email.strip()

        return None

    def _build_template_payload(self, event: Event, template_code: str) -> dict:
        payload = dict(event.payload)
        if template_code in TECHNICIAN_TEMPLATE_CODES:
            technician_doc = payload.get("technician_doc")
            if isinstance(technician_doc, dict):
                payload["doc"] = technician_doc
        return payload

    def _render_subject(self, template: NotificationTemplate, payload: dict, event: Event) -> str:
        """Render the notification subject with event data."""
        if not template.subject:
            return f"Notification: {event.event_type.value}"

        return self._render_template_string(template.subject, payload)

    def _render_body(self, template: NotificationTemplate, payload: dict, event: Event) -> str:
        """Render the notification body with event data."""
        if not template.body:
            return f"Event: {event.event_type.value}\n{event.payload}"

        return self._render_template_string(template.body, payload)

    def _render_template_string(self, template: str, payload: dict) -> str:
        def _resolve_token(token: str) -> str | None:
            parts = token.split(".")
            current: object = payload
            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return None
            if current is None:
                return ""
            return str(current)

        def _replace_jinja(match):
            token = match.group(1).strip()
            resolved = _resolve_token(token)
            return resolved if resolved is not None else match.group(0)

        def _replace_single_brace(match):
            token = match.group(1).strip()
            resolved = _resolve_token(token)
            return resolved if resolved is not None else match.group(0)

        # Replace {{ token }} first
        result = re.sub(r"{{\s*([a-zA-Z0-9_.-]+)\s*}}", _replace_jinja, template)
        # Replace {token} without double braces
        result = re.sub(r"(?<!{){\s*([a-zA-Z0-9_.-]+)\s*}(?!})", _replace_single_brace, result)
        return result

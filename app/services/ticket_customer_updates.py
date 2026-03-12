from __future__ import annotations

import base64
import html
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.dispatch import TechnicianProfile
from app.models.person import Person
from app.models.tickets import Ticket, TicketComment, TicketStatus
from app.services.ai.client import AIClientError
from app.services.ai.gateway import ai_gateway
from app.services.branding import get_branding
from app.services.email import send_email
from app.services.storage import storage

logger = logging.getLogger(__name__)


def _status_label(status: TicketStatus | None) -> str:
    if not status:
        return "updated"
    return status.value.replace("_", " ").title()


def _resolve_customer(ticket: Ticket, db: Session) -> Person | None:
    if ticket.customer_person_id:
        person = db.get(Person, ticket.customer_person_id)
        if person:
            return person
    subscriber = getattr(ticket, "subscriber", None)
    if subscriber and getattr(subscriber, "person", None):
        return subscriber.person
    lead = getattr(ticket, "lead", None)
    if lead and getattr(lead, "person", None):
        return lead.person
    return None


def _customer_name(customer: Person | None) -> str:
    if not customer:
        return "Subscriber"
    return customer.display_name or f"{customer.first_name or ''} {customer.last_name or ''}".strip() or "Subscriber"


def _customer_email(customer: Person | None) -> str | None:
    email = getattr(customer, "email", None)
    if isinstance(email, str) and email.strip():
        return email.strip()
    return None


def _is_active_technician(db: Session, person_id: UUID | None) -> bool:
    if not person_id:
        return False
    return (
        db.query(TechnicianProfile.id)
        .filter(TechnicianProfile.person_id == person_id)
        .filter(TechnicianProfile.is_active.is_(True))
        .first()
        is not None
    )


def _html_from_text(db: Session, *, body: str, subject: str, ticket: Ticket) -> str:
    branding = get_branding(db)
    company = html.escape(str(branding.get("company_name") or "Dotmac"))
    logo_url = str(branding.get("logo_url") or "").strip()
    support_email = str(branding.get("support_email") or "").strip()
    parts = [segment.strip() for segment in body.split("\n\n") if segment.strip()]
    content_parts = ["<p>We have an update on your support ticket.</p>"]
    if parts:
        content_parts = []
        for part in parts:
            content_parts.append(f"<p>{html.escape(part).replace(chr(10), '<br>')}</p>")
    logo_html = ""
    if logo_url:
        safe_logo_url = html.escape(logo_url, quote=True)
        logo_html = (
            f'<div style="margin-bottom: 20px;">'
            f'<img src="{safe_logo_url}" alt="{company}" style="max-height: 56px; max-width: 220px;">'
            f"</div>"
        )
    support_html = ""
    if support_email:
        safe_support_email = html.escape(support_email)
        support_html = (
            f'<p style="margin: 0;">Need help? Contact us at '
            f'<a href="mailto:{safe_support_email}" style="color: #d97706; text-decoration: none;">'
            f"{safe_support_email}</a>.</p>"
        )
    safe_subject = html.escape(subject)
    safe_ticket_ref = html.escape(str(ticket.number or ticket.id))
    joined_content = "".join(content_parts)
    return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_subject}</title>
</head>
<body style="margin: 0; padding: 0; background: #f8fafc; font-family: Arial, sans-serif; color: #0f172a;">
    <div style="max-width: 640px; margin: 0 auto; padding: 24px 16px;">
        <div style="border-radius: 20px; overflow: hidden; box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);">
            <div style="background: linear-gradient(135deg, #f59e0b, #ea580c); padding: 28px 32px; color: #ffffff;">
                {logo_html}
                <div style="font-size: 13px; letter-spacing: 0.08em; text-transform: uppercase; opacity: 0.88;">Support Ticket Update</div>
                <h1 style="margin: 10px 0 0; font-size: 24px; line-height: 1.25;">{safe_subject}</h1>
            </div>
            <div style="background: #ffffff; padding: 32px;">
                {joined_content}
                <div style="margin-top: 28px; padding-top: 20px; border-top: 1px solid #e2e8f0; font-size: 13px; color: #475569;">
                    <p style="margin: 0 0 8px;">Ticket reference: <strong>{safe_ticket_ref}</strong></p>
                    {support_html}
                    <p style="margin: 12px 0 0;">Thank you,<br>{company} Support</p>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""


def _attachment_payloads(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        try:
            content = storage.get(key)
        except Exception:
            logger.warning("ticket_customer_update_attachment_missing key=%s", key, exc_info=True)
            continue
        payloads.append(
            {
                "file_name": str(item.get("file_name") or "attachment"),
                "mime_type": str(item.get("mime_type") or "application/octet-stream"),
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
        )
    return payloads


def _fallback_status_message(ticket: Ticket, *, customer_name: str, previous: TicketStatus | None) -> str:
    current = _status_label(ticket.status)
    prior = _status_label(previous) if previous else None
    lines = [f"Dear {customer_name},"]
    if prior:
        lines.append(f"Your support ticket \"{ticket.title}\" status has changed from {prior} to {current}.")
    else:
        lines.append(f"There is a new update on your support ticket \"{ticket.title}\".")
    lines.append("Our team is actively working on the issue and we will continue to share updates as they come.")
    return "\n\n".join(lines)


def _fallback_comment_message(
    ticket: Ticket,
    *,
    customer_name: str,
    author_name: str,
    comment_body: str,
) -> str:
    update_text = (comment_body or "").strip() or "Our technical team shared a progress update on your ticket."
    return (
        f"Dear {customer_name},\n\n"
        f"Our technical team has shared an update on your support ticket \"{ticket.title}\".\n\n"
        f"Update from {author_name}: {update_text}\n\n"
        "We are working to resolve the issue as quickly as possible and will keep you informed."
    )


def _refine_message_with_ai(
    db: Session,
    *,
    ticket: Ticket,
    customer_name: str,
    event_kind: str,
    raw_message: str,
    fallback_message: str,
) -> str:
    system = (
        "You rewrite internal ISP support ticket updates into polished customer-facing email copy. "
        "Respond with plain text only. Keep the message professional, empathetic, and concise. "
        "Do not use markdown. Do not invent facts. Preserve the real meaning of the update. "
        "Avoid internal jargon, slang, blame, or abbreviations. Write for a customer."
    )
    prompt = (
        f"Customer name: {customer_name}\n"
        f"Ticket number: {ticket.number or ticket.id}\n"
        f"Ticket title: {ticket.title}\n"
        f"Event kind: {event_kind}\n"
        f"Internal update to rewrite:\n{raw_message}\n\n"
        "Write a customer-facing message with a greeting and a clear reassurance that further updates will follow when appropriate."
    )
    try:
        result, _meta = ai_gateway.generate_with_fallback(
            db,
            system=system,
            prompt=prompt,
            max_tokens=350,
        )
    except AIClientError:
        logger.info("ticket_customer_update_ai_unavailable ticket_id=%s event=%s", ticket.id, event_kind)
        return fallback_message

    content = (result.content or "").strip()
    if not content:
        return fallback_message
    return content[:5000]


def send_status_change_update(
    db: Session,
    *,
    ticket: Ticket,
    previous_status: TicketStatus | None,
) -> bool:
    customer = _resolve_customer(ticket, db)
    recipient = _customer_email(customer)
    if not recipient:
        return False

    customer_name = _customer_name(customer)
    fallback_message = _fallback_status_message(ticket, customer_name=customer_name, previous=previous_status)
    raw_message = (
        f"Ticket status changed from {_status_label(previous_status)} to {_status_label(ticket.status)} "
        f"for ticket '{ticket.title}'."
    )
    refined = _refine_message_with_ai(
        db,
        ticket=ticket,
        customer_name=customer_name,
        event_kind="status_change",
        raw_message=raw_message,
        fallback_message=fallback_message,
    )
    subject = f"Update on your support ticket {ticket.number or ticket.id}"
    send_email(
        db,
        recipient,
        subject,
        _html_from_text(db, body=refined, subject=subject, ticket=ticket),
        refined,
    )
    return True


def send_comment_update(
    db: Session,
    *,
    ticket: Ticket,
    comment: TicketComment,
) -> bool:
    if comment.is_internal or not comment.author_person_id:
        return False
    if not _is_active_technician(db, comment.author_person_id):
        return False

    customer = _resolve_customer(ticket, db)
    recipient = _customer_email(customer)
    if not recipient:
        return False

    author = db.get(Person, comment.author_person_id)
    author_name = _customer_name(author) if author else "our technical team"
    customer_name = _customer_name(customer)
    fallback_message = _fallback_comment_message(
        ticket,
        customer_name=customer_name,
        author_name=author_name,
        comment_body=comment.body,
    )
    refined = _refine_message_with_ai(
        db,
        ticket=ticket,
        customer_name=customer_name,
        event_kind="technician_comment",
        raw_message=comment.body,
        fallback_message=fallback_message,
    )
    subject = f"New update on your support ticket {ticket.number or ticket.id}"
    send_email(
        db,
        recipient,
        subject,
        _html_from_text(db, body=refined, subject=subject, ticket=ticket),
        refined,
        attachments=_attachment_payloads(comment.attachments if isinstance(comment.attachments, list) else None),
    )
    return True

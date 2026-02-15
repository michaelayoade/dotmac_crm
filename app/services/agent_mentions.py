"""Generic @mention notifications for CRM agents.

Used across admin surfaces (inbox, tickets, projects, tasks).
"""

from __future__ import annotations

from html import escape as html_escape

from sqlalchemy.orm import Session

from app.models.crm.team import CrmAgent
from app.models.notification import NotificationChannel
from app.models.person import Person
from app.schemas.notification import NotificationCreate
from app.services import email as email_service
from app.services import notification as notification_service
from app.services.common import coerce_uuid


def _build_mention_email(db: Session, payload: dict) -> tuple[str, str]:
    title = str(payload.get("title") or "Mentioned")
    subtitle = str(payload.get("subtitle") or "").strip()
    preview = str(payload.get("preview") or "").strip()
    target_url = str(payload.get("target_url") or "").strip()

    if not target_url:
        conversation_id = payload.get("conversation_id")
        ticket_id = payload.get("ticket_id")
        ticket_number = payload.get("ticket_number")
        project_id = payload.get("project_id")
        project_number = payload.get("project_number")
        if conversation_id:
            target_url = f"/admin/crm/inbox?conversation_id={conversation_id}"
        elif ticket_id or ticket_number:
            ref = ticket_number or ticket_id
            target_url = f"/admin/support/tickets/{ref}"
        elif project_id or project_number:
            ref = project_number or project_id
            target_url = f"/admin/projects/{ref}"

    base_url = (email_service.get_app_url(db) or "").rstrip("/")
    link = target_url
    if link and link.startswith("/") and base_url:
        link = f"{base_url}{link}"

    subject = title if not subtitle else f"{title}: {subtitle}"
    if len(subject) > 200:
        subject = subject[:197].rstrip() + "..."

    safe_subtitle = html_escape(subtitle, quote=True)
    safe_preview = html_escape(preview, quote=True)
    safe_link = html_escape(link, quote=True) if link else ""

    body_parts = ["<p>You were mentioned.</p>"]
    if subtitle:
        body_parts.append(f"<p>Context: {safe_subtitle}</p>")
    if preview:
        body_parts.append(f"<p>Message: {safe_preview}</p>")
    if link:
        body_parts.append(f'<p>Open: <a href="{safe_link}">{safe_link}</a></p>')
    body_html = "\n".join(body_parts)
    return subject, body_html


def queue_mention_email_notifications(db: Session, *, recipient_person_ids: list[str], payload: dict) -> None:
    if not recipient_person_ids:
        return
    subject, body_html = _build_mention_email(db, payload)
    for person_id in recipient_person_ids:
        person = db.get(Person, coerce_uuid(person_id))
        if not person or not person.email:
            continue
        notification_service.notifications.create(
            db,
            NotificationCreate(
                channel=NotificationChannel.email,
                recipient=person.email,
                subject=subject,
                body=body_html,
            ),
        )


def notify_agent_mentions(
    db: Session,
    *,
    mentioned_agent_ids: list[str] | None,
    actor_person_id: str | None,
    payload: dict,
) -> None:
    """Broadcast an `AGENT_NOTIFICATION` websocket event to mentioned agents.

    Best-effort: failures should never break the user action that triggered the mention.
    """
    if not mentioned_agent_ids:
        return

    agent_uuids = []
    for raw in mentioned_agent_ids:
        try:
            agent_uuids.append(coerce_uuid(raw))
        except Exception:
            continue
    if not agent_uuids:
        return

    agents = (
        db.query(CrmAgent)
        .filter(CrmAgent.id.in_(agent_uuids))
        .filter(CrmAgent.is_active.is_(True))
        .filter(CrmAgent.person_id.isnot(None))
        .all()
    )
    if not agents:
        return

    actor_uuid = None
    if actor_person_id:
        try:
            actor_uuid = str(coerce_uuid(actor_person_id))
        except Exception:
            actor_uuid = None

    recipient_person_ids = []
    seen = set()
    for agent in agents:
        pid = str(agent.person_id)
        if actor_uuid and pid == actor_uuid:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        recipient_person_ids.append(pid)

    if not recipient_person_ids:
        return

    from app.websocket.broadcaster import broadcast_agent_notification

    for person_id in recipient_person_ids:
        broadcast_agent_notification(person_id, payload)
    queue_mention_email_notifications(db, recipient_person_ids=recipient_person_ids, payload=payload)

"""Ticket comment @mention notifications."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.agent_mentions import list_active_users_for_mentions, resolve_mentioned_person_ids
from app.services.common import coerce_uuid


def list_ticket_mention_users(db: Session, *, limit: int = 200) -> list[dict]:
    """Return active web-user options suitable for ticket @mention autocomplete.

    Shape: [{"id": "person:<person_uuid>", "label": "<display name>"}]
    """
    return list_active_users_for_mentions(db, limit=limit)


def notify_ticket_comment_mentions(
    db: Session,
    *,
    ticket_id: str,
    ticket_number: str | None,
    ticket_title: str | None,
    comment_preview: str | None,
    mentioned_agent_ids: list[str] | None,
    actor_person_id: str | None,
) -> None:
    """Broadcast an in-app notification to mentioned agents.

    Notes:
    - This uses the existing `AGENT_NOTIFICATION` websocket event.
    - Delivery is best-effort; failures should never break comment creation.
    """
    if not mentioned_agent_ids:
        return

    recipient_person_ids: list[str] = resolve_mentioned_person_ids(db, mentioned_agent_ids)

    if not recipient_person_ids:
        return

    actor_uuid = None
    if actor_person_id:
        try:
            actor_uuid = str(coerce_uuid(actor_person_id))
        except Exception:
            actor_uuid = None

    if actor_uuid:
        recipient_person_ids = [pid for pid in recipient_person_ids if pid != actor_uuid]
    if not recipient_person_ids:
        return

    subject = ticket_title or ""
    ref = ticket_number or ticket_id
    subtitle = f"Ticket {ref}"
    if subject:
        subtitle = f"{subtitle} · {subject}"

    payload = {
        "kind": "mention",
        "title": "Mentioned in ticket",
        "subtitle": subtitle,
        "preview": comment_preview,
        "ticket_id": ticket_id,
        "ticket_number": ticket_number,
    }

    from app.websocket.broadcaster import broadcast_agent_notification

    for person_id in recipient_person_ids:
        broadcast_agent_notification(person_id, payload)
    try:
        from app.services.agent_mentions import queue_mention_email_notifications

        queue_mention_email_notifications(db, recipient_person_ids=recipient_person_ids, payload=payload)
    except Exception:
        # Email mention notifications are best-effort.
        pass

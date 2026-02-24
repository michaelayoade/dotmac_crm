"""Generic @mention notifications.

Used across admin surfaces (inbox, tickets, projects, tasks).
"""

from __future__ import annotations

from html import escape as html_escape

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.auth import UserCredential
from app.models.crm.team import CrmAgent
from app.models.notification import NotificationChannel
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember
from app.schemas.notification import NotificationCreate
from app.services import email as email_service
from app.services import notification as notification_service
from app.services.common import coerce_uuid


def list_active_users_for_mentions(db: Session, *, limit: int = 200) -> list[dict]:
    """Return active web-user options suitable for @mention autocomplete.

    Shape:
    - {"id": "person:<person_uuid>", "label": "<display name>", "kind": "person"}
    - {"id": "group:<team_uuid>", "label": "<group name>", "kind": "group"}
    """
    safe_limit = max(int(limit or 200), 1)
    rows = (
        db.query(Person)
        .join(
            UserCredential,
            and_(
                UserCredential.person_id == Person.id,
                UserCredential.is_active.is_(True),
            ),
        )
        .filter(Person.is_active.is_(True))
        .order_by(Person.last_name.asc(), Person.first_name.asc(), Person.created_at.asc())
        .limit(safe_limit)
        .all()
    )
    items: list[dict] = []
    seen_person_ids: set[str] = set()
    for person in rows:
        person_id = str(person.id)
        if person_id in seen_person_ids:
            continue
        seen_person_ids.add(person_id)
        label = (
            person.display_name
            or f"{(person.first_name or '').strip()} {(person.last_name or '').strip()}".strip()
            or person.email
            or "User"
        )
        items.append({"id": f"person:{person_id}", "label": label, "kind": "person"})

    group_rows = (
        db.query(ServiceTeam.id, ServiceTeam.name, func.count(ServiceTeamMember.person_id).label("member_count"))
        .join(
            ServiceTeamMember,
            and_(
                ServiceTeamMember.team_id == ServiceTeam.id,
                ServiceTeamMember.is_active.is_(True),
            ),
        )
        .join(
            Person,
            and_(
                Person.id == ServiceTeamMember.person_id,
                Person.is_active.is_(True),
            ),
        )
        .join(
            UserCredential,
            and_(
                UserCredential.person_id == Person.id,
                UserCredential.is_active.is_(True),
            ),
        )
        .filter(ServiceTeam.is_active.is_(True))
        .group_by(ServiceTeam.id, ServiceTeam.name)
        .order_by(ServiceTeam.name.asc())
        .limit(safe_limit)
        .all()
    )
    seen_group_ids: set[str] = set()
    for team_id, team_name, member_count in group_rows:
        gid = str(team_id)
        if gid in seen_group_ids:
            continue
        seen_group_ids.add(gid)
        label = (team_name or "Group").strip() or "Group"
        if member_count and int(member_count) > 0:
            label = f"{label} (Group)"
        items.append({"id": f"group:{gid}", "label": label, "kind": "group"})
    return items


def resolve_mentioned_person_ids(db: Session, mentioned_agent_ids: list[str] | None) -> list[str]:
    """Resolve person IDs from mention tokens (person, agent, or group)."""
    if not mentioned_agent_ids:
        return []

    agent_uuids = []
    person_uuids = []
    group_uuids = []
    for raw in mentioned_agent_ids:
        token = (raw or "").strip()
        if not token:
            continue
        if token.startswith("person:"):
            token = token.split(":", 1)[1].strip()
            try:
                person_uuids.append(coerce_uuid(token))
            except Exception:
                continue
            continue
        if token.startswith("group:"):
            token = token.split(":", 1)[1].strip()
            try:
                group_uuids.append(coerce_uuid(token))
            except Exception:
                continue
            continue
        if token.startswith("agent:"):
            token = token.split(":", 1)[1].strip()
        try:
            agent_uuids.append(coerce_uuid(token))
        except Exception:
            continue

    recipient_person_ids = []
    seen = set()

    if person_uuids:
        people = db.query(Person).filter(Person.id.in_(person_uuids)).filter(Person.is_active.is_(True)).all()
        for person in people:
            pid = str(person.id)
            if pid in seen:
                continue
            seen.add(pid)
            recipient_person_ids.append(pid)

    if agent_uuids:
        agents = (
            db.query(CrmAgent)
            .filter(CrmAgent.id.in_(agent_uuids))
            .filter(CrmAgent.is_active.is_(True))
            .filter(CrmAgent.person_id.isnot(None))
            .all()
        )
        for agent in agents:
            pid = str(agent.person_id)
            if pid in seen:
                continue
            seen.add(pid)
            recipient_person_ids.append(pid)

    if group_uuids:
        members = (
            db.query(ServiceTeamMember.person_id)
            .join(
                ServiceTeam,
                and_(
                    ServiceTeam.id == ServiceTeamMember.team_id,
                    ServiceTeam.is_active.is_(True),
                ),
            )
            .join(
                Person,
                and_(
                    Person.id == ServiceTeamMember.person_id,
                    Person.is_active.is_(True),
                ),
            )
            .join(
                UserCredential,
                and_(
                    UserCredential.person_id == Person.id,
                    UserCredential.is_active.is_(True),
                ),
            )
            .filter(ServiceTeamMember.team_id.in_(group_uuids))
            .filter(ServiceTeamMember.is_active.is_(True))
            .all()
        )
        for (person_id,) in members:
            pid = str(person_id)
            if pid in seen:
                continue
            seen.add(pid)
            recipient_person_ids.append(pid)

    return recipient_person_ids


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
    """Broadcast an `AGENT_NOTIFICATION` websocket event to mentioned users.

    Accepts mention IDs as:
    - `person:<person_uuid>` (preferred)
    - `agent:<agent_uuid>` or raw `<agent_uuid>` (legacy compatibility)

    Best-effort: failures should never break the user action that triggered
    the mention.
    """
    if not mentioned_agent_ids:
        return

    recipient_person_ids = resolve_mentioned_person_ids(db, mentioned_agent_ids)

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

    from app.websocket.broadcaster import broadcast_agent_notification

    for person_id in recipient_person_ids:
        broadcast_agent_notification(person_id, payload)
    queue_mention_email_notifications(db, recipient_person_ids=recipient_person_ids, payload=payload)

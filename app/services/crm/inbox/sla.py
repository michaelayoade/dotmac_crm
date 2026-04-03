"""SLA breach detection for CRM inbox conversations."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ConversationStatus
from app.models.domain_settings import SettingDomain
from app.services import settings_spec

logger = logging.getLogger(__name__)

_DEFAULT_RESPONSE = {"urgent": 60, "high": 240, "medium": 480, "low": 1440, "none": 1440}
_DEFAULT_RESOLUTION = {"urgent": 240, "high": 1440, "medium": 2880, "low": 4320, "none": 4320}
_PRIORITY_KEYS = ("urgent", "high", "medium", "low", "none")


def get_sla_targets(db: Session) -> dict[str, dict[str, int]]:
    """Load per-priority SLA targets from settings, falling back to defaults."""
    response: dict[str, int] = {}
    resolution: dict[str, int] = {}
    for priority in _PRIORITY_KEYS:
        resp_key = f"crm_sla_response_{priority}_minutes"
        res_key = f"crm_sla_resolution_{priority}_minutes"
        resp_val = settings_spec.resolve_value(db, SettingDomain.notification, resp_key)
        res_val = settings_spec.resolve_value(db, SettingDomain.notification, res_key)
        response[priority] = int(resp_val) if resp_val is not None else _DEFAULT_RESPONSE[priority]
        resolution[priority] = int(res_val) if res_val is not None else _DEFAULT_RESOLUTION[priority]
    return {"response": response, "resolution": resolution}


def _priority_value(conv: Conversation) -> str:
    if conv.priority is None:
        return "none"
    return conv.priority.value


def find_response_breaches(db: Session, targets_minutes: dict[str, int]) -> list[Conversation]:
    """Find open/pending conversations that have breached response SLA."""
    now = datetime.now(UTC)
    candidates = (
        db.query(Conversation)
        .filter(
            Conversation.status.in_([ConversationStatus.open, ConversationStatus.pending]),
            Conversation.first_response_at.is_(None),
            Conversation.is_active.is_(True),
        )
        .all()
    )
    breached: list[Conversation] = []
    for conv in candidates:
        priority = _priority_value(conv)
        threshold_minutes = targets_minutes.get(priority, 1440)
        deadline = conv.created_at + timedelta(minutes=threshold_minutes)
        if now > deadline:
            breached.append(conv)
    return breached


def find_resolution_breaches(db: Session, targets_minutes: dict[str, int]) -> list[Conversation]:
    """Find open/pending conversations that have breached resolution SLA.

    Only considers conversations that already received a first response
    (first_response_at is set) to avoid double-alerting with response breaches.
    """
    now = datetime.now(UTC)
    candidates = (
        db.query(Conversation)
        .filter(
            Conversation.status.in_([ConversationStatus.open, ConversationStatus.pending]),
            Conversation.first_response_at.isnot(None),
            Conversation.resolved_at.is_(None),
            Conversation.is_active.is_(True),
        )
        .all()
    )
    breached: list[Conversation] = []
    for conv in candidates:
        priority = _priority_value(conv)
        threshold_minutes = targets_minutes.get(priority, 4320)
        deadline = conv.created_at + timedelta(minutes=threshold_minutes)
        if now > deadline:
            breached.append(conv)
    return breached


def check_and_alert_breaches(db: Session) -> dict:
    """Run SLA breach check and create in-app alerts. Returns stats."""
    from app.models.notification import Notification, NotificationChannel, NotificationStatus

    targets = get_sla_targets(db)
    response_breaches = find_response_breaches(db, targets["response"])
    resolution_breaches = find_resolution_breaches(db, targets["resolution"])

    alerted_response = 0
    alerted_resolution = 0

    for conv in response_breaches:
        metadata = conv.metadata_ if isinstance(conv.metadata_, dict) else {}
        if metadata.get("sla_response_breach_alerted_at"):
            continue
        metadata["sla_response_breach_alerted_at"] = datetime.now(UTC).isoformat()
        conv.metadata_ = metadata
        flag_modified(conv, "metadata_")

        recipient = _resolve_notification_recipient(db, conv)
        if recipient:
            elapsed_hours = round((datetime.now(UTC) - conv.created_at).total_seconds() / 3600, 1)
            db.add(
                Notification(
                    channel=NotificationChannel.push,
                    recipient=recipient,
                    subject=f"SLA Breach: Response overdue ({elapsed_hours}h)",
                    body=(
                        f'Conversation "{conv.subject or "No subject"}" '
                        f"(priority: {_priority_value(conv)}) has no first response "
                        f"after {elapsed_hours} hours.\n"
                        f"Open: /admin/crm/inbox?conversation_id={conv.id}"
                    ),
                    status=NotificationStatus.delivered,
                    sent_at=datetime.now(UTC),
                )
            )
            alerted_response += 1

    for conv in resolution_breaches:
        metadata = conv.metadata_ if isinstance(conv.metadata_, dict) else {}
        if metadata.get("sla_resolution_breach_alerted_at"):
            continue
        metadata["sla_resolution_breach_alerted_at"] = datetime.now(UTC).isoformat()
        conv.metadata_ = metadata
        flag_modified(conv, "metadata_")

        recipient = _resolve_notification_recipient(db, conv)
        if recipient:
            elapsed_hours = round((datetime.now(UTC) - conv.created_at).total_seconds() / 3600, 1)
            db.add(
                Notification(
                    channel=NotificationChannel.push,
                    recipient=recipient,
                    subject=f"SLA Breach: Resolution overdue ({elapsed_hours}h)",
                    body=(
                        f'Conversation "{conv.subject or "No subject"}" '
                        f"(priority: {_priority_value(conv)}) has been open "
                        f"for {elapsed_hours} hours without resolution.\n"
                        f"Open: /admin/crm/inbox?conversation_id={conv.id}"
                    ),
                    status=NotificationStatus.delivered,
                    sent_at=datetime.now(UTC),
                )
            )
            alerted_resolution += 1

    if alerted_response or alerted_resolution:
        db.commit()

    return {
        "response_breaches": len(response_breaches),
        "resolution_breaches": len(resolution_breaches),
        "alerted_response": alerted_response,
        "alerted_resolution": alerted_resolution,
    }


def _resolve_notification_recipient(db: Session, conv: Conversation) -> str | None:
    from app.models.crm.conversation import ConversationAssignment
    from app.models.crm.team import CrmAgent
    from app.models.person import Person

    assignment = (
        db.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conv.id, ConversationAssignment.is_active.is_(True))
        .first()
    )
    if not assignment or not assignment.agent_id:
        return None
    agent = db.get(CrmAgent, assignment.agent_id)
    if not agent:
        return None
    person = db.get(Person, agent.person_id)
    return person.email if person else None

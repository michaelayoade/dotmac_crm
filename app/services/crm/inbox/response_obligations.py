"""Authoritative response-obligation policy, reconciliation, and escalation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import (
    ConversationStatus,
    MessageDirection,
    MessageStatus,
    ResponseObligationState,
)
from app.models.crm.response_obligation import ResponseObligation
from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamMemberRole, ServiceTeamType
from app.services.common import coerce_uuid
from app.services.crm.inbox.sla import get_sla_targets
from app.services.settings_spec import SettingDomain, resolve_value

logger = logging.getLogger(__name__)

AWAITING_STATES = (
    ResponseObligationState.awaiting_first_response,
    ResponseObligationState.awaiting_follow_up,
)
RESOLVED_STATUSES = (ConversationStatus.resolved, ConversationStatus.resolved_to_ticket)
SUCCESSFUL_OUTBOUND_STATUSES = (MessageStatus.sent, MessageStatus.delivered, MessageStatus.read)
DEFAULT_TEAM_ESCALATION_SECONDS = 900
DEFAULT_OPERATIONS_ESCALATION_SECONDS = 3600
DEFAULT_REMINDER_DELAY_SECONDS = 300


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _activity_at(message: Message) -> datetime:
    value = message.received_at or message.sent_at or message.created_at
    return _as_utc(value) or _now()


def _latest_inbound(db: Session, conversation_id) -> Message | None:
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.direction == MessageDirection.inbound)
        .order_by(
            Message.received_at.desc().nullslast(),
            Message.created_at.desc(),
            Message.id.desc(),
        )
        .first()
    )


def _latest_meaningful_outbound(db: Session, conversation_id) -> Message | None:
    """Return the latest successfully-sent, non-exempt response.

    Historical messages do not always have an author, so authorship alone is
    not a safe discriminator. Known automation marks itself in metadata and
    callers may explicitly set ``response_obligation_exempt``.
    """
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.direction == MessageDirection.outbound)
        .filter(Message.status.in_(SUCCESSFUL_OUTBOUND_STATUSES))
        .filter(
            or_(
                Message.metadata_.is_(None),
                Message.metadata_["response_obligation_exempt"].as_boolean().isnot(True),
            )
        )
        .filter(
            or_(
                Message.metadata_.is_(None),
                Message.metadata_["ai_intake_generated"].as_boolean().isnot(True),
            )
        )
        .order_by(
            Message.sent_at.desc().nullslast(),
            Message.created_at.desc(),
            Message.id.desc(),
        )
        .first()
    )


def _active_assignment(db: Session, conversation_id) -> ConversationAssignment | None:
    return (
        db.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation_id)
        .filter(ConversationAssignment.is_active.is_(True))
        .order_by(
            ConversationAssignment.assigned_at.desc().nullslast(),
            ConversationAssignment.created_at.desc(),
        )
        .first()
    )


def _owner_scope(assignment: ConversationAssignment | None) -> str:
    if assignment and assignment.agent_id:
        return f"agent:{assignment.agent_id}"
    if assignment and assignment.team_id:
        return f"team:{assignment.team_id}"
    return "unassigned"


def _policy_config(db: Session) -> dict[str, object]:
    cached = db.info.get("_crm_response_policy_config")
    if isinstance(cached, dict):
        return cached
    cached = {
        "response_targets": get_sla_targets(db)["response"],
        "reminder_delay_seconds": _coerce_positive_int(
            resolve_value(db, SettingDomain.notification, "crm_inbox_reply_reminder_delay_seconds"),
            DEFAULT_REMINDER_DELAY_SECONDS,
        ),
        "team_escalation_seconds": _coerce_positive_int(
            resolve_value(db, SettingDomain.notification, "crm_inbox_response_escalation_team_seconds"),
            DEFAULT_TEAM_ESCALATION_SECONDS,
        ),
        "operations_escalation_seconds": _coerce_positive_int(
            resolve_value(db, SettingDomain.notification, "crm_inbox_response_escalation_operations_seconds"),
            DEFAULT_OPERATIONS_ESCALATION_SECONDS,
        ),
    }
    db.info["_crm_response_policy_config"] = cached
    return cached


def _response_due_at(db: Session, conversation: Conversation, inbound_at: datetime) -> datetime:
    priority = conversation.priority.value if conversation.priority else "none"
    targets = _policy_config(db)["response_targets"]
    target_minutes = targets.get(priority, 1440) if isinstance(targets, dict) else 1440
    return inbound_at + timedelta(minutes=target_minutes)


def _initial_reminder_at(db: Session, inbound_at: datetime) -> datetime:
    delay = _coerce_positive_int(
        _policy_config(db)["reminder_delay_seconds"],
        DEFAULT_REMINDER_DELAY_SECONDS,
    )
    return inbound_at + timedelta(seconds=delay)


def reconcile_response_obligation(
    db: Session,
    conversation_id: str,
    *,
    now: datetime | None = None,
) -> ResponseObligation | None:
    """Derive and persist the one current response decision for a conversation."""
    clock = now or _now()
    conversation_uuid = coerce_uuid(conversation_id)
    conversation = db.get(Conversation, conversation_uuid)
    if not conversation:
        obligation = db.get(ResponseObligation, conversation_uuid)
        if obligation:
            db.delete(obligation)
        return None

    inbound = _latest_inbound(db, conversation_uuid)
    outbound = _latest_meaningful_outbound(db, conversation_uuid)
    assignment = _active_assignment(db, conversation_uuid)
    inbound_at = _activity_at(inbound) if inbound else None
    outbound_at = _activity_at(outbound) if outbound else None
    owner_scope = _owner_scope(assignment)

    if not conversation.is_active or conversation.status in RESOLVED_STATUSES:
        state = ResponseObligationState.resolved
    elif conversation.status == ConversationStatus.snoozed:
        state = ResponseObligationState.snoozed
    elif inbound is None:
        state = ResponseObligationState.no_customer_message
    elif outbound is None:
        state = ResponseObligationState.awaiting_first_response
    elif inbound_at and outbound_at and inbound_at >= outbound_at:
        state = ResponseObligationState.awaiting_follow_up
    else:
        state = ResponseObligationState.responded

    awaiting = state in AWAITING_STATES
    trigger_message_id = inbound.id if awaiting and inbound else None
    due_at = _response_due_at(db, conversation, inbound_at) if awaiting and inbound_at else None
    initial_reminder_at = _initial_reminder_at(db, inbound_at) if awaiting and inbound_at else None

    obligation = db.get(ResponseObligation, conversation_uuid)
    if not obligation:
        obligation = ResponseObligation(
            conversation_id=conversation_uuid,
            state=state,
            owner_scope=owner_scope,
        )
        db.add(obligation)

    trigger_changed = obligation.trigger_message_id != trigger_message_id
    owner_changed = obligation.owner_scope != owner_scope
    obligation.state = state
    obligation.trigger_message_id = trigger_message_id
    obligation.latest_inbound_at = inbound_at
    obligation.latest_outbound_at = outbound_at
    obligation.response_due_at = due_at
    obligation.responded_at = outbound_at if state == ResponseObligationState.responded else None
    obligation.owner_agent_id = assignment.agent_id if assignment else None
    obligation.owner_team_id = assignment.team_id if assignment else None
    obligation.owner_scope = owner_scope
    obligation.reconciled_at = clock

    if awaiting:
        if trigger_changed:
            obligation.breached_at = None
            obligation.escalation_level = 0
            obligation.last_escalated_at = None
            obligation.next_escalation_at = initial_reminder_at
        elif owner_changed:
            # A new accountable owner must receive the current obligation even
            # when a previous owner had already been reminded.
            obligation.escalation_level = 0
            obligation.last_escalated_at = None
            obligation.next_escalation_at = max(clock, _as_utc(initial_reminder_at) or clock)
        elif obligation.next_escalation_at is None and obligation.escalation_level == 0:
            obligation.next_escalation_at = initial_reminder_at
    else:
        obligation.breached_at = None
        obligation.escalation_level = 0
        obligation.next_escalation_at = None
        obligation.last_escalated_at = None

    db.flush()
    return obligation


def reconcile_response_obligations(db: Session, *, limit: int = 200) -> int:
    """Idempotently repair the stalest/missing obligation rows in bounded batches."""
    rows = (
        db.query(Conversation.id)
        .outerjoin(ResponseObligation, ResponseObligation.conversation_id == Conversation.id)
        .filter(Conversation.is_active.is_(True))
        .order_by(ResponseObligation.reconciled_at.asc().nullsfirst(), Conversation.updated_at.asc())
        .limit(max(1, min(limit, 1000)))
        .all()
    )
    for (conversation_id,) in rows:
        reconcile_response_obligation(db, str(conversation_id))
    db.commit()
    return len(rows)


def _coerce_positive_int(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str | bytes | bytearray):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _escalation_intervals(db: Session) -> tuple[int, int]:
    config = _policy_config(db)
    return (
        _coerce_positive_int(config["team_escalation_seconds"], DEFAULT_TEAM_ESCALATION_SECONDS),
        _coerce_positive_int(config["operations_escalation_seconds"], DEFAULT_OPERATIONS_ESCALATION_SECONDS),
    )


def _person_recipient(person: Person | None) -> str | None:
    if not person or not person.is_active:
        return None
    return (person.email or "").strip() or str(person.id)


def _agent_recipients(db: Session, agent_ids: list) -> list[str]:
    if not agent_ids:
        return []
    rows = (
        db.query(Person)
        .join(CrmAgent, CrmAgent.person_id == Person.id)
        .filter(CrmAgent.id.in_(agent_ids), CrmAgent.is_active.is_(True), Person.is_active.is_(True))
        .all()
    )
    return [recipient for person in rows if (recipient := _person_recipient(person))]


def _team_agent_recipients(db: Session, team_id) -> list[str]:
    agent_ids = [
        row[0]
        for row in db.query(CrmAgentTeam.agent_id)
        .filter(CrmAgentTeam.team_id == team_id, CrmAgentTeam.is_active.is_(True))
        .all()
    ]
    return _agent_recipients(db, agent_ids)


def _service_team_lead_recipients(db: Session, crm_team_id) -> list[str]:
    team = db.get(CrmTeam, crm_team_id) if crm_team_id else None
    if not team or not team.service_team_id:
        return []
    person_ids = {
        row[0]
        for row in db.query(ServiceTeamMember.person_id)
        .filter(
            ServiceTeamMember.team_id == team.service_team_id,
            ServiceTeamMember.is_active.is_(True),
            ServiceTeamMember.role.in_([ServiceTeamMemberRole.lead, ServiceTeamMemberRole.manager]),
        )
        .all()
    }
    service_team = db.get(ServiceTeam, team.service_team_id)
    if service_team and service_team.manager_person_id:
        person_ids.add(service_team.manager_person_id)
    people = db.query(Person).filter(Person.id.in_(person_ids), Person.is_active.is_(True)).all() if person_ids else []
    return [recipient for person in people if (recipient := _person_recipient(person))]


def _operations_lead_recipients(db: Session) -> list[str]:
    teams = (
        db.query(ServiceTeam)
        .filter(
            ServiceTeam.is_active.is_(True),
            ServiceTeam.team_type.in_([ServiceTeamType.operations, ServiceTeamType.support]),
        )
        .all()
    )
    person_ids = {team.manager_person_id for team in teams if team.manager_person_id}
    team_ids = [team.id for team in teams]
    if team_ids:
        person_ids.update(
            row[0]
            for row in db.query(ServiceTeamMember.person_id)
            .filter(
                ServiceTeamMember.team_id.in_(team_ids),
                ServiceTeamMember.is_active.is_(True),
                ServiceTeamMember.role.in_([ServiceTeamMemberRole.lead, ServiceTeamMemberRole.manager]),
            )
            .all()
        )
    people = db.query(Person).filter(Person.id.in_(person_ids), Person.is_active.is_(True)).all() if person_ids else []
    return [recipient for person in people if (recipient := _person_recipient(person))]


def _recipients_for_level(db: Session, obligation: ResponseObligation) -> list[str]:
    if obligation.escalation_level == 0:
        if obligation.owner_agent_id:
            recipients = _agent_recipients(db, [obligation.owner_agent_id])
        elif obligation.owner_team_id:
            recipients = _team_agent_recipients(db, obligation.owner_team_id)
        else:
            recipients = _operations_lead_recipients(db)
    elif obligation.escalation_level == 1:
        recipients = _service_team_lead_recipients(db, obligation.owner_team_id)
        if not recipients:
            recipients = _operations_lead_recipients(db)
    else:
        recipients = _operations_lead_recipients(db)
    return sorted(set(recipients))


def _notification_subject(level: int, state: ResponseObligationState) -> str:
    label = "First response" if state == ResponseObligationState.awaiting_first_response else "Follow-up response"
    if level == 0:
        return f"CRM response reminder: {label}"
    if level == 1:
        return f"CRM response escalated to team lead: {label}"
    return f"CRM response escalated to operations: {label}"


def process_due_response_obligations(
    db: Session,
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> dict[str, int]:
    """Notify and escalate due obligations using only the indexed decision table."""
    clock = now or _now()
    rows = (
        db.query(ResponseObligation)
        .join(Conversation, Conversation.id == ResponseObligation.conversation_id)
        .filter(
            Conversation.is_active.is_(True),
            Conversation.is_muted.is_(False),
            Conversation.status.in_([ConversationStatus.open, ConversationStatus.pending]),
            ResponseObligation.state.in_(AWAITING_STATES),
            ResponseObligation.next_escalation_at.isnot(None),
            ResponseObligation.next_escalation_at <= clock,
        )
        .order_by(ResponseObligation.next_escalation_at.asc())
        .with_for_update(skip_locked=True)
        .limit(max(1, min(limit, 500)))
        .all()
    )
    team_seconds, operations_seconds = _escalation_intervals(db)
    notified = 0
    escalated = 0
    missing_recipients = 0

    for obligation in rows:
        conversation = db.get(Conversation, obligation.conversation_id)
        if not conversation:
            db.delete(obligation)
            continue
        # Recheck source facts under the same transaction before producing a consequence.
        current = reconcile_response_obligation(db, str(conversation.id), now=clock)
        if (
            not current
            or current.state not in AWAITING_STATES
            or not current.next_escalation_at
            or (_as_utc(current.next_escalation_at) or clock) > clock
        ):
            continue
        obligation = current

        level = obligation.escalation_level
        recipients = _recipients_for_level(db, obligation)
        if not recipients:
            missing_recipients += 1
            recipients = ["system:team_leads" if level < 2 else "system:operations_managers"]

        due_at = _as_utc(obligation.response_due_at)
        inbound_at = _as_utc(obligation.latest_inbound_at)
        if due_at and due_at <= clock:
            timing = f"is {int((clock - due_at).total_seconds() // 60)} minutes overdue"
            if obligation.breached_at is None:
                obligation.breached_at = clock
        else:
            waiting_minutes = max(0, int((clock - (inbound_at or clock)).total_seconds() // 60))
            due_minutes = max(0, int(((due_at or clock) - clock).total_seconds() // 60))
            timing = f"has waited {waiting_minutes} minutes; response SLA is due in {due_minutes} minutes"
        target_url = f"/admin/crm/inbox?conversation_id={conversation.id}"
        body = (
            f'Conversation "{conversation.subject or "No subject"}" is awaiting a customer response '
            f"and {timing}. Owner: {obligation.owner_scope}.\n"
            f"Open: {target_url}"
        )
        subject = _notification_subject(level, obligation.state)
        for recipient in recipients:
            db.add(
                Notification(
                    channel=NotificationChannel.push,
                    recipient=recipient,
                    subject=subject[:200],
                    body=body,
                    status=NotificationStatus.delivered,
                    sent_at=clock,
                )
            )
            notified += 1

        obligation.last_escalated_at = clock
        obligation.escalation_level = level + 1
        if level == 0:
            obligation.next_escalation_at = clock + timedelta(seconds=team_seconds)
        elif level == 1:
            obligation.next_escalation_at = clock + timedelta(seconds=operations_seconds)
        else:
            obligation.next_escalation_at = None
        if level > 0:
            escalated += 1

    db.commit()
    return {
        "processed": len(rows),
        "notified": notified,
        "escalated": escalated,
        "missing_recipients": missing_recipients,
    }

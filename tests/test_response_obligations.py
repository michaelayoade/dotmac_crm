"""Tests for the authoritative CRM response-obligation control plane."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import (
    ChannelType,
    ConversationPriority,
    ConversationStatus,
    MessageDirection,
    MessageStatus,
    ResponseObligationState,
)
from app.models.crm.response_obligation import ResponseObligation
from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam
from app.models.notification import Notification
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamMemberRole, ServiceTeamType
from app.services.crm.inbox.response_obligations import (
    process_due_response_obligations,
    reconcile_response_obligation,
    reconcile_response_obligations,
)


def _person(db, email: str) -> Person:
    person = Person(first_name="Response", last_name="Owner", email=email)
    db.add(person)
    db.flush()
    return person


def _conversation(db, contact: Person, *, priority=ConversationPriority.urgent) -> Conversation:
    conversation = Conversation(
        person_id=contact.id,
        subject="Customer needs help",
        priority=priority,
        status=ConversationStatus.open,
    )
    db.add(conversation)
    db.flush()
    return conversation


def _message(
    db,
    conversation: Conversation,
    *,
    direction: MessageDirection,
    at: datetime,
    author_id=None,
    metadata: dict | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.whatsapp,
        direction=direction,
        status=MessageStatus.received if direction == MessageDirection.inbound else MessageStatus.sent,
        body="Customer message" if direction == MessageDirection.inbound else "Agent response",
        received_at=at if direction == MessageDirection.inbound else None,
        sent_at=at if direction == MessageDirection.outbound else None,
        author_id=author_id,
        metadata_=metadata,
    )
    db.add(message)
    conversation.last_message_at = at
    conversation.updated_at = at
    db.flush()
    return message


def test_obligation_tracks_first_response_and_customer_follow_up(db_session):
    now = datetime.now(UTC)
    contact = _person(db_session, "response-contact@example.com")
    agent_person = _person(db_session, "response-agent@example.com")
    agent = CrmAgent(person_id=agent_person.id, is_active=True)
    db_session.add(agent)
    db_session.flush()
    conversation = _conversation(db_session, contact)
    db_session.add(
        ConversationAssignment(
            conversation_id=conversation.id,
            agent_id=agent.id,
            is_active=True,
            assigned_at=now,
        )
    )
    first_inbound = _message(
        db_session,
        conversation,
        direction=MessageDirection.inbound,
        at=now,
    )

    obligation = reconcile_response_obligation(db_session, str(conversation.id), now=now)
    assert obligation is not None
    assert obligation.state == ResponseObligationState.awaiting_first_response
    assert obligation.trigger_message_id == first_inbound.id
    assert obligation.owner_scope == f"agent:{agent.id}"
    assert obligation.response_due_at == now + timedelta(minutes=60)

    _message(
        db_session,
        conversation,
        direction=MessageDirection.outbound,
        at=now + timedelta(minutes=5),
        author_id=agent_person.id,
    )
    obligation = reconcile_response_obligation(db_session, str(conversation.id), now=now + timedelta(minutes=5))
    assert obligation is not None
    assert obligation.state == ResponseObligationState.responded
    assert obligation.response_due_at is None

    follow_up = _message(
        db_session,
        conversation,
        direction=MessageDirection.inbound,
        at=now + timedelta(minutes=10),
    )
    obligation = reconcile_response_obligation(db_session, str(conversation.id), now=now + timedelta(minutes=10))
    assert obligation is not None
    assert obligation.state == ResponseObligationState.awaiting_follow_up
    assert obligation.trigger_message_id == follow_up.id
    assert obligation.response_due_at == now + timedelta(minutes=70)


def test_ai_acknowledgement_does_not_discharge_obligation(db_session):
    now = datetime.now(UTC)
    contact = _person(db_session, "ai-exempt-contact@example.com")
    conversation = _conversation(db_session, contact)
    _message(db_session, conversation, direction=MessageDirection.inbound, at=now)
    _message(
        db_session,
        conversation,
        direction=MessageDirection.outbound,
        at=now + timedelta(minutes=1),
        metadata={"ai_intake_generated": True},
    )

    obligation = reconcile_response_obligation(db_session, str(conversation.id), now=now + timedelta(minutes=1))

    assert obligation is not None
    assert obligation.state == ResponseObligationState.awaiting_first_response


def test_bounded_reconciler_repairs_missing_obligation(db_session):
    now = datetime.now(UTC)
    contact = _person(db_session, "reconcile-contact@example.com")
    conversation = _conversation(db_session, contact)
    _message(db_session, conversation, direction=MessageDirection.inbound, at=now)
    db_session.commit()

    assert db_session.get(ResponseObligation, conversation.id) is None

    assert reconcile_response_obligations(db_session, limit=1) == 1
    obligation = db_session.get(ResponseObligation, conversation.id)
    assert obligation is not None
    assert obligation.state == ResponseObligationState.awaiting_first_response


def test_muted_conversation_keeps_obligation_without_sending_reminder(db_session):
    now = datetime.now(UTC)
    contact = _person(db_session, "muted-contact@example.com")
    conversation = _conversation(db_session, contact)
    conversation.is_muted = True
    _message(
        db_session,
        conversation,
        direction=MessageDirection.inbound,
        at=now - timedelta(minutes=10),
    )
    obligation = reconcile_response_obligation(db_session, str(conversation.id), now=now)
    db_session.commit()

    result = process_due_response_obligations(db_session, now=now)

    assert result["processed"] == 0
    assert obligation is not None
    assert obligation.state == ResponseObligationState.awaiting_first_response
    assert db_session.query(Notification).count() == 0


def test_due_obligation_escalates_agent_then_team_lead_then_operations(db_session):
    now = datetime.now(UTC)
    contact = _person(db_session, "escalation-contact@example.com")
    agent_person = _person(db_session, "escalation-agent@example.com")
    lead_person = _person(db_session, "escalation-lead@example.com")
    operations_person = _person(db_session, "escalation-operations@example.com")

    support_team = ServiceTeam(
        name="Customer Support",
        team_type=ServiceTeamType.support,
        manager_person_id=lead_person.id,
        is_active=True,
    )
    operations_team = ServiceTeam(
        name="Operations",
        team_type=ServiceTeamType.operations,
        manager_person_id=operations_person.id,
        is_active=True,
    )
    db_session.add_all([support_team, operations_team])
    db_session.flush()
    db_session.add(
        ServiceTeamMember(
            team_id=support_team.id,
            person_id=lead_person.id,
            role=ServiceTeamMemberRole.lead,
            is_active=True,
        )
    )
    crm_team = CrmTeam(name="CRM Support", service_team_id=support_team.id, is_active=True)
    agent = CrmAgent(person_id=agent_person.id, is_active=True)
    db_session.add_all([crm_team, agent])
    db_session.flush()
    db_session.add(CrmAgentTeam(agent_id=agent.id, team_id=crm_team.id, is_active=True))

    conversation = _conversation(db_session, contact)
    db_session.add(
        ConversationAssignment(
            conversation_id=conversation.id,
            agent_id=agent.id,
            team_id=crm_team.id,
            assigned_at=now,
            is_active=True,
        )
    )
    _message(
        db_session,
        conversation,
        direction=MessageDirection.inbound,
        at=now - timedelta(minutes=61),
    )
    obligation = reconcile_response_obligation(db_session, str(conversation.id), now=now)
    db_session.commit()
    assert obligation is not None

    first = process_due_response_obligations(db_session, now=now)
    assert first["notified"] == 1
    assert obligation.escalation_level == 1
    assert db_session.query(Notification).filter(Notification.recipient == agent_person.email).count() == 1

    second = process_due_response_obligations(db_session, now=now + timedelta(seconds=901))
    assert second["escalated"] == 1
    assert obligation.escalation_level == 2
    assert db_session.query(Notification).filter(Notification.recipient == lead_person.email).count() == 1

    third = process_due_response_obligations(
        db_session,
        now=now + timedelta(seconds=901 + 3601),
    )
    assert third["escalated"] == 1
    assert obligation.escalation_level == 3
    assert obligation.next_escalation_at is None
    assert db_session.query(Notification).filter(Notification.recipient == operations_person.email).count() == 1


def test_reply_reminder_task_executes_policy_service(monkeypatch):
    from app.tasks import crm_inbox as task_module

    class FakeSession:
        def rollback(self):
            raise AssertionError("rollback should not be called")

        def close(self):
            return None

    captured = {}

    def fake_process(_db, *, limit):
        captured["limit"] = limit
        return {"processed": 1, "notified": 1, "escalated": 0, "missing_recipients": 0}

    monkeypatch.setattr(task_module, "SessionLocal", FakeSession)
    monkeypatch.setattr(
        "app.services.crm.inbox.response_obligations.process_due_response_obligations",
        fake_process,
    )
    monkeypatch.setattr("app.metrics.observe_job", lambda *_args: None)

    result = task_module.send_reply_reminders_task.run(limit=17)

    assert captured == {"limit": 17}
    assert result["notified"] == 1

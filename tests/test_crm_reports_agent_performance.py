from datetime import UTC, datetime, timedelta

from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection
from app.models.crm.team import CrmAgent
from app.models.person import Person
from app.services.crm.reports import agent_performance_metrics


def test_agent_performance_metrics_computes_metrics_from_batched_message_queries(db_session):
    now = datetime.now(UTC)

    contact = Person(first_name="Contact", last_name="One", email="contact1@example.com")
    agent_person = Person(first_name="Ada", last_name="Agent", email="agent1@example.com")
    db_session.add_all([contact, agent_person])
    db_session.flush()

    agent = CrmAgent(person_id=agent_person.id, is_active=True)
    db_session.add(agent)
    db_session.flush()

    convo_open = Conversation(
        person_id=contact.id,
        status=ConversationStatus.open,
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=1),
    )
    convo_resolved = Conversation(
        person_id=contact.id,
        status=ConversationStatus.resolved,
        created_at=now - timedelta(hours=3),
        updated_at=now - timedelta(minutes=30),
    )
    db_session.add_all([convo_open, convo_resolved])
    db_session.flush()

    db_session.add_all(
        [
            ConversationAssignment(
                agent_id=agent.id,
                conversation_id=convo_open.id,
                assigned_at=now - timedelta(hours=2),
                is_active=True,
            ),
            ConversationAssignment(
                agent_id=agent.id,
                conversation_id=convo_resolved.id,
                assigned_at=now - timedelta(hours=3),
                is_active=True,
            ),
        ]
    )

    # Open conversation: first response in 10 minutes.
    db_session.add_all(
        [
            Message(
                conversation_id=convo_open.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
                body="inbound",
                received_at=now - timedelta(hours=2),
                created_at=now - timedelta(hours=2),
            ),
            Message(
                conversation_id=convo_open.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.outbound,
                body="outbound",
                sent_at=now - timedelta(hours=1, minutes=50),
                created_at=now - timedelta(hours=1, minutes=50),
                author_id=agent_person.id,
            ),
        ]
    )

    # Resolved conversation: first response in 20 minutes.
    db_session.add_all(
        [
            Message(
                conversation_id=convo_resolved.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
                body="inbound2",
                received_at=now - timedelta(hours=3),
                created_at=now - timedelta(hours=3),
            ),
            Message(
                conversation_id=convo_resolved.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.outbound,
                body="outbound2",
                sent_at=now - timedelta(hours=2, minutes=40),
                created_at=now - timedelta(hours=2, minutes=40),
                author_id=agent_person.id,
            ),
        ]
    )

    db_session.commit()

    rows = agent_performance_metrics(
        db=db_session,
        start_at=None,
        end_at=None,
        agent_id=str(agent.id),
        team_id=None,
        channel_type="email",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["total_conversations"] == 2
    assert row["resolved_conversations"] == 1
    assert row["avg_first_response_minutes"] == 15.0
    assert row["avg_resolution_minutes"] is not None


def test_agent_performance_counts_message_activity_for_existing_conversations(db_session, monkeypatch):
    now = datetime.now(UTC)
    start_at = now - timedelta(days=1)

    contact = Person(first_name="Contact", last_name="Two", email="contact2@example.com")
    agent_person = Person(first_name="Bea", last_name="Agent", email="agent2@example.com")
    db_session.add_all([contact, agent_person])
    db_session.flush()

    agent = CrmAgent(person_id=agent_person.id, is_active=True)
    db_session.add(agent)
    db_session.flush()

    old_convo = Conversation(
        person_id=contact.id,
        status=ConversationStatus.resolved,
        created_at=now - timedelta(days=10),
        resolved_at=now - timedelta(hours=1),
        updated_at=now - timedelta(hours=1),
    )
    db_session.add(old_convo)
    db_session.flush()

    db_session.add(
        ConversationAssignment(
            agent_id=agent.id,
            conversation_id=old_convo.id,
            assigned_at=now - timedelta(hours=2),
            is_active=True,
        )
    )
    db_session.add_all(
        [
            Message(
                conversation_id=old_convo.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.inbound,
                body="new inbound on old conversation",
                received_at=now - timedelta(hours=2),
                created_at=now - timedelta(hours=2),
            ),
            Message(
                conversation_id=old_convo.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.outbound,
                body="period response",
                sent_at=now - timedelta(hours=1, minutes=45),
                created_at=now - timedelta(hours=1, minutes=45),
                author_id=agent_person.id,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.crm.presence.agent_presence.seconds_by_status_bulk",
        lambda *args, **kwargs: {},
    )

    rows = agent_performance_metrics(
        db=db_session,
        start_at=start_at,
        end_at=now,
        agent_id=str(agent.id),
        team_id=None,
        channel_type="whatsapp",
    )

    assert len(rows) == 1
    assert rows[0]["total_conversations"] == 1
    assert rows[0]["resolved_conversations"] == 1
    assert rows[0]["avg_first_response_minutes"] == 15.0


def test_agent_performance_starts_response_and_resolution_at_ai_handoff(db_session, monkeypatch):
    now = datetime.now(UTC)
    start_at = now - timedelta(hours=1)

    contact = Person(first_name="Contact", last_name="AI", email="contact-ai@example.com")
    agent_person = Person(first_name="Cara", last_name="Agent", email="agent-ai@example.com")
    db_session.add_all([contact, agent_person])
    db_session.flush()

    agent = CrmAgent(person_id=agent_person.id, is_active=True)
    db_session.add(agent)
    db_session.flush()

    handoff_at = now - timedelta(minutes=5)
    conversation = Conversation(
        person_id=contact.id,
        status=ConversationStatus.resolved,
        created_at=now - timedelta(minutes=30),
        resolved_at=now - timedelta(minutes=1),
        updated_at=now - timedelta(minutes=1),
        metadata_={
            "ai_intake": {
                "status": "resolved",
                "resolved_at": handoff_at.isoformat(),
                "handoff_sent_at": handoff_at.isoformat(),
                "routing_assigned_agent_id": str(agent.id),
            }
        },
    )
    db_session.add(conversation)
    db_session.flush()

    db_session.add(
        ConversationAssignment(
            agent_id=agent.id,
            conversation_id=conversation.id,
            assigned_at=handoff_at,
            is_active=True,
        )
    )
    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.inbound,
                body="AI intake starts",
                received_at=now - timedelta(minutes=30),
                created_at=now - timedelta(minutes=30),
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.outbound,
                body="AI generated handoff",
                sent_at=handoff_at,
                created_at=handoff_at,
                metadata_={"ai_intake_generated": True},
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.outbound,
                body="Human reply",
                sent_at=now - timedelta(minutes=3),
                created_at=now - timedelta(minutes=3),
                author_id=agent_person.id,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.crm.presence.agent_presence.seconds_by_status_bulk",
        lambda *args, **kwargs: {},
    )

    rows = agent_performance_metrics(
        db=db_session,
        start_at=start_at,
        end_at=now,
        agent_id=str(agent.id),
        team_id=None,
        channel_type="whatsapp",
    )

    assert len(rows) == 1
    assert rows[0]["total_conversations"] == 1
    assert rows[0]["resolved_conversations"] == 1
    assert rows[0]["avg_first_response_minutes"] == 2.0
    assert rows[0]["avg_resolution_minutes"] == 4.0
    assert rows[0]["first_response_count"] == 1
    assert rows[0]["resolution_time_count"] == 1


def test_agent_performance_counts_agent_reply_before_late_assignment(db_session, monkeypatch):
    now = datetime.now(UTC)
    start_at = now - timedelta(days=1)

    contact = Person(first_name="Contact", last_name="Late", email="contact-late@example.com")
    agent_person = Person(first_name="Shallom", last_name="Agent", email="agent-late@example.com")
    db_session.add_all([contact, agent_person])
    db_session.flush()

    agent = CrmAgent(person_id=agent_person.id, is_active=True)
    db_session.add(agent)
    db_session.flush()

    inbound_at = now - timedelta(minutes=50)
    first_reply_at = now - timedelta(minutes=2)
    assigned_at = now
    conversation = Conversation(
        person_id=contact.id,
        status=ConversationStatus.resolved,
        created_at=inbound_at,
        first_assigned_at=assigned_at,
        resolved_at=now,
        updated_at=now,
        metadata_={
            "ai_intake": {
                "status": "escalated",
                "turn_count": 0,
                "reason": "ai_error:AIClientError",
                "handoff_sent_at": assigned_at.isoformat(),
            }
        },
    )
    db_session.add(conversation)
    db_session.flush()

    db_session.add(
        ConversationAssignment(
            agent_id=agent.id,
            conversation_id=conversation.id,
            assigned_at=assigned_at,
            is_active=True,
        )
    )
    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.inbound,
                body="Internet not working",
                received_at=inbound_at,
                created_at=inbound_at,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.outbound,
                body="We are checking this.",
                sent_at=first_reply_at,
                created_at=first_reply_at,
                author_id=agent_person.id,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.outbound,
                body="Thanks for chatting with us today.\n\nFollow us for updates:",
                sent_at=now + timedelta(days=3),
                created_at=now + timedelta(days=3),
                author_id=agent_person.id,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.crm.presence.agent_presence.seconds_by_status_bulk",
        lambda *args, **kwargs: {},
    )

    rows = agent_performance_metrics(
        db=db_session,
        start_at=start_at,
        end_at=now + timedelta(days=4),
        agent_id=str(agent.id),
        team_id=None,
        channel_type="whatsapp",
    )

    assert len(rows) == 1
    assert rows[0]["avg_first_response_minutes"] == 48.0


def test_agent_performance_starts_at_assignment_when_customer_waited_before_assignment(db_session, monkeypatch):
    now = datetime.now(UTC)
    start_at = now - timedelta(days=3)

    contact = Person(first_name="Contact", last_name="Assigned", email="contact-assigned@example.com")
    agent_person = Person(first_name="Shallom", last_name="Assigned", email="agent-assigned@example.com")
    db_session.add_all([contact, agent_person])
    db_session.flush()

    agent = CrmAgent(person_id=agent_person.id, is_active=True)
    db_session.add(agent)
    db_session.flush()

    inbound_at = now - timedelta(days=2)
    assigned_at = now - timedelta(minutes=35)
    first_reply_at = now
    conversation = Conversation(
        person_id=contact.id,
        status=ConversationStatus.resolved,
        created_at=inbound_at,
        first_assigned_at=assigned_at,
        resolved_at=now,
        updated_at=now,
    )
    db_session.add(conversation)
    db_session.flush()

    db_session.add(
        ConversationAssignment(
            agent_id=agent.id,
            conversation_id=conversation.id,
            assigned_at=assigned_at,
            is_active=True,
        )
    )
    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
                body="Still down",
                received_at=inbound_at,
                created_at=inbound_at,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.outbound,
                body="Checking now.",
                sent_at=first_reply_at,
                created_at=first_reply_at,
                author_id=agent_person.id,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.crm.presence.agent_presence.seconds_by_status_bulk",
        lambda *args, **kwargs: {},
    )

    rows = agent_performance_metrics(
        db=db_session,
        start_at=start_at,
        end_at=now + timedelta(minutes=1),
        agent_id=str(agent.id),
        team_id=None,
        channel_type="email",
    )

    assert len(rows) == 1
    assert rows[0]["avg_first_response_minutes"] == 35.0


def test_agent_performance_attributes_first_response_to_first_human_responder(db_session, monkeypatch):
    now = datetime.now(UTC)
    start_at = now - timedelta(days=1)

    contact = Person(first_name="Contact", last_name="Reassigned", email="contact-reassigned@example.com")
    first_responder_person = Person(first_name="David", last_name="Dikko", email="david-reassigned@example.com")
    final_agent_person = Person(first_name="Shallom", last_name="Chukwueke", email="shallom-reassigned@example.com")
    db_session.add_all([contact, first_responder_person, final_agent_person])
    db_session.flush()

    first_responder = CrmAgent(person_id=first_responder_person.id, is_active=True)
    final_agent = CrmAgent(person_id=final_agent_person.id, is_active=True)
    db_session.add_all([first_responder, final_agent])
    db_session.flush()

    first_inbound_at = now - timedelta(hours=2)
    first_responder_assigned_at = now - timedelta(minutes=110)
    first_human_reply_at = first_responder_assigned_at + timedelta(minutes=42)
    final_agent_assigned_at = now - timedelta(minutes=64)
    final_agent_reply_at = now
    conversation = Conversation(
        person_id=contact.id,
        status=ConversationStatus.resolved,
        created_at=first_inbound_at,
        first_assigned_at=first_responder_assigned_at,
        resolved_at=now,
        updated_at=now,
    )
    db_session.add(conversation)
    db_session.flush()

    db_session.add_all(
        [
            ConversationAssignment(
                agent_id=first_responder.id,
                conversation_id=conversation.id,
                assigned_at=first_responder_assigned_at,
                is_active=False,
            ),
            ConversationAssignment(
                agent_id=final_agent.id,
                conversation_id=conversation.id,
                assigned_at=final_agent_assigned_at,
                is_active=True,
            ),
        ]
    )
    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
                body="Internet is down",
                received_at=first_inbound_at,
                created_at=first_inbound_at,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.outbound,
                body="We are checking this.",
                sent_at=first_human_reply_at,
                created_at=first_human_reply_at,
                author_id=first_responder_person.id,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
                body="Any update?",
                received_at=final_agent_assigned_at,
                created_at=final_agent_assigned_at,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.outbound,
                body="The service is restored now.",
                sent_at=final_agent_reply_at,
                created_at=final_agent_reply_at,
                author_id=final_agent_person.id,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.crm.presence.agent_presence.seconds_by_status_bulk",
        lambda *args, **kwargs: {},
    )

    rows = agent_performance_metrics(
        db=db_session,
        start_at=start_at,
        end_at=now + timedelta(minutes=1),
        agent_id=None,
        team_id=None,
        channel_type="email",
    )
    rows_by_agent_id = {row["agent_id"]: row for row in rows}

    assert rows_by_agent_id[str(first_responder.id)]["avg_first_response_minutes"] == 42.0
    assert rows_by_agent_id[str(first_responder.id)]["first_response_count"] == 1
    assert rows_by_agent_id[str(final_agent.id)]["avg_first_response_minutes"] is None
    assert rows_by_agent_id[str(final_agent.id)]["first_response_count"] == 0


def test_agent_performance_first_responder_scope_matches_filtered_view(db_session, monkeypatch):
    now = datetime.now(UTC)
    start_at = now - timedelta(days=1)

    contact = Person(first_name="Contact", last_name="Scope", email="contact-scope@example.com")
    first_responder_person = Person(first_name="Chris", last_name="Mbah", email="chris-scope@example.com")
    other_agent_person = Person(first_name="Other", last_name="Agent", email="other-scope@example.com")
    db_session.add_all([contact, first_responder_person, other_agent_person])
    db_session.flush()

    first_responder = CrmAgent(person_id=first_responder_person.id, is_active=True)
    other_agent = CrmAgent(person_id=other_agent_person.id, is_active=True)
    db_session.add_all([first_responder, other_agent])
    db_session.flush()

    inbound_at = now - timedelta(hours=3)
    first_responder_assigned_at = now - timedelta(hours=2, minutes=30)
    first_reply_at = first_responder_assigned_at + timedelta(minutes=19)
    other_agent_assigned_at = now - timedelta(hours=1)
    conversation = Conversation(
        person_id=contact.id,
        status=ConversationStatus.resolved,
        created_at=inbound_at - timedelta(minutes=10),
        first_assigned_at=first_responder_assigned_at,
        resolved_at=now,
        updated_at=now,
    )
    db_session.add(conversation)
    db_session.flush()

    db_session.add_all(
        [
            ConversationAssignment(
                agent_id=first_responder.id,
                conversation_id=conversation.id,
                assigned_at=first_responder_assigned_at,
                is_active=False,
            ),
            ConversationAssignment(
                agent_id=other_agent.id,
                conversation_id=conversation.id,
                assigned_at=other_agent_assigned_at,
                is_active=True,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.outbound,
                body="Earlier note before the customer waited",
                sent_at=inbound_at - timedelta(minutes=5),
                created_at=inbound_at - timedelta(minutes=5),
                author_id=other_agent_person.id,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.inbound,
                body="Need support",
                received_at=inbound_at,
                created_at=inbound_at,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.outbound,
                body="I will check this.",
                sent_at=first_reply_at,
                created_at=first_reply_at,
                author_id=first_responder_person.id,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.outbound,
                body="Resolved.",
                sent_at=now - timedelta(minutes=10),
                created_at=now - timedelta(minutes=10),
                author_id=other_agent_person.id,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.crm.presence.agent_presence.seconds_by_status_bulk",
        lambda *args, **kwargs: {},
    )

    all_rows = agent_performance_metrics(
        db=db_session,
        start_at=start_at,
        end_at=now + timedelta(minutes=1),
        agent_id=None,
        team_id=None,
        channel_type="whatsapp",
    )
    filtered_rows = agent_performance_metrics(
        db=db_session,
        start_at=start_at,
        end_at=now + timedelta(minutes=1),
        agent_id=str(first_responder.id),
        team_id=None,
        channel_type="whatsapp",
    )
    all_row = {row["agent_id"]: row for row in all_rows}[str(first_responder.id)]
    filtered_row = filtered_rows[0]

    assert all_row["total_conversations"] == filtered_row["total_conversations"] == 1
    assert all_row["avg_first_response_minutes"] == filtered_row["avg_first_response_minutes"] == 19.0
    assert all_row["first_response_count"] == filtered_row["first_response_count"] == 1


def test_agent_performance_does_not_attribute_unassigned_first_human_reply(db_session, monkeypatch):
    now = datetime.now(UTC)
    start_at = now - timedelta(days=1)

    contact = Person(first_name="Contact", last_name="Unassigned", email="contact-unassigned@example.com")
    unassigned_person = Person(first_name="Chinelo", last_name="Okoro", email="chinelo-unassigned@example.com")
    assigned_person = Person(first_name="Assigned", last_name="Agent", email="assigned-after-unassigned@example.com")
    db_session.add_all([contact, unassigned_person, assigned_person])
    db_session.flush()

    unassigned_agent = CrmAgent(person_id=unassigned_person.id, is_active=True)
    assigned_agent = CrmAgent(person_id=assigned_person.id, is_active=True)
    db_session.add_all([unassigned_agent, assigned_agent])
    db_session.flush()

    inbound_at = now - timedelta(hours=4)
    unassigned_reply_at = inbound_at + timedelta(minutes=20)
    assigned_at = inbound_at + timedelta(hours=1)
    assigned_reply_at = assigned_at + timedelta(minutes=15)
    conversation = Conversation(
        person_id=contact.id,
        status=ConversationStatus.resolved,
        created_at=inbound_at,
        first_assigned_at=assigned_at,
        resolved_at=now,
        updated_at=now,
    )
    db_session.add(conversation)
    db_session.flush()

    db_session.add(
        ConversationAssignment(
            agent_id=assigned_agent.id,
            conversation_id=conversation.id,
            assigned_at=assigned_at,
            is_active=True,
        )
    )
    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
                body="Need help",
                received_at=inbound_at,
                created_at=inbound_at,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.outbound,
                body="Unassigned human reply",
                sent_at=unassigned_reply_at,
                created_at=unassigned_reply_at,
                author_id=unassigned_person.id,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.outbound,
                body="Assigned follow-up",
                sent_at=assigned_reply_at,
                created_at=assigned_reply_at,
                author_id=assigned_person.id,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.crm.presence.agent_presence.seconds_by_status_bulk",
        lambda *args, **kwargs: {},
    )

    rows = agent_performance_metrics(
        db=db_session,
        start_at=start_at,
        end_at=now + timedelta(minutes=1),
        agent_id=None,
        team_id=None,
        channel_type="email",
    )
    rows_by_agent_id = {row["agent_id"]: row for row in rows}

    assert rows_by_agent_id[str(unassigned_agent.id)]["avg_first_response_minutes"] is None
    assert rows_by_agent_id[str(unassigned_agent.id)]["first_response_count"] == 0
    assert rows_by_agent_id[str(assigned_agent.id)]["avg_first_response_minutes"] is None
    assert rows_by_agent_id[str(assigned_agent.id)]["first_response_count"] == 0


def test_agent_performance_ignores_resolved_closing_message_as_first_response(db_session, monkeypatch):
    now = datetime.now(UTC)
    start_at = now - timedelta(days=1)

    contact = Person(first_name="Contact", last_name="Close", email="contact-close@example.com")
    agent_person = Person(first_name="Close", last_name="Agent", email="agent-close@example.com")
    db_session.add_all([contact, agent_person])
    db_session.flush()

    agent = CrmAgent(person_id=agent_person.id, is_active=True)
    db_session.add(agent)
    db_session.flush()

    inbound_at = now - timedelta(minutes=30)
    closing_at = now - timedelta(minutes=5)
    conversation = Conversation(
        person_id=contact.id,
        status=ConversationStatus.resolved,
        created_at=inbound_at,
        resolved_at=closing_at,
        updated_at=closing_at,
    )
    db_session.add(conversation)
    db_session.flush()

    closing_message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.whatsapp,
        direction=MessageDirection.outbound,
        body="Thanks for chatting with us today.\n\nFollow us for updates:",
        sent_at=closing_at,
        created_at=closing_at,
        author_id=agent_person.id,
    )
    db_session.add_all(
        [
            ConversationAssignment(
                agent_id=agent.id,
                conversation_id=conversation.id,
                assigned_at=inbound_at,
                is_active=True,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.inbound,
                body="Need help",
                received_at=inbound_at,
                created_at=inbound_at,
            ),
            closing_message,
        ]
    )
    db_session.flush()
    conversation.metadata_ = {"resolved_closing_message": {"message_id": str(closing_message.id)}}
    db_session.commit()
    monkeypatch.setattr(
        "app.services.crm.presence.agent_presence.seconds_by_status_bulk",
        lambda *args, **kwargs: {},
    )

    rows = agent_performance_metrics(
        db=db_session,
        start_at=start_at,
        end_at=now,
        agent_id=str(agent.id),
        team_id=None,
        channel_type="whatsapp",
    )

    assert len(rows) == 1
    assert rows[0]["first_response_count"] == 0
    assert rows[0]["avg_first_response_minutes"] is None
    assert rows[0]["resolution_time_count"] == 1


def test_agent_performance_ignores_outbound_from_non_assigned_agent(db_session, monkeypatch):
    now = datetime.now(UTC)
    start_at = now - timedelta(hours=1)

    contact = Person(first_name="Contact", last_name="Owner", email="contact-owner@example.com")
    assigned_person = Person(first_name="Assigned", last_name="Agent", email="assigned@example.com")
    other_person = Person(first_name="Other", last_name="Agent", email="other@example.com")
    db_session.add_all([contact, assigned_person, other_person])
    db_session.flush()

    assigned_agent = CrmAgent(person_id=assigned_person.id, is_active=True)
    other_agent = CrmAgent(person_id=other_person.id, is_active=True)
    db_session.add_all([assigned_agent, other_agent])
    db_session.flush()

    assigned_at = now - timedelta(minutes=10)
    conversation = Conversation(
        person_id=contact.id,
        status=ConversationStatus.open,
        created_at=now - timedelta(minutes=20),
        updated_at=now - timedelta(minutes=2),
    )
    db_session.add(conversation)
    db_session.flush()
    db_session.add(
        ConversationAssignment(
            agent_id=assigned_agent.id,
            conversation_id=conversation.id,
            assigned_at=assigned_at,
            is_active=True,
        )
    )
    db_session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
                body="Need help",
                received_at=now - timedelta(minutes=20),
                created_at=now - timedelta(minutes=20),
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.outbound,
                body="Reply from someone else",
                sent_at=now - timedelta(minutes=2),
                created_at=now - timedelta(minutes=2),
                author_id=other_person.id,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(
        "app.services.crm.presence.agent_presence.seconds_by_status_bulk",
        lambda *args, **kwargs: {},
    )

    rows = agent_performance_metrics(
        db=db_session,
        start_at=start_at,
        end_at=now,
        agent_id=str(assigned_agent.id),
        team_id=None,
        channel_type="email",
    )

    assert len(rows) == 1
    assert rows[0]["total_conversations"] == 1
    assert rows[0]["avg_first_response_minutes"] is None
    assert rows[0]["first_response_count"] == 0

from datetime import UTC, datetime, timedelta

from starlette.requests import Request

from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection
from app.models.crm.team import CrmAgent
from app.models.person import Person
from app.services.crm.reports import agent_performance_metrics, crm_performance_summary


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


def test_agent_performance_uses_assignment_start_not_old_customer_inbound(db_session, monkeypatch):
    now = datetime.now(UTC)
    start_at = now - timedelta(days=1)

    contact = Person(first_name="Contact", last_name="Late", email="contact-late@example.com")
    agent_person = Person(first_name="Shallom", last_name="Agent", email="agent-late@example.com")
    db_session.add_all([contact, agent_person])
    db_session.flush()

    agent = CrmAgent(person_id=agent_person.id, is_active=True)
    db_session.add(agent)
    db_session.flush()

    inbound_at = now - timedelta(days=2)
    first_reply_at = now - timedelta(minutes=2)
    assigned_at = first_reply_at - timedelta(minutes=2, seconds=12)
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
    assert rows[0]["avg_first_response_minutes"] == 2.2
    assert rows[0]["total_assignments"] == 1
    assert rows[0]["first_response_count"] == 1
    assert rows[0]["unanswered_assignments"] == 0
    assert rows[0]["response_coverage_percent"] == 100.0


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


def test_crm_performance_summary_counts_reassigned_conversation_once(db_session, monkeypatch):
    now = datetime.now(UTC)
    start_at = now - timedelta(days=1)
    monkeypatch.setattr(
        "app.services.crm.presence.agent_presence.seconds_by_status_bulk",
        lambda *args, **kwargs: {},
    )

    contact = Person(first_name="Shared", last_name="Contact", email="shared-contact@example.com")
    agent_a_person = Person(first_name="Agent", last_name="A", email="agent-a@example.com")
    agent_b_person = Person(first_name="Agent", last_name="B", email="agent-b@example.com")
    db_session.add_all([contact, agent_a_person, agent_b_person])
    db_session.flush()

    agent_a = CrmAgent(person_id=agent_a_person.id, is_active=True)
    agent_b = CrmAgent(person_id=agent_b_person.id, is_active=True)
    db_session.add_all([agent_a, agent_b])
    db_session.flush()

    conversation = Conversation(
        person_id=contact.id,
        status=ConversationStatus.open,
        created_at=now - timedelta(hours=4),
        updated_at=now - timedelta(minutes=5),
    )
    db_session.add(conversation)
    db_session.flush()

    db_session.add_all(
        [
            ConversationAssignment(
                agent_id=agent_a.id,
                conversation_id=conversation.id,
                assigned_at=now - timedelta(hours=3),
                ended_at=now - timedelta(hours=2),
                is_active=False,
            ),
            ConversationAssignment(
                agent_id=agent_b.id,
                conversation_id=conversation.id,
                assigned_at=now - timedelta(hours=1),
                is_active=True,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.inbound,
                body="Need support",
                received_at=now - timedelta(hours=3),
                created_at=now - timedelta(hours=3),
            ),
        ]
    )
    db_session.commit()

    summary = crm_performance_summary(
        db=db_session,
        start_at=start_at,
        end_at=now,
        agent_id=None,
        team_id=None,
        channel_type="whatsapp",
    )
    rows = agent_performance_metrics(
        db=db_session,
        start_at=start_at,
        end_at=now,
        agent_id=None,
        team_id=None,
        channel_type="whatsapp",
    )

    assert summary["total_conversations"] == 1
    assert summary["resolved_conversations"] == 0
    assert sum(row["total_conversations"] for row in rows) == 2
    assert sum(int(row["total_assignments"]) for row in rows) == 2
    assert sorted(row["total_conversations"] for row in rows) == [1, 1]


def test_crm_performance_summary_counts_resolved_conversation_once_across_agents(db_session, monkeypatch):
    now = datetime.now(UTC)
    start_at = now - timedelta(days=1)
    monkeypatch.setattr(
        "app.services.crm.presence.agent_presence.seconds_by_status_bulk",
        lambda *args, **kwargs: {},
    )

    contact = Person(first_name="Resolved", last_name="Contact", email="resolved-contact@example.com")
    agent_a_person = Person(first_name="Resolved", last_name="A", email="resolved-a@example.com")
    agent_b_person = Person(first_name="Resolved", last_name="B", email="resolved-b@example.com")
    db_session.add_all([contact, agent_a_person, agent_b_person])
    db_session.flush()

    agent_a = CrmAgent(person_id=agent_a_person.id, is_active=True)
    agent_b = CrmAgent(person_id=agent_b_person.id, is_active=True)
    db_session.add_all([agent_a, agent_b])
    db_session.flush()

    resolved_at = now - timedelta(minutes=10)
    conversation = Conversation(
        person_id=contact.id,
        status=ConversationStatus.resolved,
        created_at=now - timedelta(hours=5),
        resolved_at=resolved_at,
        updated_at=resolved_at,
    )
    db_session.add(conversation)
    db_session.flush()

    db_session.add_all(
        [
            ConversationAssignment(
                agent_id=agent_a.id,
                conversation_id=conversation.id,
                assigned_at=now - timedelta(hours=4),
                ended_at=now - timedelta(hours=2),
                is_active=False,
            ),
            ConversationAssignment(
                agent_id=agent_b.id,
                conversation_id=conversation.id,
                assigned_at=now - timedelta(hours=1),
                is_active=True,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
                body="Resolved case",
                received_at=now - timedelta(hours=4),
                created_at=now - timedelta(hours=4),
            ),
        ]
    )
    db_session.commit()

    summary = crm_performance_summary(
        db=db_session,
        start_at=start_at,
        end_at=now,
        agent_id=None,
        team_id=None,
        channel_type="email",
    )
    rows = agent_performance_metrics(
        db=db_session,
        start_at=start_at,
        end_at=now,
        agent_id=None,
        team_id=None,
        channel_type="email",
    )

    assert summary["total_conversations"] == 1
    assert summary["resolved_conversations"] == 1
    assert summary["resolution_rate"] == 100.0
    assert sum(row["resolved_conversations"] for row in rows) == 2


def test_crm_performance_summary_counts_multiple_stints_same_agent_once(db_session, monkeypatch):
    now = datetime.now(UTC)
    start_at = now - timedelta(days=1)
    monkeypatch.setattr(
        "app.services.crm.presence.agent_presence.seconds_by_status_bulk",
        lambda *args, **kwargs: {},
    )

    contact = Person(first_name="Repeat", last_name="Contact", email="repeat-contact@example.com")
    agent_person = Person(first_name="Repeat", last_name="Agent", email="repeat-agent@example.com")
    db_session.add_all([contact, agent_person])
    db_session.flush()

    agent = CrmAgent(person_id=agent_person.id, is_active=True)
    db_session.add(agent)
    db_session.flush()

    conversation = Conversation(
        person_id=contact.id,
        status=ConversationStatus.open,
        created_at=now - timedelta(hours=6),
        updated_at=now - timedelta(minutes=20),
    )
    db_session.add(conversation)
    db_session.flush()

    db_session.add_all(
        [
            ConversationAssignment(
                agent_id=agent.id,
                conversation_id=conversation.id,
                assigned_at=now - timedelta(hours=5),
                ended_at=now - timedelta(hours=4),
                is_active=False,
            ),
            ConversationAssignment(
                agent_id=agent.id,
                conversation_id=conversation.id,
                assigned_at=now - timedelta(hours=2),
                is_active=True,
            ),
            Message(
                conversation_id=conversation.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
                body="Same agent twice",
                received_at=now - timedelta(hours=5),
                created_at=now - timedelta(hours=5),
            ),
        ]
    )
    db_session.commit()

    summary = crm_performance_summary(
        db=db_session,
        start_at=start_at,
        end_at=now,
        agent_id=str(agent.id),
        team_id=None,
        channel_type="email",
    )
    rows = agent_performance_metrics(
        db=db_session,
        start_at=start_at,
        end_at=now,
        agent_id=str(agent.id),
        team_id=None,
        channel_type="email",
    )

    assert summary["total_conversations"] == 1
    assert rows[0]["total_conversations"] == 1
    assert rows[0]["total_assignments"] == 2


def test_crm_performance_summary_preserves_agent_team_date_and_channel_filters(db_session, monkeypatch):
    now = datetime.now(UTC)
    start_at = now - timedelta(days=1)
    monkeypatch.setattr(
        "app.services.crm.presence.agent_presence.seconds_by_status_bulk",
        lambda *args, **kwargs: {},
    )

    from app.models.crm.team import CrmAgentTeam, CrmTeam

    contact = Person(first_name="Scoped", last_name="Contact", email="scoped-contact@example.com")
    agent_person = Person(first_name="Scoped", last_name="Agent", email="scoped-agent@example.com")
    other_person = Person(first_name="Other", last_name="Agent", email="other-scoped-agent@example.com")
    db_session.add_all([contact, agent_person, other_person])
    db_session.flush()

    scoped_agent = CrmAgent(person_id=agent_person.id, is_active=True)
    other_agent = CrmAgent(person_id=other_person.id, is_active=True)
    team_a = CrmTeam(name="Team A", is_active=True)
    team_b = CrmTeam(name="Team B", is_active=True)
    db_session.add_all([scoped_agent, other_agent, team_a, team_b])
    db_session.flush()
    db_session.add_all(
        [
            CrmAgentTeam(agent_id=scoped_agent.id, team_id=team_a.id, is_active=True),
            CrmAgentTeam(agent_id=other_agent.id, team_id=team_b.id, is_active=True),
        ]
    )

    included = Conversation(
        person_id=contact.id,
        status=ConversationStatus.resolved,
        created_at=now - timedelta(hours=12),
        resolved_at=now - timedelta(hours=1),
        updated_at=now - timedelta(hours=1),
    )
    wrong_channel = Conversation(
        person_id=contact.id,
        status=ConversationStatus.resolved,
        created_at=now - timedelta(hours=12),
        resolved_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=2),
    )
    wrong_team = Conversation(
        person_id=contact.id,
        status=ConversationStatus.resolved,
        created_at=now - timedelta(hours=12),
        resolved_at=now - timedelta(hours=3),
        updated_at=now - timedelta(hours=3),
    )
    outside_range = Conversation(
        person_id=contact.id,
        status=ConversationStatus.resolved,
        created_at=now - timedelta(days=3),
        resolved_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=2),
    )
    db_session.add_all([included, wrong_channel, wrong_team, outside_range])
    db_session.flush()

    db_session.add_all(
        [
            ConversationAssignment(
                agent_id=scoped_agent.id,
                conversation_id=included.id,
                assigned_at=now - timedelta(hours=6),
                is_active=True,
            ),
            ConversationAssignment(
                agent_id=scoped_agent.id,
                conversation_id=wrong_channel.id,
                assigned_at=now - timedelta(hours=5),
                is_active=True,
            ),
            ConversationAssignment(
                agent_id=other_agent.id,
                conversation_id=wrong_team.id,
                assigned_at=now - timedelta(hours=4),
                is_active=True,
            ),
            ConversationAssignment(
                agent_id=scoped_agent.id,
                conversation_id=outside_range.id,
                assigned_at=now - timedelta(days=2),
                is_active=True,
            ),
            Message(
                conversation_id=included.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.inbound,
                body="Included",
                received_at=now - timedelta(hours=6),
                created_at=now - timedelta(hours=6),
            ),
            Message(
                conversation_id=wrong_channel.id,
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
                body="Wrong channel",
                received_at=now - timedelta(hours=5),
                created_at=now - timedelta(hours=5),
            ),
            Message(
                conversation_id=wrong_team.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.inbound,
                body="Wrong team",
                received_at=now - timedelta(hours=4),
                created_at=now - timedelta(hours=4),
            ),
            Message(
                conversation_id=outside_range.id,
                channel_type=ChannelType.whatsapp,
                direction=MessageDirection.inbound,
                body="Outside range",
                received_at=now - timedelta(days=2),
                created_at=now - timedelta(days=2),
            ),
        ]
    )
    db_session.commit()

    summary = crm_performance_summary(
        db=db_session,
        start_at=start_at,
        end_at=now,
        agent_id=str(scoped_agent.id),
        team_id=str(team_a.id),
        channel_type="whatsapp",
    )

    assert summary["total_conversations"] == 1
    assert summary["resolved_conversations"] == 1
    assert summary["resolution_rate"] == 100.0


def test_crm_performance_report_uses_unique_summary_totals(db_session, monkeypatch):
    from app.web.admin import reports as reports_web

    monkeypatch.setattr(reports_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(reports_web, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(reports_web.crm_team_service.Teams, "list", lambda *args, **_kwargs: [])
    monkeypatch.setattr(reports_web.crm_team_service.Agents, "list", lambda *args, **_kwargs: [])
    monkeypatch.setattr(reports_web.crm_team_service, "get_agent_labels", lambda _db, _agents: {})
    monkeypatch.setattr(
        reports_web.crm_reports_service,
        "inbox_kpis",
        lambda **_kwargs: {"messages": {"total": 0, "inbound": 0, "outbound": 0, "by_channel": {}}},
    )
    monkeypatch.setattr(
        reports_web.crm_reports_service,
        "agent_performance_metrics",
        lambda **_kwargs: [
            {
                "agent_id": "a",
                "name": "Agent A",
                "total_conversations": 1,
                "total_assignments": 1,
                "first_response_count": 1,
                "unanswered_assignments": 0,
                "response_coverage_percent": 100.0,
                "resolved_conversations": 1,
                "avg_first_response_minutes": 5.0,
                "avg_resolution_minutes": 20.0,
                "resolution_time_count": 1,
                "active_hours_display": "1h 00m",
            },
            {
                "agent_id": "b",
                "name": "Agent B",
                "total_conversations": 1,
                "total_assignments": 1,
                "first_response_count": 1,
                "unanswered_assignments": 0,
                "response_coverage_percent": 100.0,
                "resolved_conversations": 1,
                "avg_first_response_minutes": 10.0,
                "avg_resolution_minutes": 25.0,
                "resolution_time_count": 1,
                "active_hours_display": "1h 00m",
            },
        ],
    )
    monkeypatch.setattr(
        reports_web.crm_reports_service,
        "crm_performance_summary",
        lambda **_kwargs: {
            "total_conversations": 1,
            "resolved_conversations": 1,
            "resolution_rate": 100.0,
        },
    )
    monkeypatch.setattr(reports_web.crm_reports_service, "conversation_trend", lambda **_kwargs: [])

    request = Request({"type": "http", "method": "GET", "path": "/admin/reports/crm-performance", "headers": []})
    response = reports_web.crm_performance_report(
        request=request,
        db=db_session,
        days=30,
        start_date=None,
        end_date=None,
        agent_id=None,
        team_id=None,
        channel_type=None,
    )

    assert response.context["total_conversations"] == 1
    assert response.context["resolved_conversations"] == 1
    assert response.context["resolution_rate"] == 100.0
    assert response.context["total_assignments"] == 2

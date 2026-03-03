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
            ConversationAssignment(agent_id=agent.id, conversation_id=convo_open.id, is_active=True),
            ConversationAssignment(agent_id=agent.id, conversation_id=convo_resolved.id, is_active=True),
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

from app.models.crm.conversation import Conversation, ConversationTag
from app.models.crm.enums import AgentPresenceStatus, ConversationPriority, ConversationStatus
from app.models.crm.presence import AgentPresence
from app.models.crm.team import CrmAgent
from app.models.person import Person
from app.services.crm.inbox.bulk_actions import apply_bulk_action


def _make_person(db_session, email: str) -> Person:
    person = Person(first_name="Bulk", last_name="User", email=email)
    db_session.add(person)
    db_session.flush()
    return person


def _make_conversation(db_session, person: Person) -> Conversation:
    conversation = Conversation(person_id=person.id, status=ConversationStatus.open)
    db_session.add(conversation)
    db_session.flush()
    return conversation


def test_bulk_status_updates_multiple_conversations(db_session):
    person = _make_person(db_session, "bulk-status@example.com")
    conv_a = _make_conversation(db_session, person)
    conv_b = _make_conversation(db_session, person)

    result = apply_bulk_action(
        db_session,
        conversation_ids=[str(conv_a.id), str(conv_b.id)],
        action="status:pending",
        actor_id=str(person.id),
    )

    assert result.kind == "success"
    assert result.applied == 2
    db_session.refresh(conv_a)
    db_session.refresh(conv_b)
    assert conv_a.status == ConversationStatus.pending
    assert conv_b.status == ConversationStatus.pending


def test_bulk_priority_updates_multiple_conversations(db_session):
    person = _make_person(db_session, "bulk-priority@example.com")
    conv_a = _make_conversation(db_session, person)
    conv_b = _make_conversation(db_session, person)

    result = apply_bulk_action(
        db_session,
        conversation_ids=[str(conv_a.id), str(conv_b.id)],
        action="priority:high",
        actor_id=str(person.id),
    )

    assert result.kind == "success"
    assert result.applied == 2
    db_session.refresh(conv_a)
    db_session.refresh(conv_b)
    assert conv_a.priority == ConversationPriority.high
    assert conv_b.priority == ConversationPriority.high


def test_bulk_assign_me_applies_agent_assignment(db_session):
    person = _make_person(db_session, "bulk-assign@example.com")
    agent_person = _make_person(db_session, "bulk-agent@example.com")
    agent = CrmAgent(person_id=agent_person.id, title="Support")
    db_session.add(agent)
    db_session.flush()
    db_session.add(
        AgentPresence(
            agent_id=agent.id,
            status=AgentPresenceStatus.online,
            manual_override_status=AgentPresenceStatus.online,
        )
    )
    db_session.flush()

    conversation = _make_conversation(db_session, person)
    result = apply_bulk_action(
        db_session,
        conversation_ids=[str(conversation.id)],
        action="assign:me",
        actor_id=str(agent_person.id),
        current_agent_id=str(agent.id),
    )

    assert result.kind == "success"
    assert result.applied == 0
    assert result.failed == 1


def test_bulk_label_add_and_remove(db_session):
    person = _make_person(db_session, "bulk-label@example.com")
    conversation = _make_conversation(db_session, person)

    add_result = apply_bulk_action(
        db_session,
        conversation_ids=[str(conversation.id)],
        action="label:add",
        actor_id=str(person.id),
        label="vip",
    )
    assert add_result.kind == "success"
    assert add_result.applied == 1
    tag = (
        db_session.query(ConversationTag)
        .filter(ConversationTag.conversation_id == conversation.id)
        .filter(ConversationTag.tag == "vip")
        .first()
    )
    assert tag is not None

    remove_result = apply_bulk_action(
        db_session,
        conversation_ids=[str(conversation.id)],
        action="label:remove",
        actor_id=str(person.id),
        label="vip",
    )
    assert remove_result.kind == "success"
    assert remove_result.applied == 1
    assert (
        db_session.query(ConversationTag)
        .filter(ConversationTag.conversation_id == conversation.id)
        .filter(ConversationTag.tag == "vip")
        .first()
        is None
    )

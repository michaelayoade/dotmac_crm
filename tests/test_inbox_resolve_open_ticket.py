"""Open-ticket guardrails on inbox resolve.

Covers find_open_ticket_for_person, linking an unlinked ticket during
ticket handoff, and suppression of the "successfully resolved" closing
message when the contact still has an open ticket.
"""

from unittest.mock import Mock, patch
from uuid import uuid4

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ChannelType, ConversationStatus
from app.models.subscriber import Subscriber
from app.models.tickets import Ticket, TicketStatus
from app.services.crm.inbox.conversation_status import update_conversation_status
from app.services.crm.inbox.resolve_gate import (
    _ticket_belongs_to_conversation_contact,
    find_open_ticket_for_person,
)


def _ticket(db_session, *, customer_person_id=None, subscriber_id=None, status=TicketStatus.open, number=None):
    ticket = Ticket(
        title="Link down",
        customer_person_id=customer_person_id,
        subscriber_id=subscriber_id,
        status=status,
        number=number,
        is_active=True,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def test_find_open_ticket_matches_direct_customer_ticket(db_session, person):
    ticket = _ticket(db_session, customer_person_id=person.id)

    found = find_open_ticket_for_person(db_session, person_id=person.id)

    assert found is not None
    assert found.id == ticket.id


def test_find_open_ticket_ignores_terminal_tickets(db_session, person):
    _ticket(db_session, customer_person_id=person.id, status=TicketStatus.closed)
    _ticket(db_session, customer_person_id=person.id, status=TicketStatus.canceled)

    assert find_open_ticket_for_person(db_session, person_id=person.id) is None


def test_find_open_ticket_matches_via_subscriber(db_session, person):
    subscriber = Subscriber(person_id=person.id, subscriber_number="SUB-200", is_active=True)
    db_session.add(subscriber)
    db_session.flush()
    ticket = _ticket(db_session, subscriber_id=subscriber.id)

    found = find_open_ticket_for_person(db_session, person_id=person.id)

    assert found is not None
    assert found.id == ticket.id


def test_find_open_ticket_returns_none_for_unknown_person(db_session):
    assert find_open_ticket_for_person(db_session, person_id=uuid4()) is None
    assert find_open_ticket_for_person(db_session, person_id="not-a-uuid") is None


def test_ticket_ownership_check_rejects_other_customers_ticket(db_session, person, crm_contact):
    other_ticket = _ticket(db_session, customer_person_id=crm_contact.id)
    conversation = Conversation(person_id=person.id, status=ConversationStatus.open)
    db_session.add(conversation)
    db_session.commit()

    assert not _ticket_belongs_to_conversation_contact(db_session, conversation=conversation, ticket=other_ticket)

    own_ticket = _ticket(db_session, customer_person_id=person.id)
    assert _ticket_belongs_to_conversation_contact(db_session, conversation=conversation, ticket=own_ticket)


class _FakeConversation:
    def __init__(self):
        self.status = ConversationStatus.open
        self.metadata_ = {}
        self.created_at = None
        self.resolved_at = None
        self.resolution_time_seconds = None
        self.ticket_id = None
        self.person_id = uuid4()
        self.id = uuid4()


def test_plain_resolve_suppresses_closing_message_when_open_ticket_exists():
    conversation_id = str(uuid4())
    fake_conversation = _FakeConversation()
    db = Mock()
    db.get.return_value = fake_conversation
    open_ticket = Mock(number="22013", id=uuid4())

    with (
        patch("app.services.crm.inbox.conversation_status.conversation_service") as mock_service,
        patch("app.services.crm.inbox.conversation_status.log_conversation_action"),
        patch("app.services.crm.inbox.conversation_status.queue_for_resolved_conversation"),
        patch("app.services.crm.inbox.conversation_status._resolve_latest_channel_type") as mock_resolve_channel,
        patch("app.services.crm.inbox.resolve_gate.find_open_ticket_for_person", return_value=open_ticket),
        patch("app.services.crm.inbox.conversation_status._claim_resolved_closing_message_send") as mock_claim_send,
        patch(
            "app.services.crm.inbox.conversation_status._send_resolved_closing_message"
        ) as mock_send_resolved_closing,
        patch("app.services.crm.inbox.summaries.recompute_conversation_summary"),
    ):
        mock_service.Conversations.get.return_value = fake_conversation
        mock_resolve_channel.return_value = ChannelType.whatsapp

        result = update_conversation_status(
            db,
            conversation_id=conversation_id,
            new_status="resolved",
            actor_id="person-1",
        )

        assert result.kind == "updated"
        mock_claim_send.assert_not_called()
        mock_send_resolved_closing.assert_not_called()
        closing = fake_conversation.metadata_["resolved_closing_message"]
        assert closing["suppressed_reason"] == "open_ticket"
        assert closing["suppressed_ticket_reference"] == "22013"


def test_plain_resolve_sends_closing_message_when_no_open_ticket():
    conversation_id = str(uuid4())
    fake_conversation = _FakeConversation()
    db = Mock()
    db.get.return_value = fake_conversation

    with (
        patch("app.services.crm.inbox.conversation_status.conversation_service") as mock_service,
        patch("app.services.crm.inbox.conversation_status.log_conversation_action"),
        patch("app.services.crm.inbox.conversation_status.queue_for_resolved_conversation"),
        patch("app.services.crm.inbox.conversation_status._resolve_latest_channel_type") as mock_resolve_channel,
        patch("app.services.crm.inbox.resolve_gate.find_open_ticket_for_person", return_value=None),
        patch("app.services.crm.inbox.conversation_status._select_resolved_closing_variant") as mock_select_variant,
        patch("app.services.crm.inbox.conversation_status._claim_resolved_closing_message_send") as mock_claim_send,
        patch(
            "app.services.crm.inbox.conversation_status._send_resolved_closing_message"
        ) as mock_send_resolved_closing,
        patch("app.services.crm.inbox.conversation_status._persist_resolved_closing_message_metadata"),
        patch("app.services.crm.inbox.summaries.recompute_conversation_summary"),
    ):
        mock_service.Conversations.get.return_value = fake_conversation
        mock_resolve_channel.return_value = ChannelType.whatsapp
        mock_select_variant.return_value = "social"
        mock_claim_send.return_value = True
        mock_send_resolved_closing.return_value = (True, "msg-1", "whatsapp", None)

        result = update_conversation_status(
            db,
            conversation_id=conversation_id,
            new_status="resolved",
            actor_id="person-1",
        )

        assert result.kind == "updated"
        mock_send_resolved_closing.assert_called_once()

from app.models.crm.enums import ConversationStatus
from app.services.crm.inbox.status_flow import is_transition_allowed, validate_transition


class _Conversation:
    def __init__(self, status):
        self.status = status
        self.metadata_ = {}


def test_status_flow_blocks_resolved_to_open():
    assert not is_transition_allowed(ConversationStatus.resolved, ConversationStatus.open)
    check = validate_transition(ConversationStatus.resolved, ConversationStatus.open)
    assert not check.allowed
    assert check.reason


def test_status_flow_blocks_resolved_to_pending():
    assert not is_transition_allowed(ConversationStatus.resolved, ConversationStatus.pending)
    check = validate_transition(ConversationStatus.resolved, ConversationStatus.pending)
    assert not check.allowed
    assert check.reason


def test_status_flow_allows_active_to_resolved_to_ticket():
    assert is_transition_allowed(ConversationStatus.open, ConversationStatus.resolved_to_ticket)
    assert is_transition_allowed(ConversationStatus.pending, ConversationStatus.resolved_to_ticket)
    assert is_transition_allowed(ConversationStatus.snoozed, ConversationStatus.resolved_to_ticket)


def test_status_flow_allows_resolved_to_ticket_to_reopen():
    check = validate_transition(ConversationStatus.resolved_to_ticket, ConversationStatus.open)
    assert check.allowed


def test_status_flow_blocks_resolved_to_resolved_to_ticket():
    check = validate_transition(ConversationStatus.resolved, ConversationStatus.resolved_to_ticket)
    assert not check.allowed
    assert check.reason


def test_status_flow_clears_ticket_handoff_send_marker_on_reopen():
    from app.services.crm.inbox.status_flow import apply_status_transition

    conversation = _Conversation(ConversationStatus.resolved_to_ticket)
    conversation.metadata_ = {
        "resolved_closing_message": {
            "ticket_handoffs": {
                "ticket:ticket-1": {
                    "sent_at": "2026-06-05T12:46:52+00:00",
                    "message_id": "msg-1",
                }
            }
        },
        "resolution": {"mode": "ticket_handoff", "ticket_id": "ticket-1"},
    }

    check = apply_status_transition(conversation, ConversationStatus.open)

    assert check.allowed
    assert conversation.status == ConversationStatus.open
    assert "resolved_closing_message" not in conversation.metadata_
    assert conversation.metadata_["resolution"]["ticket_id"] == "ticket-1"

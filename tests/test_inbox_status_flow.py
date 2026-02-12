from app.models.crm.enums import ConversationStatus
from app.services.crm.inbox.status_flow import is_transition_allowed, validate_transition


def test_status_flow_allows_reopen():
    assert is_transition_allowed(ConversationStatus.resolved, ConversationStatus.open)


def test_status_flow_blocks_resolved_to_pending():
    assert not is_transition_allowed(ConversationStatus.resolved, ConversationStatus.pending)
    check = validate_transition(ConversationStatus.resolved, ConversationStatus.pending)
    assert not check.allowed
    assert check.reason

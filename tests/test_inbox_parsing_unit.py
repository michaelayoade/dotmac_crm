from app.models.crm.conversation import Conversation
from app.services.crm.inbox import parsing


def _create_conversation(db_session, person, subject=None):
    conversation = Conversation(person_id=person.id, subject=subject)
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)
    return conversation


def test_extract_message_ids_handles_angle_brackets():
    value = "<msg-1@example.com> <msg-2@example.com>"
    result = parsing._extract_message_ids(value)
    assert "msg-1@example.com" in result
    assert "msg-2@example.com" in result


def test_extract_conversation_tokens():
    tokens = parsing._extract_conversation_tokens("Re: conv_1234abcd ticket #abcdef12")
    assert "1234abcd" in tokens
    assert "abcdef12" in tokens


def test_find_conversation_by_token_uuid_prefix(db_session, person):
    conversation = _create_conversation(db_session, person)
    token = str(conversation.id).replace("-", "")[:8]
    found = parsing._find_conversation_by_token(db_session, token)
    assert found is not None
    assert found.id == conversation.id


def test_resolve_conversation_from_email_metadata_subject_token(db_session, person):
    conversation = _create_conversation(db_session, person)
    subject = f"Re: conv_{conversation.id}"
    metadata = {"headers": {"in-reply-to": "<msg@example.com>"}}
    found = parsing._resolve_conversation_from_email_metadata(
        db_session,
        subject=subject,
        metadata=metadata,
    )
    assert found is not None
    assert found.id == conversation.id

"""Tests for CRM inbox parsing utilities.

Tests cover email header parsing, conversation token extraction,
and conversation resolution from email metadata.
"""

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, MessageDirection
from app.services.crm.inbox.parsing import (
    _extract_conversation_tokens,
    _extract_message_ids,
    _find_conversation_by_token,
    _get_metadata_value,
    _resolve_conversation_from_email_metadata,
)


class TestGetMetadataValue:
    """Tests for _get_metadata_value function."""

    def test_returns_none_for_none_metadata(self):
        assert _get_metadata_value(None, "key") is None

    def test_returns_none_for_empty_metadata(self):
        assert _get_metadata_value({}, "key") is None

    def test_exact_match(self):
        metadata = {"subject": "Test Subject", "from": "sender@example.com"}
        assert _get_metadata_value(metadata, "subject") == "Test Subject"
        assert _get_metadata_value(metadata, "from") == "sender@example.com"

    def test_case_insensitive_match(self):
        metadata = {"Subject": "Test Subject", "FROM": "sender@example.com"}
        assert _get_metadata_value(metadata, "subject") == "Test Subject"
        assert _get_metadata_value(metadata, "from") == "sender@example.com"

    def test_headers_subdict_fallback(self):
        metadata = {
            "body": "Email body",
            "headers": {
                "In-Reply-To": "<msg-id@example.com>",
                "References": "<ref1> <ref2>",
            },
        }
        # Case-insensitive lookup uses lowercase comparison
        assert _get_metadata_value(metadata, "In-Reply-To") == "<msg-id@example.com>"
        # Note: 'in-reply-to' (with dashes) matches 'In-Reply-To', but 'in_reply_to' (with underscores) does not
        assert _get_metadata_value(metadata, "in-reply-to") == "<msg-id@example.com>"
        assert _get_metadata_value(metadata, "references") == "<ref1> <ref2>"

    def test_headers_case_insensitive(self):
        metadata = {
            "headers": {
                "MESSAGE-ID": "<original@example.com>",
            }
        }
        assert _get_metadata_value(metadata, "message-id") == "<original@example.com>"
        assert _get_metadata_value(metadata, "Message-ID") == "<original@example.com>"

    def test_top_level_takes_precedence(self):
        metadata = {
            "subject": "Top Level Subject",
            "headers": {
                "subject": "Header Subject",
            },
        }
        assert _get_metadata_value(metadata, "subject") == "Top Level Subject"

    def test_non_dict_headers_ignored(self):
        metadata = {
            "headers": "not a dict",
            "subject": "Test",
        }
        assert _get_metadata_value(metadata, "subject") == "Test"
        # Should not crash when headers is not a dict
        assert _get_metadata_value(metadata, "missing_key") is None

    def test_non_string_keys_handled(self):
        # Metadata might have non-string keys from JSON parsing
        metadata = {123: "numeric key", "valid": "string key"}
        assert _get_metadata_value(metadata, "valid") == "string key"
        # Non-string keys are skipped in case-insensitive matching
        assert _get_metadata_value(metadata, "numeric") is None


class TestExtractMessageIds:
    """Tests for _extract_message_ids function."""

    def test_returns_empty_for_none(self):
        assert _extract_message_ids(None) == []

    def test_returns_empty_for_empty_string(self):
        assert _extract_message_ids("") == []

    def test_single_angle_bracket_id(self):
        result = _extract_message_ids("<msg-id@example.com>")
        assert "msg-id@example.com" in result

    def test_multiple_angle_bracket_ids(self):
        result = _extract_message_ids("<id1@example.com> <id2@example.com>")
        assert "id1@example.com" in result
        assert "id2@example.com" in result

    def test_bare_id_without_brackets(self):
        result = _extract_message_ids("msg-id@example.com")
        assert "msg-id@example.com" in result

    def test_list_input(self):
        result = _extract_message_ids(["<id1@example.com>", "<id2@example.com>"])
        assert "id1@example.com" in result
        assert "id2@example.com" in result

    def test_tuple_input(self):
        result = _extract_message_ids(("<id1@example.com>",))
        assert "id1@example.com" in result

    def test_set_input(self):
        result = _extract_message_ids({"<id1@example.com>"})
        assert "id1@example.com" in result

    def test_mixed_formats(self):
        # References header often has multiple IDs
        refs = "<first@example.com>\n<second@example.com>\n<third@example.com>"
        result = _extract_message_ids(refs)
        assert "first@example.com" in result
        assert "second@example.com" in result
        assert "third@example.com" in result

    def test_ignores_empty_items_in_list(self):
        result = _extract_message_ids(["<id1@example.com>", None, "", "<id2@example.com>"])
        assert "id1@example.com" in result
        assert "id2@example.com" in result

    def test_real_world_references_header(self):
        # Actual References header format
        refs = "<CAG+5c-wvRwpkZPc0nrHgJ4q@mail.gmail.com> <CAG+5c-xYzAbc123@mail.gmail.com>"
        result = _extract_message_ids(refs)
        assert len(result) >= 2


class TestExtractConversationTokens:
    """Tests for _extract_conversation_tokens function."""

    def test_returns_empty_for_none(self):
        assert _extract_conversation_tokens(None) == []

    def test_returns_empty_for_empty_string(self):
        assert _extract_conversation_tokens("") == []

    def test_extracts_conv_underscore_token(self):
        # Token must be 8+ hex chars
        text = "Re: Your question [conv_abcd1234]"
        tokens = _extract_conversation_tokens(text)
        assert "abcd1234" in tokens

    def test_extracts_conv_dash_token(self):
        text = "Re: Your question [conv-abcd1234]"
        tokens = _extract_conversation_tokens(text)
        assert "abcd1234" in tokens

    def test_extracts_conversation_underscore_token(self):
        text = "Re: Support request conversation_12345678"
        tokens = _extract_conversation_tokens(text)
        assert "12345678" in tokens

    def test_extracts_conversation_dash_token(self):
        text = "Re: Support request conversation-12345678"
        tokens = _extract_conversation_tokens(text)
        assert "12345678" in tokens

    def test_extracts_ticket_hash_token(self):
        text = "Re: Ticket #12345678"
        tokens = _extract_conversation_tokens(text)
        assert "12345678" in tokens

    def test_extracts_ticket_with_spaces(self):
        text = "Re: ticket # 87654321 update"
        tokens = _extract_conversation_tokens(text)
        assert "87654321" in tokens

    def test_extracts_full_uuid(self):
        uuid_str = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        text = f"Re: conv_{uuid_str}"
        tokens = _extract_conversation_tokens(text)
        assert uuid_str in tokens

    def test_extracts_multiple_tokens(self):
        # Tokens must be 8+ hex chars for conv patterns
        text = "Re: conv_abcdef12 and ticket #98765432"
        tokens = _extract_conversation_tokens(text)
        assert "abcdef12" in tokens
        assert "98765432" in tokens

    def test_no_match_for_random_text(self):
        text = "Just a regular email subject"
        tokens = _extract_conversation_tokens(text)
        assert tokens == []

    def test_case_insensitive_ticket(self):
        text = "Re: TICKET #12345678"
        tokens = _extract_conversation_tokens(text)
        assert "12345678" in tokens

    def test_short_token_not_matched(self):
        # Tokens < 8 chars should not be matched
        text = "Re: conv_abc123"  # Only 6 chars
        tokens = _extract_conversation_tokens(text)
        assert "abc123" not in tokens


class TestFindConversationByToken:
    """Tests for _find_conversation_by_token function."""

    def test_returns_none_for_empty_token(self, db_session):
        assert _find_conversation_by_token(db_session, "") is None
        assert _find_conversation_by_token(db_session, "   ") is None

    def test_strips_brackets(self, db_session):
        # Token wrapped in brackets should be cleaned
        # This tests the cleanup logic, not the actual lookup
        result = _find_conversation_by_token(db_session, "[abc]")
        assert result is None  # No match, but didn't crash

    def test_finds_by_full_uuid_hex(self, db_session, crm_contact):
        """Test finding conversation by 32-char hex UUID."""
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Test Conversation",
        )
        db_session.add(conv)
        db_session.commit()

        # Use hex format (no dashes)
        hex_id = conv.id.hex
        result = _find_conversation_by_token(db_session, hex_id)
        assert result is not None
        assert result.id == conv.id

    def test_finds_by_full_uuid_with_dashes(self, db_session, crm_contact):
        """Test finding conversation by 36-char UUID with dashes."""
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Test Conversation",
        )
        db_session.add(conv)
        db_session.commit()

        # Use string format (with dashes)
        str_id = str(conv.id)
        result = _find_conversation_by_token(db_session, str_id)
        assert result is not None
        assert result.id == conv.id

    def test_finds_by_uuid_prefix(self, db_session, crm_contact):
        """Test finding conversation by UUID prefix (8+ hex chars)."""
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Test Conversation",
        )
        db_session.add(conv)
        db_session.commit()

        # Use first 8 characters of hex ID
        prefix = conv.id.hex[:8]
        result = _find_conversation_by_token(db_session, prefix)
        assert result is not None
        assert result.id == conv.id

    def test_finds_by_numeric_subject_match(self, db_session, crm_contact):
        """Test finding conversation by numeric ID in subject."""
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Issue #123456 - Login problem",
        )
        db_session.add(conv)
        db_session.commit()

        result = _find_conversation_by_token(db_session, "123456")
        assert result is not None
        assert result.id == conv.id

    def test_returns_none_for_invalid_uuid(self, db_session):
        """Test that invalid UUIDs don't crash."""
        result = _find_conversation_by_token(db_session, "not-a-valid-uuid")
        assert result is None

    def test_returns_none_for_short_token(self, db_session):
        """Test that tokens < 4 chars are rejected."""
        result = _find_conversation_by_token(db_session, "abc")
        assert result is None


class TestResolveConversationFromEmailMetadata:
    """Tests for _resolve_conversation_from_email_metadata function."""

    def test_returns_none_for_empty_inputs(self, db_session):
        result = _resolve_conversation_from_email_metadata(db_session, None, None)
        assert result is None

    def test_returns_none_for_no_matches(self, db_session):
        result = _resolve_conversation_from_email_metadata(
            db_session,
            "Random subject with no tokens",
            {"from": "sender@example.com"},
        )
        assert result is None

    def test_resolves_by_full_uuid_in_subject(self, db_session, crm_contact):
        """Test resolution via full UUID in subject."""
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Original Subject",
        )
        db_session.add(conv)
        db_session.commit()

        # Subject contains the full conversation UUID (with dashes)
        subject = f"Re: Original Subject [conv_{conv.id}]"
        result = _resolve_conversation_from_email_metadata(db_session, subject, None)
        assert result is not None
        assert result.id == conv.id

    def test_resolves_by_full_uuid_hex_in_subject(self, db_session, crm_contact):
        """Test resolution via full hex UUID in subject."""
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Original Subject",
        )
        db_session.add(conv)
        db_session.commit()

        # Subject contains the full conversation UUID (hex format)
        subject = f"Re: Original Subject [conv_{conv.id.hex}]"
        result = _resolve_conversation_from_email_metadata(db_session, subject, None)
        assert result is not None
        assert result.id == conv.id

    def test_resolves_by_subject_token_prefix(self, db_session, crm_contact):
        """Test resolution via conversation token prefix in subject."""
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Original Subject",
        )
        db_session.add(conv)
        db_session.commit()

        # Subject contains a prefix of the conversation ID
        subject = f"Re: Original Subject [conv_{conv.id.hex[:12]}]"
        result = _resolve_conversation_from_email_metadata(db_session, subject, None)
        assert result is not None
        assert result.id == conv.id

    def test_resolves_by_full_uuid_in_reply_to(self, db_session, crm_contact):
        """Test resolution via full UUID in reply-to address."""
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Support Request",
        )
        db_session.add(conv)
        db_session.commit()

        metadata = {
            "reply_to": f"support+conv_{conv.id}@example.com",
        }
        result = _resolve_conversation_from_email_metadata(db_session, "Re: Support Request", metadata)
        assert result is not None
        assert result.id == conv.id

    def test_resolves_by_in_reply_to_header(self, db_session, crm_contact):
        """Test resolution via In-Reply-To header matching existing message."""
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Email Thread",
        )
        db_session.add(conv)
        db_session.commit()

        # Create a message with external_id
        msg = Message(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            external_id="original-msg-id@example.com",
            body="Original message",
        )
        db_session.add(msg)
        db_session.commit()

        metadata = {
            "in_reply_to": "<original-msg-id@example.com>",
        }
        result = _resolve_conversation_from_email_metadata(db_session, "Re: Email Thread", metadata)
        assert result is not None
        assert result.id == conv.id

    def test_resolves_by_references_header(self, db_session, crm_contact):
        """Test resolution via References header."""
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Long Thread",
        )
        db_session.add(conv)
        db_session.commit()

        # Create a message in the thread
        msg = Message(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            external_id="thread-msg-2@example.com",
            body="Second message",
        )
        db_session.add(msg)
        db_session.commit()

        # References header contains multiple message IDs
        metadata = {
            "references": "<thread-msg-1@example.com> <thread-msg-2@example.com>",
        }
        result = _resolve_conversation_from_email_metadata(db_session, "Re: Long Thread", metadata)
        assert result is not None
        assert result.id == conv.id

    def test_extracts_tokens_from_to_address(self, db_session, crm_contact):
        """Test resolution via full UUID in To address."""
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Customer Issue",
        )
        db_session.add(conv)
        db_session.commit()

        metadata = {
            "to": [f"support+conv_{conv.id}@company.com"],
        }
        result = _resolve_conversation_from_email_metadata(db_session, "Re: Customer Issue", metadata)
        assert result is not None
        assert result.id == conv.id

    def test_handles_list_addresses(self, db_session, crm_contact):
        """Test that list-format addresses are processed."""
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Multi-recipient",
        )
        db_session.add(conv)
        db_session.commit()

        metadata = {
            "to": ["primary@company.com", f"tracking+conv_{conv.id}@company.com"],
        }
        result = _resolve_conversation_from_email_metadata(db_session, "Re: Multi-recipient", metadata)
        assert result is not None
        assert result.id == conv.id


class TestIntegrationScenarios:
    """Integration tests for real-world email threading scenarios."""

    def test_gmail_thread_resolution(self, db_session, crm_contact):
        """Test resolving Gmail-style email threads."""
        # Create initial conversation
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Help with my order",
        )
        db_session.add(conv)
        db_session.commit()

        # Create outbound reply with Message-ID
        outbound = Message(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            external_id="CAG+5c-outbound123@mail.gmail.com",
            body="Thank you for contacting us...",
        )
        db_session.add(outbound)
        db_session.commit()

        # Simulate customer reply with In-Reply-To
        metadata = {
            "in_reply_to": "<CAG+5c-outbound123@mail.gmail.com>",
            "references": "<CAG+5c-outbound123@mail.gmail.com>",
        }
        result = _resolve_conversation_from_email_metadata(db_session, "Re: Help with my order", metadata)
        assert result is not None
        assert result.id == conv.id

    def test_outlook_thread_resolution(self, db_session, crm_contact):
        """Test resolving Outlook-style email threads."""
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Project Update",
        )
        db_session.add(conv)
        db_session.commit()

        # Outlook uses different Message-ID format
        outbound = Message(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            external_id="AM6PR07MB1234.prod.outlook.com",
            body="Here is the update...",
        )
        db_session.add(outbound)
        db_session.commit()

        # Use underscores in metadata key (matching how emails are typically processed)
        metadata = {
            "in_reply_to": "<AM6PR07MB1234.prod.outlook.com>",
        }
        result = _resolve_conversation_from_email_metadata(db_session, "RE: Project Update", metadata)
        assert result is not None
        assert result.id == conv.id

    def test_support_ticket_reference(self, db_session, crm_contact):
        """Test resolving via ticket reference in subject."""
        # Ticket number must be 8+ chars to match the regex pattern
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Ticket #98765432 - Network Issue",
        )
        db_session.add(conv)
        db_session.commit()

        # Customer replies referencing ticket number
        result = _resolve_conversation_from_email_metadata(
            db_session,
            "Re: Ticket #98765432 - Network Issue",
            None,
        )
        assert result is not None
        assert result.id == conv.id

    def test_plus_addressed_reply_full_uuid(self, db_session, crm_contact):
        """Test resolving via plus-addressed reply-to with full UUID."""
        conv = Conversation(
            person_id=crm_contact.id,
            subject="Billing Question",
        )
        db_session.add(conv)
        db_session.commit()

        # Reply-to uses plus addressing with full conversation UUID
        metadata = {
            "reply_to": f'"Support" <support+conv-{conv.id}@company.com>',
        }
        result = _resolve_conversation_from_email_metadata(db_session, "Re: Billing Question", metadata)
        assert result is not None
        assert result.id == conv.id

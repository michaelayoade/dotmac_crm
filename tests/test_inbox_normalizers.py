"""Tests for CRM inbox normalizers.

Tests cover all normalization functions for external IDs, email addresses,
phone numbers, and channel-specific address handling.
"""

import hashlib

from app.models.crm.enums import ChannelType
from app.services.crm.inbox.normalizers import (
    _normalize_channel_address,
    _normalize_email_address,
    _normalize_email_message_id,
    _normalize_external_id,
    _normalize_phone_address,
)


class TestNormalizeExternalId:
    """Tests for _normalize_external_id function."""

    def test_returns_none_for_none_input(self):
        assert _normalize_external_id(None) is None

    def test_returns_none_for_empty_string(self):
        assert _normalize_external_id("") is None

    def test_returns_none_for_whitespace_only(self):
        assert _normalize_external_id("   ") is None
        assert _normalize_external_id("\t\n") is None

    def test_strips_whitespace(self):
        assert _normalize_external_id("  abc123  ") == "abc123"
        assert _normalize_external_id("\t\nmsg-id\n\t") == "msg-id"

    def test_returns_id_under_120_chars(self):
        short_id = "a" * 100
        assert _normalize_external_id(short_id) == short_id

    def test_returns_id_exactly_120_chars(self):
        exact_id = "x" * 120
        assert _normalize_external_id(exact_id) == exact_id

    def test_hashes_id_over_120_chars(self):
        long_id = "y" * 121
        result = _normalize_external_id(long_id)
        expected_hash = hashlib.sha256(long_id.encode("utf-8")).hexdigest()
        assert result == expected_hash
        assert len(result) == 64  # SHA-256 hex length

    def test_hashes_very_long_id(self):
        very_long_id = "z" * 1000
        result = _normalize_external_id(very_long_id)
        expected_hash = hashlib.sha256(very_long_id.encode("utf-8")).hexdigest()
        assert result == expected_hash

    def test_preserves_special_characters(self):
        special_id = "msg-id@example.com_12345"
        assert _normalize_external_id(special_id) == special_id


class TestNormalizeEmailMessageId:
    """Tests for _normalize_email_message_id function."""

    def test_returns_none_for_none_input(self):
        assert _normalize_email_message_id(None) is None

    def test_returns_none_for_empty_string(self):
        assert _normalize_email_message_id("") is None

    def test_strips_angle_brackets(self):
        assert _normalize_email_message_id("<msg-id@example.com>") == "msg-id@example.com"

    def test_strips_single_angle_bracket(self):
        assert _normalize_email_message_id("<msg-id") == "msg-id"
        assert _normalize_email_message_id("msg-id>") == "msg-id"

    def test_handles_no_angle_brackets(self):
        assert _normalize_email_message_id("msg-id@example.com") == "msg-id@example.com"

    def test_strips_whitespace_and_brackets(self):
        assert _normalize_email_message_id("  <msg-id@example.com>  ") == "msg-id@example.com"

    def test_handles_nested_brackets(self):
        # Double brackets - inner ones remain after first strip
        assert _normalize_email_message_id("<<inner>>") == "inner"

    def test_hashes_long_message_id(self):
        long_msg_id = "<" + "a" * 130 + "@example.com>"
        result = _normalize_email_message_id(long_msg_id)
        # After stripping brackets, the ID is still > 120 chars, so it gets hashed
        expected = hashlib.sha256(("a" * 130 + "@example.com").encode("utf-8")).hexdigest()
        assert result == expected


class TestNormalizeEmailAddress:
    """Tests for _normalize_email_address function."""

    def test_returns_none_for_none_input(self):
        assert _normalize_email_address(None) is None

    def test_returns_none_for_empty_string(self):
        assert _normalize_email_address("") is None

    def test_returns_none_for_whitespace_only(self):
        assert _normalize_email_address("   ") is None

    def test_lowercases_email(self):
        assert _normalize_email_address("User@Example.COM") == "user@example.com"
        assert _normalize_email_address("ALLCAPS@DOMAIN.ORG") == "allcaps@domain.org"

    def test_strips_whitespace(self):
        assert _normalize_email_address("  user@example.com  ") == "user@example.com"
        assert _normalize_email_address("\tuser@example.com\n") == "user@example.com"

    def test_lowercases_and_strips(self):
        assert _normalize_email_address("  User@Example.COM  ") == "user@example.com"

    def test_preserves_valid_email_format(self):
        assert _normalize_email_address("test.user+tag@sub.domain.com") == "test.user+tag@sub.domain.com"


class TestNormalizePhoneAddress:
    """Tests for _normalize_phone_address function."""

    def test_returns_none_for_none_input(self):
        assert _normalize_phone_address(None) is None

    def test_returns_none_for_empty_string(self):
        assert _normalize_phone_address("") is None

    def test_returns_none_for_no_digits(self):
        assert _normalize_phone_address("abc") is None
        assert _normalize_phone_address("---") is None
        assert _normalize_phone_address("   ") is None

    def test_extracts_digits_and_adds_plus(self):
        assert _normalize_phone_address("1234567890") == "+1234567890"

    def test_strips_formatting_characters(self):
        assert _normalize_phone_address("+1 (234) 567-8900") == "+12345678900"
        assert _normalize_phone_address("1-234-567-8900") == "+12345678900"

    def test_handles_international_format(self):
        assert _normalize_phone_address("+63 917 123 4567") == "+639171234567"
        assert _normalize_phone_address("+1-800-FLOWERS") == "+1800"  # Letters stripped

    def test_handles_whatsapp_format(self):
        # WhatsApp often uses formats like "whatsapp:+639171234567"
        assert _normalize_phone_address("whatsapp:+639171234567") == "+639171234567"

    def test_extracts_from_mixed_content(self):
        assert _normalize_phone_address("Call me at 555-1234") == "+5551234"


class TestNormalizeChannelAddress:
    """Tests for _normalize_channel_address function."""

    def test_returns_none_for_none_address(self):
        assert _normalize_channel_address(ChannelType.email, None) is None
        assert _normalize_channel_address(ChannelType.whatsapp, None) is None
        assert _normalize_channel_address(ChannelType.facebook_messenger, None) is None

    def test_email_channel_uses_email_normalizer(self):
        result = _normalize_channel_address(ChannelType.email, "  User@Example.COM  ")
        assert result == "user@example.com"

    def test_whatsapp_channel_uses_phone_normalizer(self):
        result = _normalize_channel_address(ChannelType.whatsapp, "+1 (234) 567-8900")
        assert result == "+12345678900"

    def test_facebook_channel_strips_only(self):
        result = _normalize_channel_address(ChannelType.facebook_messenger, "  user_id_12345  ")
        assert result == "user_id_12345"

    def test_instagram_channel_strips_only(self):
        result = _normalize_channel_address(ChannelType.instagram_dm, "  ig_user_456  ")
        assert result == "ig_user_456"

    def test_chat_widget_channel_strips_only(self):
        result = _normalize_channel_address(ChannelType.chat_widget, "  session_abc123  ")
        assert result == "session_abc123"

    def test_note_channel_strips_only(self):
        result = _normalize_channel_address(ChannelType.note, "  internal_note_789  ")
        assert result == "internal_note_789"

    def test_all_channel_types_handle_whitespace(self):
        # All channel types should at least strip whitespace
        for channel_type in ChannelType:
            result = _normalize_channel_address(channel_type, "  test_value  ")
            # Result should not have leading/trailing whitespace
            assert result is None or result == result.strip()


class TestEdgeCases:
    """Edge case tests for normalizers."""

    def test_unicode_in_external_id(self):
        unicode_id = "msg-id-日本語"
        result = _normalize_external_id(unicode_id)
        assert result == unicode_id

    def test_unicode_in_long_external_id(self):
        # Unicode ID > 120 chars should be hashed
        unicode_id = "日本語" * 50
        result = _normalize_external_id(unicode_id)
        expected = hashlib.sha256(unicode_id.encode("utf-8")).hexdigest()
        assert result == expected

    def test_email_with_unicode_domain(self):
        # Internationalized domain names
        result = _normalize_email_address("user@例え.jp")
        assert result == "user@例え.jp"

    def test_phone_with_extension(self):
        # Extensions should have digits extracted
        result = _normalize_phone_address("+1-800-555-1234 ext 567")
        assert result == "+18005551234567"

    def test_empty_channel_address_variants(self):
        assert _normalize_channel_address(ChannelType.email, "") is None
        assert _normalize_channel_address(ChannelType.whatsapp, "   ") is None

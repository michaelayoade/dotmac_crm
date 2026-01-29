from datetime import datetime, timezone

from app.logic.crm_inbox_logic import (
    LogicService,
    InboundSelfMessageContext,
    InboundDedupeContext,
)


def test_inbound_self_email_metadata_skip():
    logic = LogicService()
    ctx = InboundSelfMessageContext(
        channel_type="email",
        sender_address="user@example.com",
        metadata={"from_me": True},
        self_email_addresses={"user@example.com"},
    )
    assert logic.decide_inbound_self_message(ctx) is True


def test_inbound_self_email_address_match():
    logic = LogicService()
    ctx = InboundSelfMessageContext(
        channel_type="email",
        sender_address="Sender@Example.com ",
        metadata=None,
        self_email_addresses={"sender@example.com"},
    )
    assert logic.decide_inbound_self_message(ctx) is True


def test_inbound_self_whatsapp_address_match():
    logic = LogicService()
    ctx = InboundSelfMessageContext(
        channel_type="whatsapp",
        sender_address="+1 (555) 123-4567",
        metadata=None,
        business_number="15551234567",
    )
    assert logic.decide_inbound_self_message(ctx) is True


def test_inbound_dedupe_email_builds_external_id_when_missing_message_id():
    logic = LogicService()
    received_at = datetime(2026, 1, 28, 12, 0, 0, tzinfo=timezone.utc)
    ctx = InboundDedupeContext(
        channel_type="email",
        contact_address="Test@Example.com",
        subject="Hello",
        body="Body",
        received_at_iso=received_at.isoformat(),
        message_id=None,
    )
    decision = logic.decide_inbound_dedupe(ctx)
    assert decision.message_id
    assert decision.dedupe_across_targets is True


def test_inbound_dedupe_email_normalizes_long_message_id():
    logic = LogicService()
    long_id = "x" * 200
    ctx = InboundDedupeContext(
        channel_type="email",
        contact_address="user@example.com",
        subject=None,
        body="Body",
        received_at_iso="2026-01-28T12:00:00+00:00",
        message_id=long_id,
    )
    decision = logic.decide_inbound_dedupe(ctx)
    assert decision.message_id != long_id
    assert decision.dedupe_across_targets is True


def test_inbound_dedupe_whatsapp_uses_message_id():
    logic = LogicService()
    ctx = InboundDedupeContext(
        channel_type="whatsapp",
        contact_address="+15551234567",
        subject=None,
        body="Hi",
        received_at_iso="2026-01-28T12:00:00+00:00",
        message_id="wa-123",
    )
    decision = logic.decide_inbound_dedupe(ctx)
    assert decision.message_id == "wa-123"
    assert decision.dedupe_across_targets is False

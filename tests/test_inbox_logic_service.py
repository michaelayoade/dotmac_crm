from datetime import UTC, datetime, timedelta

from app.logic.crm_inbox_logic import LogicService, MessageContext


def test_email_allowed_without_last_inbound():
    logic = LogicService()
    now = datetime.now(UTC)
    ctx = MessageContext(
        conversation_id="c1",
        person_id="p1",
        requested_channel_type="email",
        requested_channel_target_id=None,
        last_inbound_channel_type=None,
        last_inbound_channel_target_id=None,
        last_inbound_received_at_iso=None,
        now_iso=now.isoformat(),
    )
    decision = logic.decide_send_message(ctx)
    assert decision.status == "allow"
    assert decision.channel_type == "email"


def test_email_denied_on_channel_mismatch():
    logic = LogicService()
    now = datetime.now(UTC)
    ctx = MessageContext(
        conversation_id="c2",
        person_id="p2",
        requested_channel_type="email",
        requested_channel_target_id=None,
        last_inbound_channel_type="whatsapp",
        last_inbound_channel_target_id=None,
        last_inbound_received_at_iso=now.isoformat(),
        now_iso=now.isoformat(),
    )
    decision = logic.decide_send_message(ctx)
    assert decision.status == "deny"
    assert "Reply channel does not match" in (decision.reason or "")


def test_mock_whatsapp_allowed_without_last_inbound():
    logic = LogicService()
    now = datetime.now(UTC)
    ctx = MessageContext(
        conversation_id="c3",
        person_id="p3",
        requested_channel_type="whatsapp",
        requested_channel_target_id="wa-target-1",
        last_inbound_channel_type=None,
        last_inbound_channel_target_id=None,
        last_inbound_received_at_iso=None,
        now_iso=now.isoformat(),
    )
    decision = logic.decide_send_message(ctx)
    assert decision.status == "allow"
    assert decision.channel_type == "whatsapp"


def test_mock_facebook_denied_outside_24h():
    logic = LogicService()
    now = datetime.now(UTC)
    ctx = MessageContext(
        conversation_id="c4",
        person_id="p4",
        requested_channel_type="facebook_messenger",
        requested_channel_target_id=None,
        last_inbound_channel_type="facebook_messenger",
        last_inbound_channel_target_id="page-1",
        last_inbound_received_at_iso=(now - timedelta(hours=30)).isoformat(),
        now_iso=now.isoformat(),
    )
    decision = logic.decide_send_message(ctx)
    assert decision.status == "deny"
    assert "Meta reply window expired" in (decision.reason or "")

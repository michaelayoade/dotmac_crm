from types import SimpleNamespace
from unittest.mock import patch

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection
from app.models.person import Person
from app.schemas.crm.inbox import EmailWebhookPayload, WhatsAppWebhookPayload
from app.services.crm.ai_intake import AI_INTAKE_METADATA_KEY, AiIntakeResult
from app.services.crm.inbox.handlers.base import (
    InboundDuplicateResult,
    InboundHandler,
    InboundProcessResult,
)
from app.services.crm.inbox.handlers.email import EmailHandler
from app.services.crm.inbox.handlers.utils import post_process_inbound_message
from app.services.crm.inbox.handlers.whatsapp import WhatsAppHandler


class _DummyDB:
    def commit(self):
        return None

    def refresh(self, _):
        return None


class _DummyEvent:
    pass


class _DummyMessage:
    def __init__(self):
        self.id = "msg-1"
        self.conversation_id = "conv-1"
        self.direction = MessageDirection.inbound
        self.channel_type = SimpleNamespace(value=ChannelType.email.value)
        self.channel_target_id = None
        self.subject = "Subject"
        self.external_id = "ext-1"


class _DummyConversation:
    def __init__(self):
        self.id = "conv-1"
        self.person_id = "person-1"


class _Handler(InboundHandler):
    def process(self, db, payload):
        return InboundProcessResult(
            conversation_id="conv-1",
            message_payload=payload,
            channel_target_id=None,
        )


def test_base_handler_receive_post_commit():
    handler = _Handler()
    db = _DummyDB()
    payload = SimpleNamespace(
        model_dump=lambda by_alias=False: {
            "conversation_id": "conv-1",
            "direction": MessageDirection.inbound,
            "channel_type": ChannelType.email,
            "subject": "Subject",
            "external_id": "ext-1",
        }
    )

    with (
        patch("app.services.crm.inbox.handlers.base.create_message_and_touch_conversation") as mock_create,
        patch("app.services.crm.inbox.handlers.base._is_new_inbound_message") as mock_is_new,
        patch("app.services.crm.inbox.handlers.base.post_process_inbound_message") as mock_post,
        patch("app.services.crm.inbox.handlers.base.emit_event") as mock_emit,
        patch("app.services.crm.inbox.handlers.base.event.listen") as mock_listen,
    ):
        mock_create.return_value = (_DummyConversation(), _DummyMessage())
        mock_is_new.return_value = True

        def _listen(_target, _name, fn, once=False):
            fn(_DummyEvent())
            return None

        mock_listen.side_effect = _listen
        message = handler.receive(db, payload)
        assert message is not None
        mock_post.assert_called_once()
        assert mock_post.call_args.kwargs["is_new_conversation"] is True
        mock_emit.assert_called_once()


def test_base_handler_preserves_ingest_time_newness_for_after_commit_callback():
    handler = _Handler()
    db = _DummyDB()
    payload = SimpleNamespace(
        model_dump=lambda by_alias=False: {
            "conversation_id": "conv-1",
            "direction": MessageDirection.inbound,
            "channel_type": ChannelType.email,
            "subject": "Subject",
            "external_id": "ext-1",
        }
    )
    after_commit = {}

    with (
        patch("app.services.crm.inbox.handlers.base.create_message_and_touch_conversation") as mock_create,
        patch("app.services.crm.inbox.handlers.base._is_new_inbound_message") as mock_is_new,
        patch("app.services.crm.inbox.handlers.base.post_process_inbound_message") as mock_post,
        patch("app.services.crm.inbox.handlers.base.emit_event"),
        patch("app.services.crm.inbox.handlers.base.event.listen") as mock_listen,
    ):
        mock_create.return_value = (_DummyConversation(), _DummyMessage())
        mock_is_new.return_value = True

        def _listen(_target, _name, fn, once=False):
            after_commit["fn"] = fn
            return None

        mock_listen.side_effect = _listen
        message = handler.receive(db, payload)
        assert message is not None
        mock_post.assert_not_called()

        mock_is_new.return_value = False
        after_commit["fn"](_DummyEvent())

        mock_post.assert_called_once()
        assert mock_post.call_args.kwargs["is_new_conversation"] is True


def test_post_process_inbound_message_skips_generic_routing_when_ai_intake_handles_first_inbound(
    db_session, monkeypatch
):
    person = Person(email="handler-ai@example.com", first_name="Handler", last_name="AI")
    db_session.add(person)
    db_session.flush()
    conversation = Conversation(person_id=person.id, status=ConversationStatus.open, is_active=True, metadata_={})
    db_session.add(conversation)
    db_session.flush()
    message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.whatsapp,
        direction=MessageDirection.inbound,
        body="Need billing help",
        metadata_={},
    )
    db_session.add(message)
    db_session.commit()

    events = []

    def _fake_process_pending_intake(db, **kwargs):
        events.append(("process_pending_intake", kwargs["is_new_conversation"]))
        refreshed = db.get(Conversation, conversation.id)
        refreshed.status = ConversationStatus.pending
        refreshed.metadata_ = {
            AI_INTAKE_METADATA_KEY: {
                "status": "awaiting_customer",
                "started_at": "2026-05-07T08:02:10+00:00",
            }
        }
        db.commit()
        return AiIntakeResult(handled=True, waiting_for_customer=True)

    monkeypatch.setattr(
        "app.services.crm.inbox.handlers.utils._assign_billing_risk_outreach_owner",
        lambda db, conversation: False,
    )
    monkeypatch.setattr("app.services.crm.inbox.handlers.utils.make_scope_key", lambda **kwargs: "target:test")
    monkeypatch.setattr(
        "app.services.crm.inbox.handlers.utils.process_pending_intake",
        _fake_process_pending_intake,
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.handlers.utils._send_resolved_ai_handoff_if_missing",
        lambda db, conversation, message, intake_result: events.append(("handoff_check", intake_result.handled)),
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.handlers.utils.apply_routing_rules",
        lambda db, conversation, message: events.append(("apply_routing_rules", str(conversation.id))),
    )
    monkeypatch.setattr("app.services.crm.inbox.handlers.utils.broadcast_new_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "app.services.crm.inbox.handlers.utils.broadcast_conversation_summary",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.crm.inbox.handlers.utils.broadcast_agent_notification", lambda *args, **kwargs: None
    )
    monkeypatch.setattr("app.services.crm.inbox.handlers.utils.broadcast_inbox_updated", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.crm.inbox.handlers.utils.build_conversation_summary", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "app.services.crm.inbox.handlers.utils.notify_assigned_agent_new_reply",
        lambda *args, **kwargs: events.append(("notify_assigned_agent_new_reply", None)),
    )

    post_process_inbound_message(
        db_session,
        conversation_id=str(conversation.id),
        message_id=str(message.id),
        channel_target_id=None,
        is_new_conversation=True,
    )

    db_session.refresh(conversation)
    assert events[0] == ("process_pending_intake", True)
    assert ("apply_routing_rules", str(conversation.id)) not in events
    assert ("notify_assigned_agent_new_reply", None) not in events
    assert conversation.status == ConversationStatus.pending
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["started_at"] == "2026-05-07T08:02:10+00:00"


def test_whatsapp_handler_process_success():
    handler = WhatsAppHandler()
    payload = WhatsAppWebhookPayload(
        contact_address="+15555550000",
        contact_name="Test",
        message_id="mid-1",
        body="Hello",
        received_at=None,
        metadata={},
        channel_target_id=None,
    )

    with (
        patch("app.services.crm.inbox.handlers.whatsapp._resolve_integration_target") as mock_target,
        patch("app.services.crm.inbox.handlers.whatsapp._resolve_connector_config") as mock_config,
        patch("app.services.crm.inbox.handlers.whatsapp._is_self_whatsapp_message") as mock_self,
        patch("app.services.crm.inbox.handlers.whatsapp._resolve_person_for_inbound") as mock_person,
        patch("app.services.crm.inbox.handlers.whatsapp._find_duplicate_inbound_message") as mock_dedupe,
        patch("app.services.crm.inbox.handlers.whatsapp.conversation_service") as mock_conv,
    ):
        mock_target.return_value = SimpleNamespace(id="target-1")
        mock_config.return_value = None
        mock_self.return_value = False
        mock_person.return_value = (SimpleNamespace(id="person-1"), SimpleNamespace(id="chan-1"))
        mock_dedupe.return_value = None
        mock_conv.resolve_open_conversation_for_channel.return_value = None
        mock_conv.Conversations.create.return_value = SimpleNamespace(id="conv-1", is_active=True)

        result = handler.process(_DummyDB(), payload)
        assert isinstance(result, InboundProcessResult)
        assert result.channel_target_id == "target-1"


def test_whatsapp_handler_promotes_meta_attribution_to_conversation_metadata():
    handler = WhatsAppHandler()
    payload = WhatsAppWebhookPayload(
        contact_address="+15555550000",
        contact_name="Test",
        message_id="mid-1",
        body="Hello",
        received_at=None,
        metadata={"attribution": {"source": "ADS", "ad_id": "ad-1", "campaign_id": "camp-1"}},
        channel_target_id=None,
    )
    conversation = SimpleNamespace(id="conv-1", is_active=True, metadata_={})
    person = SimpleNamespace(id="person-1", metadata_={})

    with (
        patch("app.services.crm.inbox.handlers.whatsapp._resolve_integration_target") as mock_target,
        patch("app.services.crm.inbox.handlers.whatsapp._resolve_connector_config") as mock_config,
        patch("app.services.crm.inbox.handlers.whatsapp._is_self_whatsapp_message") as mock_self,
        patch("app.services.crm.inbox.handlers.whatsapp._resolve_person_for_inbound") as mock_person,
        patch("app.services.crm.inbox.handlers.whatsapp._find_duplicate_inbound_message") as mock_dedupe,
        patch("app.services.crm.inbox.handlers.whatsapp.conversation_service") as mock_conv,
        patch("app.services.crm.inbox.handlers.whatsapp.meta_webhooks._persist_meta_attribution_to_person_and_lead"),
    ):
        mock_target.return_value = SimpleNamespace(id="target-1")
        mock_config.return_value = None
        mock_self.return_value = False
        mock_person.return_value = (person, SimpleNamespace(id="chan-1"))
        mock_dedupe.return_value = None
        mock_conv.resolve_open_conversation_for_channel.return_value = conversation

        result = handler.process(_DummyDB(), payload)

    assert isinstance(result, InboundProcessResult)
    assert conversation.metadata_["attribution"]["source"] == "ADS"
    assert conversation.metadata_["attribution"]["ad_id"] == "ad-1"
    assert conversation.metadata_["attribution"]["campaign_id"] == "camp-1"
    assert conversation.metadata_["attribution"]["last_channel"] == "whatsapp"


def test_whatsapp_handler_leaves_conversation_metadata_unchanged_without_attribution():
    handler = WhatsAppHandler()
    payload = WhatsAppWebhookPayload(
        contact_address="+15555550000",
        contact_name="Test",
        message_id="mid-1",
        body="Hello",
        received_at=None,
        metadata={"phone_number_id": "pnid-1"},
        channel_target_id=None,
    )
    conversation = SimpleNamespace(id="conv-1", is_active=True, metadata_={"existing": "value"})

    with (
        patch("app.services.crm.inbox.handlers.whatsapp._resolve_integration_target") as mock_target,
        patch("app.services.crm.inbox.handlers.whatsapp._resolve_connector_config") as mock_config,
        patch("app.services.crm.inbox.handlers.whatsapp._is_self_whatsapp_message") as mock_self,
        patch("app.services.crm.inbox.handlers.whatsapp._resolve_person_for_inbound") as mock_person,
        patch("app.services.crm.inbox.handlers.whatsapp._find_duplicate_inbound_message") as mock_dedupe,
        patch("app.services.crm.inbox.handlers.whatsapp.conversation_service") as mock_conv,
    ):
        mock_target.return_value = SimpleNamespace(id="target-1")
        mock_config.return_value = None
        mock_self.return_value = False
        mock_person.return_value = (SimpleNamespace(id="person-1"), SimpleNamespace(id="chan-1"))
        mock_dedupe.return_value = None
        mock_conv.resolve_open_conversation_for_channel.return_value = conversation

        result = handler.process(_DummyDB(), payload)

    assert isinstance(result, InboundProcessResult)
    assert conversation.metadata_ == {"existing": "value"}


def test_whatsapp_handler_duplicate():
    handler = WhatsAppHandler()
    payload = WhatsAppWebhookPayload(
        contact_address="+15555550000",
        contact_name="Test",
        message_id="mid-1",
        body="Hello",
        received_at=None,
        metadata={},
        channel_target_id=None,
    )

    with (
        patch("app.services.crm.inbox.handlers.whatsapp._resolve_integration_target") as mock_target,
        patch("app.services.crm.inbox.handlers.whatsapp._resolve_connector_config") as mock_config,
        patch("app.services.crm.inbox.handlers.whatsapp._is_self_whatsapp_message") as mock_self,
        patch("app.services.crm.inbox.handlers.whatsapp._resolve_person_for_inbound") as mock_person,
        patch("app.services.crm.inbox.handlers.whatsapp._find_duplicate_inbound_message") as mock_dedupe,
    ):
        mock_target.return_value = SimpleNamespace(id="target-1")
        mock_config.return_value = None
        mock_self.return_value = False
        mock_person.return_value = (SimpleNamespace(id="person-1"), SimpleNamespace(id="chan-1"))
        mock_dedupe.return_value = SimpleNamespace(id="msg-dup")

        result = handler.process(_DummyDB(), payload)
        assert isinstance(result, InboundDuplicateResult)


def test_email_handler_process_success():
    handler = EmailHandler()
    payload = EmailWebhookPayload(
        contact_address="user@example.com",
        contact_name="User",
        message_id="mid-1",
        subject="Hello",
        body="Body",
        received_at=None,
        metadata={},
        channel_target_id=None,
    )

    with (
        patch("app.services.crm.inbox.handlers.email._resolve_integration_target") as mock_target,
        patch("app.services.crm.inbox.handlers.email._resolve_connector_config") as mock_config,
        patch("app.services.crm.inbox.handlers.email._is_self_email_message") as mock_self,
        patch("app.services.crm.inbox.handlers.email._resolve_person_for_inbound") as mock_person,
        patch("app.services.crm.inbox.handlers.email._find_duplicate_inbound_message") as mock_dedupe,
        patch("app.services.crm.inbox.handlers.email._normalize_external_id") as mock_normalize,
        patch("app.services.crm.inbox.handlers.email._resolve_conversation_from_email_metadata") as mock_resolve,
        patch("app.services.crm.inbox.handlers.email.conversation_service") as mock_conv,
    ):
        mock_target.return_value = SimpleNamespace(id="target-1")
        mock_config.return_value = None
        mock_self.return_value = False
        mock_person.return_value = (SimpleNamespace(id="person-1"), SimpleNamespace(id="chan-1"))
        mock_normalize.return_value = "ext-1"
        mock_dedupe.return_value = None
        mock_resolve.return_value = None
        mock_conv.resolve_open_conversation_for_channel.return_value = None
        mock_conv.Conversations.create.return_value = SimpleNamespace(id="conv-1", is_active=True)

        result = handler.process(_DummyDB(), payload)
        assert isinstance(result, InboundProcessResult)
        assert result.message_payload.external_id == "ext-1"


def test_email_handler_creates_new_conversation_when_metadata_points_to_resolved():
    handler = EmailHandler()
    payload = EmailWebhookPayload(
        contact_address="user@example.com",
        contact_name="User",
        message_id="mid-2",
        subject="Hello",
        body="Body",
        received_at=None,
        metadata={},
        channel_target_id=None,
    )

    resolved_conversation = SimpleNamespace(
        id="conv-resolved",
        is_active=True,
        status=ConversationStatus.resolved,
    )

    with (
        patch("app.services.crm.inbox.handlers.email._resolve_integration_target") as mock_target,
        patch("app.services.crm.inbox.handlers.email._resolve_connector_config") as mock_config,
        patch("app.services.crm.inbox.handlers.email._is_self_email_message") as mock_self,
        patch("app.services.crm.inbox.handlers.email._resolve_person_for_inbound") as mock_person,
        patch("app.services.crm.inbox.handlers.email._find_duplicate_inbound_message") as mock_dedupe,
        patch("app.services.crm.inbox.handlers.email._normalize_external_id") as mock_normalize,
        patch("app.services.crm.inbox.handlers.email._resolve_conversation_from_email_metadata") as mock_resolve,
        patch("app.services.crm.inbox.handlers.email.conversation_service") as mock_conv,
    ):
        mock_target.return_value = SimpleNamespace(id="target-1")
        mock_config.return_value = None
        mock_self.return_value = False
        mock_person.return_value = (SimpleNamespace(id="person-1"), SimpleNamespace(id="chan-1"))
        mock_normalize.return_value = "ext-2"
        mock_dedupe.return_value = None
        mock_resolve.return_value = resolved_conversation
        mock_conv.resolve_open_conversation_for_channel.return_value = None
        mock_conv.Conversations.create.return_value = SimpleNamespace(id="conv-new", is_active=True)

        result = handler.process(_DummyDB(), payload)

        assert isinstance(result, InboundProcessResult)
        assert result.conversation_id == "conv-new"
        mock_conv.Conversations.create.assert_called_once()

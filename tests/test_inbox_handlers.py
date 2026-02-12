from types import SimpleNamespace
from unittest.mock import patch

from app.models.crm.enums import ChannelType, MessageDirection
from app.schemas.crm.inbox import EmailWebhookPayload, WhatsAppWebhookPayload
from app.services.crm.inbox.handlers.base import (
    InboundDuplicateResult,
    InboundHandler,
    InboundProcessResult,
)
from app.services.crm.inbox.handlers.email import EmailHandler
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
        patch("app.services.crm.inbox.handlers.base.post_process_inbound_message") as mock_post,
        patch("app.services.crm.inbox.handlers.base.emit_event") as mock_emit,
        patch("app.services.crm.inbox.handlers.base.event.listen") as mock_listen,
    ):
        mock_create.return_value = (_DummyConversation(), _DummyMessage())

        def _listen(_target, _name, fn, once=False):
            fn(_DummyEvent())
            return None

        mock_listen.side_effect = _listen
        message = handler.receive(db, payload)
        assert message is not None
        mock_post.assert_called_once()
        mock_emit.assert_called_once()


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

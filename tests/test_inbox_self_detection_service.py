from app.models.crm.enums import ChannelType
from app.services.crm.inbox.self_detection import SelfDetectionService


class _Config:
    def __init__(self, auth_config=None, metadata=None):
        self.auth_config = auth_config
        self.metadata_ = metadata


def test_self_detection_email_address_match():
    service = SelfDetectionService()
    config = _Config(auth_config={"username": "support@example.com"}, metadata={})
    assert service.is_self_message(
        channel_type=ChannelType.email,
        sender_address="support@example.com",
        metadata={},
        config=config,
    )


def test_self_detection_whatsapp_metadata_self():
    service = SelfDetectionService()
    assert service.is_self_message(
        channel_type=ChannelType.whatsapp,
        sender_address="+15555550000",
        metadata={"from_me": True},
        config=None,
    )


def test_self_detection_whatsapp_number_match():
    service = SelfDetectionService()
    config = _Config(metadata={"phone_number": "+15555550000"})
    assert service.is_self_message(
        channel_type=ChannelType.whatsapp,
        sender_address="+1 (555) 555-0000",
        metadata={},
        config=config,
    )

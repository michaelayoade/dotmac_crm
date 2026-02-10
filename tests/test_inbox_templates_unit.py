import pytest
from fastapi import HTTPException

from app.models.crm.enums import ChannelType
from app.schemas.crm.message_template import MessageTemplateCreate, MessageTemplateUpdate
from app.services.crm.inbox import templates


def test_message_template_crud(db_session):
    created = templates.message_templates.create(
        db_session,
        MessageTemplateCreate(
            name="Welcome",
            channel_type=ChannelType.email,
            subject="Hello",
            body="Hi there",
        ),
    )
    fetched = templates.message_templates.get(db_session, str(created.id))
    assert fetched.id == created.id

    updated = templates.message_templates.update(
        db_session,
        str(created.id),
        MessageTemplateUpdate(body="Updated body"),
    )
    assert updated.body == "Updated body"

    listed = templates.message_templates.list(
        db_session,
        channel_type="email",
        is_active=True,
        limit=10,
        offset=0,
    )
    assert any(item.id == created.id for item in listed)

    templates.message_templates.delete(db_session, str(created.id))
    with pytest.raises(HTTPException):
        templates.message_templates.get(db_session, str(created.id))

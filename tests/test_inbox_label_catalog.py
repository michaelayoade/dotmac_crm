from __future__ import annotations

import asyncio
import uuid

from app.models.crm.conversation import Conversation, ConversationTag
from app.models.crm.conversation_label import ConversationLabel
from app.models.crm.enums import ConversationStatus
from app.models.person import Person
from app.services.crm.inbox.labels import (
    create_or_reactivate_label,
    enrich_formatted_conversations_with_labels,
    list_managed_labels,
)
from app.web.admin import crm_inbox_settings


def _person(db_session) -> Person:
    person = Person(first_name="Label", last_name="User", email=f"label-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add(person)
    db_session.flush()
    return person


def _conversation(db_session, person: Person) -> Conversation:
    conv = Conversation(person_id=person.id, status=ConversationStatus.open)
    db_session.add(conv)
    db_session.flush()
    return conv


class _Req:
    def __init__(self, roles: list[str] | None = None, scopes: list[str] | None = None):
        self.state = type("State", (), {"auth": {"roles": roles or [], "scopes": scopes or []}})()


def test_label_catalog_create_and_usage_count(db_session):
    person = _person(db_session)
    conv = _conversation(db_session, person)
    db_session.add(ConversationTag(conversation_id=conv.id, tag="VIP"))
    db_session.commit()

    created = create_or_reactivate_label(db_session, name="VIP", color="amber")
    assert created.ok is True

    labels = list_managed_labels(db_session)
    vip = next((item for item in labels if item["name"] == "VIP"), None)
    assert vip is not None
    assert vip["color"] == "amber"
    assert vip["usage_count"] == 1


def test_enrich_formatted_conversations_with_labels_uses_catalog_color(db_session):
    db_session.add(ConversationLabel(name="Priority", slug="priority", color="rose", is_active=True))
    db_session.commit()

    payload = [{"id": "1", "tags": ["Priority", "Unknown"]}]
    enrich_formatted_conversations_with_labels(db_session, payload)

    assert payload[0]["labels"] == [
        {"name": "Priority", "color": "rose", "managed": True},
        {"name": "Unknown", "color": "slate", "managed": False},
    ]


def test_web_label_create_route_redirects_success(db_session):
    req = _Req(roles=["admin"])

    response = asyncio.run(
        crm_inbox_settings.create_inbox_label(
            req,
            name="Escalation",
            color="red",
            db=db_session,
        )
    )

    assert response.status_code == 303
    assert response.headers.get("location") == "/admin/crm/inbox/settings?label_setup=1"

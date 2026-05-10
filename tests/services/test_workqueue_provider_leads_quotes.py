from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.crm.enums import LeadStatus, QuoteStatus
from app.services.workqueue.providers.leads_quotes import leads_quotes_provider
from app.services.workqueue.types import ItemKind, WorkqueueAudience


@pytest.fixture
def user():
    return SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view"})


def test_kind(user):
    assert leads_quotes_provider.kind is ItemKind.lead


def test_quote_expires_today(db_session, user, quote_factory):
    quote_factory(
        owner_person_id=user.person_id,
        status=QuoteStatus.sent,
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )
    items = leads_quotes_provider.fetch(db_session, user=user, audience=WorkqueueAudience.self_, snoozed_ids=set())
    assert any(i.reason == "quote_expires_today" and i.score == 85 for i in items)


def test_lead_overdue_followup(db_session, user, lead_factory):
    lead_factory(
        owner_person_id=user.person_id,
        status=LeadStatus.contacted,
        next_action_at=datetime.now(UTC) - timedelta(hours=1),
    )
    items = leads_quotes_provider.fetch(db_session, user=user, audience=WorkqueueAudience.self_, snoozed_ids=set())
    assert any(i.reason == "lead_overdue_followup" and i.score == 70 for i in items)


def test_returns_two_kinds_in_one_call(db_session, user, lead_factory, quote_factory):
    lead_factory(
        owner_person_id=user.person_id,
        next_action_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    quote_factory(
        owner_person_id=user.person_id,
        status=QuoteStatus.sent,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    items = leads_quotes_provider.fetch(db_session, user=user, audience=WorkqueueAudience.self_, snoozed_ids=set())
    kinds = {i.kind for i in items}
    assert {ItemKind.lead, ItemKind.quote} <= kinds

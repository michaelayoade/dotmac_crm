from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.crm.enums import LeadStatus, QuoteStatus
from app.services.workqueue.providers.leads_quotes import leads_quotes_provider
from app.services.workqueue.scope import get_workqueue_scope
from app.services.workqueue.types import ItemKind, WorkqueueAudience


@pytest.fixture
def user():
    return SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view"}, roles=set(), region="North")


def test_kind(user):
    assert leads_quotes_provider.kind is ItemKind.lead


def test_quote_expires_today(db_session, user, quote_factory):
    quote_factory(
        owner_person_id=user.person_id,
        status=QuoteStatus.sent,
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )
    audience = WorkqueueAudience.self_
    items = leads_quotes_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
    assert any(i.reason == "quote_expires_today" and i.score == 85 for i in items)


def test_lead_overdue_followup(db_session, user, lead_factory):
    lead_factory(
        owner_person_id=uuid4(),
        status=LeadStatus.contacted,
        next_action_at=datetime.now(UTC) - timedelta(hours=1),
        region="North",
    )
    audience = WorkqueueAudience.self_
    items = leads_quotes_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
    assert any(i.reason == "lead_overdue_followup" and i.score == 70 for i in items)


def test_self_leads_filter_by_user_region_case_insensitive(db_session, user, lead_factory):
    matching = lead_factory(
        owner_person_id=uuid4(),
        status=LeadStatus.contacted,
        next_action_at=datetime.now(UTC) - timedelta(hours=1),
        region=" north ",
    )
    lead_factory(
        owner_person_id=user.person_id,
        status=LeadStatus.contacted,
        next_action_at=datetime.now(UTC) - timedelta(hours=1),
        region="South",
    )
    audience = WorkqueueAudience.self_
    items = leads_quotes_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
    assert {item.item_id for item in items if item.kind is ItemKind.lead} == {matching.id}


def test_self_leads_without_user_region_returns_no_leads(db_session, lead_factory):
    user = SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view"}, roles=set(), region=None)
    lead_factory(
        owner_person_id=user.person_id,
        status=LeadStatus.contacted,
        next_action_at=datetime.now(UTC) - timedelta(hours=1),
        region="North",
    )
    audience = WorkqueueAudience.self_
    items = leads_quotes_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
    assert [item for item in items if item.kind is ItemKind.lead] == []


def test_returns_two_kinds_in_one_call(db_session, user, lead_factory, quote_factory):
    lead_factory(
        owner_person_id=user.person_id,
        next_action_at=datetime.now(UTC) - timedelta(minutes=5),
        region="North",
    )
    quote_factory(
        owner_person_id=user.person_id,
        status=QuoteStatus.sent,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    audience = WorkqueueAudience.self_
    items = leads_quotes_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
    kinds = {i.kind for i in items}
    assert {ItemKind.lead, ItemKind.quote} <= kinds

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.workqueue.providers.conversations import conversations_provider
from app.services.workqueue.types import ItemKind, WorkqueueAudience


@pytest.fixture
def user():
    return SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view"})


def test_provider_kind(user):
    assert conversations_provider.kind is ItemKind.conversation


def test_returns_empty_when_no_conversations(db_session, user):
    items = conversations_provider.fetch(db_session, user=user, audience=WorkqueueAudience.self_, snoozed_ids=set())
    assert items == []


def test_sla_breach_scores_100(db_session, user, crm_conversation_factory):
    conv = crm_conversation_factory(
        assignee_person_id=user.person_id,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=5),
        last_inbound_at=datetime.now(UTC) - timedelta(minutes=15),
    )
    items = conversations_provider.fetch(db_session, user=user, audience=WorkqueueAudience.self_, snoozed_ids=set())
    assert len(items) == 1
    item = items[0]
    assert item.item_id == conv.id
    assert item.score == 100
    assert item.reason == "sla_breach"
    assert item.urgency == "critical"


def test_snoozed_ids_excluded(db_session, user, crm_conversation_factory):
    conv = crm_conversation_factory(assignee_person_id=user.person_id)
    items = conversations_provider.fetch(
        db_session,
        user=user,
        audience=WorkqueueAudience.self_,
        snoozed_ids={conv.id},
    )
    assert items == []


def test_audience_team_includes_unassigned(db_session, user, crm_conversation_factory):
    crm_conversation_factory(assignee_person_id=None)
    crm_conversation_factory(assignee_person_id=uuid4())
    items = conversations_provider.fetch(db_session, user=user, audience=WorkqueueAudience.team, snoozed_ids=set())
    assert len(items) == 2


def test_results_sorted_by_score_desc(db_session, user, crm_conversation_factory):
    crm_conversation_factory(
        assignee_person_id=user.person_id,
        sla_due_at=datetime.now(UTC) + timedelta(minutes=2),
    )
    crm_conversation_factory(
        assignee_person_id=user.person_id,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    items = conversations_provider.fetch(db_session, user=user, audience=WorkqueueAudience.self_, snoozed_ids=set())
    assert [i.score for i in items] == [100, 90]

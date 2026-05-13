from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.crm.team import CrmTeam
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamType
from app.services.workqueue.providers.conversations import conversations_provider
from app.services.workqueue.scope import get_workqueue_scope
from app.services.workqueue.types import ItemKind, WorkqueueAudience


@pytest.fixture
def user():
    return SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view"}, roles=set())


def test_provider_kind(user):
    assert conversations_provider.kind is ItemKind.conversation


def test_returns_empty_when_no_conversations(db_session, user):
    audience = WorkqueueAudience.self_
    items = conversations_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
    assert items == []


def test_sla_breach_scores_100(db_session, user, crm_conversation_factory):
    conv = crm_conversation_factory(
        assignee_person_id=user.person_id,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=5),
        last_inbound_at=datetime.now(UTC) - timedelta(minutes=15),
    )
    audience = WorkqueueAudience.self_
    items = conversations_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
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
        scope=get_workqueue_scope(db_session, user, WorkqueueAudience.self_),
        snoozed_ids={conv.id},
    )
    assert items == []


def test_audience_team_includes_unassigned(db_session, user, crm_conversation_factory):
    service_team = ServiceTeam(name="Support", team_type=ServiceTeamType.support, is_active=True)
    db_session.add(service_team)
    db_session.flush()
    crm_team = CrmTeam(name="CRM Support", service_team_id=service_team.id, is_active=True)
    db_session.add(crm_team)
    db_session.flush()
    db_session.add(ServiceTeamMember(team_id=service_team.id, person_id=user.person_id, is_active=True))
    db_session.commit()

    crm_conversation_factory(assignment_team_id=crm_team.id)
    other_team = CrmTeam(name="Other CRM", is_active=True)
    db_session.add(other_team)
    db_session.commit()
    crm_conversation_factory(assignment_team_id=other_team.id)
    items = conversations_provider.fetch(
        db_session,
        user=user,
        audience=WorkqueueAudience.team,
        scope=get_workqueue_scope(db_session, user, WorkqueueAudience.team),
        snoozed_ids=set(),
    )
    assert len(items) == 1


def test_results_sorted_by_score_desc(db_session, user, crm_conversation_factory):
    crm_conversation_factory(
        assignee_person_id=user.person_id,
        sla_due_at=datetime.now(UTC) + timedelta(minutes=2),
    )
    crm_conversation_factory(
        assignee_person_id=user.person_id,
        sla_due_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    audience = WorkqueueAudience.self_
    items = conversations_provider.fetch(
        db_session,
        user=user,
        audience=audience,
        scope=get_workqueue_scope(db_session, user, audience),
        snoozed_ids=set(),
    )
    assert [i.score for i in items] == [100, 90]

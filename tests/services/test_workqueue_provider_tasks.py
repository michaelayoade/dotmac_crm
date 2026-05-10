from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.projects import TaskStatus
from app.services.workqueue.providers.tasks import tasks_provider
from app.services.workqueue.types import ItemKind, WorkqueueAudience


@pytest.fixture
def user():
    return SimpleNamespace(person_id=uuid4(), permissions={"workqueue:view"})


def test_kind(user):
    assert tasks_provider.kind is ItemKind.task


def test_overdue_task(db_session, user, project_task_factory):
    project_task_factory(
        assignee_person_id=user.person_id,
        status=TaskStatus.in_progress,
        due_at=datetime.now(UTC) - timedelta(hours=1),
    )
    items = tasks_provider.fetch(
        db_session, user=user, audience=WorkqueueAudience.self_, snoozed_ids=set()
    )
    assert items[0].reason == "overdue" and items[0].score == 80


def test_due_today_task(db_session, user, project_task_factory):
    project_task_factory(
        assignee_person_id=user.person_id,
        status=TaskStatus.in_progress,
        due_at=datetime.now(UTC) + timedelta(hours=4),
    )
    items = tasks_provider.fetch(
        db_session, user=user, audience=WorkqueueAudience.self_, snoozed_ids=set()
    )
    assert items[0].reason == "due_today" and items[0].score == 70

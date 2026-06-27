"""Quick-win API endpoints: system health, CRM analytics reports, contact merge."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.models.person import Person
from app.services import system_health as system_health_service

# ── system health ────────────────────────────────────────────────────────────


def test_system_health_report_includes_evaluation(db_session):
    report = system_health_service.system_health_report(db_session)
    assert "evaluation" in report
    assert report["evaluation"]["status"] in ("ok", "warning", "critical")


def test_system_health_route(db_session):
    from app.api.system import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    res = client.get("/system/health")
    assert res.status_code == 200
    assert "evaluation" in res.json()


# ── CRM analytics reports ────────────────────────────────────────────────────


@pytest.fixture()
def reports_client(db_session):
    from app.api.crm.reports import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    [
        "/crm/reports/agent-performance",
        "/crm/reports/agent-weekly",
        "/crm/reports/conversation-trend",
        "/crm/reports/sales-pipeline",
        "/crm/reports/sales-forecast",
        "/crm/reports/agent-sales",
    ],
)
def test_analytics_endpoints_return_200(reports_client, path):
    res = reports_client.get(path)
    assert res.status_code == 200
    assert isinstance(res.json(), list | dict)


# ── contact merge ────────────────────────────────────────────────────────────


@pytest.fixture()
def contacts_client(db_session):
    from app.api.crm.contacts import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: {"person_id": None}
    return TestClient(app)


def _person(db, name):
    p = Person(first_name=name, last_name="Test", email=f"{name.lower()}@example.com")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_merge_contacts(contacts_client, db_session):
    src = _person(db_session, "Source")
    dst = _person(db_session, "Target")
    res = contacts_client.post(
        "/crm/contacts/merge",
        json={"source_person_id": str(src.id), "target_person_id": str(dst.id)},
    )
    assert res.status_code == 200
    assert res.json()["merged_person_id"] == str(dst.id)


def test_merge_same_contact_is_rejected(contacts_client, db_session):
    person = _person(db_session, "Solo")
    res = contacts_client.post(
        "/crm/contacts/merge",
        json={"source_person_id": str(person.id), "target_person_id": str(person.id)},
    )
    assert res.status_code == 400

"""Campaigns JSON API — thin wrappers over the campaigns service managers."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db


@pytest.fixture()
def client(db_session):
    from app.api.crm.campaigns import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: {"person_id": None}
    return TestClient(app)


def _create(client, name="Spring promo"):
    res = client.post("/crm/campaigns", json={"name": name})
    assert res.status_code == 201, res.json()
    return res.json()


def test_campaign_crud_and_list(client):
    created = _create(client)
    cid = created["id"]
    assert created["status"] == "draft"

    assert client.get(f"/crm/campaigns/{cid}").status_code == 200

    listed = client.get("/crm/campaigns").json()
    assert listed["count"] >= 1
    assert any(c["id"] == cid for c in listed["items"])

    upd = client.patch(f"/crm/campaigns/{cid}", json={"subject": "Hello"})
    assert upd.status_code == 200
    assert upd.json()["subject"] == "Hello"


def test_campaign_schedule_then_cancel(client):
    cid = _create(client, "Scheduled")["id"]
    sched = client.post(f"/crm/campaigns/{cid}/schedule", json={"scheduled_at": "2099-01-01T09:00:00Z"})
    assert sched.status_code == 200
    assert sched.json()["status"] == "scheduled"

    cancelled = client.post(f"/crm/campaigns/{cid}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_campaign_steps(client):
    # Steps are a nurture-campaign feature.
    res = client.post("/crm/campaigns", json={"name": "With steps", "campaign_type": "nurture"})
    cid = res.json()["id"]
    step = client.post("/crm/campaigns/steps", json={"campaign_id": cid, "step_index": 0, "name": "Day 1"})
    assert step.status_code == 201, step.json()
    steps = client.get(f"/crm/campaigns/{cid}/steps").json()
    assert steps["count"] == 1


def test_recipients_endpoint(client):
    cid = _create(client, "Recip")["id"]
    res = client.get(f"/crm/campaigns/{cid}/recipients")
    assert res.status_code == 200
    assert res.json()["count"] == 0

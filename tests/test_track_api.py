"""Public Track My Visit JSON API (/api/v1/track) — thin token-authed wrappers."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import get_db
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services.field import tracking


def _work_order(db, status=WorkOrderStatus.scheduled):
    wo = WorkOrder(title="Install ONT", status=status)
    db.add(wo)
    db.commit()
    db.refresh(wo)
    return wo


@pytest.fixture()
def api_client(db_session):
    from app.api.track import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def test_get_visit_state(api_client, db_session):
    token = tracking.tokens.get_or_create(db_session, _work_order(db_session)).token
    res = api_client.get(f"/api/v1/track/{token}")
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is True
    assert "timeline" in body and "destination" in body and "status" in body


def test_confirm(api_client, db_session):
    token = tracking.tokens.get_or_create(db_session, _work_order(db_session)).token
    res = api_client.post(f"/api/v1/track/{token}/confirm")
    assert res.status_code == 200
    assert res.json()["confirmed"] is True


def test_reschedule(api_client, db_session):
    token = tracking.tokens.get_or_create(db_session, _work_order(db_session)).token
    res = api_client.post(f"/api/v1/track/{token}/reschedule", json={"preferred_window": "Tuesday AM"})
    assert res.status_code == 200
    assert res.json()["requested"] is True


def test_unknown_token_404(api_client):
    assert api_client.get("/api/v1/track/not-a-real-token").status_code == 404


def test_confirm_rejected_on_completed_visit(api_client, db_session):
    wo = _work_order(db_session, status=WorkOrderStatus.completed)
    token = tracking.tokens.get_or_create(db_session, wo).token
    assert api_client.post(f"/api/v1/track/{token}/confirm").status_code == 409

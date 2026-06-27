"""Fiber-change-request submission JSON API."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db


@pytest.fixture()
def client(db_session):
    from app.api.fiber_change_requests import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: {"person_id": None}
    return TestClient(app)


def test_submit_list_and_get(client):
    body = {"asset_type": "fdh_cabinet", "operation": "create", "payload": {"name": "New FDH"}}
    res = client.post("/fiber-change-requests", json=body)
    assert res.status_code == 201, res.json()
    rid = res.json()["id"]
    assert res.json()["status"] == "pending"

    assert client.get(f"/fiber-change-requests/{rid}").status_code == 200

    listed = client.get("/fiber-change-requests", params={"status": "pending"})
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_unsupported_asset_type_rejected(client):
    res = client.post(
        "/fiber-change-requests",
        json={"asset_type": "not_a_real_asset", "operation": "create", "payload": {}},
    )
    assert res.status_code == 400

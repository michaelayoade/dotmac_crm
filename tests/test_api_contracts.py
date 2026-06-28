"""Contracts (click-to-sign) JSON API — thin wrappers over contracts service."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_db


@pytest.fixture()
def client(db_session):
    from app.api.contracts import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def test_create_read_and_list_signature(client):
    account_id = str(uuid.uuid4())
    payload = {
        "account_id": account_id,
        "signer_name": "Jane Doe",
        "signer_email": "jane@example.com",
        "ip_address": "203.0.113.5",
        "agreement_text": "I agree to the terms of service.",
    }
    created = client.post("/contracts/signatures", json=payload)
    assert created.status_code == 201, created.json()
    sig_id = created.json()["id"]

    assert client.get(f"/contracts/signatures/{sig_id}").status_code == 200

    listed = client.get("/contracts/signatures", params={"account_id": account_id})
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_template_404_when_none_published(client):
    assert client.get("/contracts/template").status_code == 404

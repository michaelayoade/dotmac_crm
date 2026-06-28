"""Contracts (click-to-sign) JSON API — thin wrappers over contracts service."""

import uuid

from fastapi import HTTPException

from app.api import contracts as contracts_api
from app.schemas.contracts import ContractSignatureCreate


def test_create_read_and_list_signature(db_session):
    account_id = str(uuid.uuid4())
    payload = ContractSignatureCreate(
        account_id=account_id,
        signer_name="Jane Doe",
        signer_email="jane@example.com",
        ip_address="203.0.113.5",
        agreement_text="I agree to the terms of service.",
    )
    created = contracts_api.create_signature(payload, db_session)
    sig_id = str(created.id)

    assert contracts_api.get_signature(sig_id, db_session).id == created.id

    listed = contracts_api.list_signatures(account_id=account_id, limit=100, offset=0, db=db_session)
    assert len(listed) == 1


def test_template_404_when_none_published(db_session):
    try:
        contracts_api.get_contract_template(db=db_session)
    except HTTPException as exc:
        assert exc.status_code == 404
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected missing template to raise 404")

"""Fiber-change-request submission JSON API."""

import pytest
from fastapi import HTTPException

from app.api import fiber_change_requests as fiber_api
from app.models.fiber_change_request import FiberChangeRequestStatus


@pytest.fixture()
def auth():
    return {"person_id": None}


def test_submit_list_and_get(db_session, auth):
    payload = fiber_api.FiberChangeRequestCreate(
        asset_type="fdh_cabinet",
        operation="create",
        payload={"name": "New FDH"},
    )
    created = fiber_api.submit_request(payload, db_session, auth)
    assert created.status == FiberChangeRequestStatus.pending

    assert fiber_api.get_request(str(created.id), db_session).id == created.id

    listed = fiber_api.list_requests(status="pending", db=db_session)
    assert len(listed) == 1
    assert listed[0].id == created.id


def test_unsupported_asset_type_rejected(db_session, auth):
    payload = fiber_api.FiberChangeRequestCreate(
        asset_type="not_a_real_asset",
        operation="create",
        payload={},
    )
    with pytest.raises(HTTPException) as exc_info:
        fiber_api.submit_request(payload, db_session, auth)
    assert exc_info.value.status_code == 400

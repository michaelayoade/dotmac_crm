"""Public Track My Visit JSON API (/api/v1/track) — thin token-authed wrappers."""

import pytest
from fastapi import HTTPException

from app.api import track as track_api
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services.field import tracking


def _work_order(db, status=WorkOrderStatus.scheduled):
    wo = WorkOrder(title="Install ONT", status=status)
    db.add(wo)
    db.commit()
    db.refresh(wo)
    return wo


def test_get_visit_state(db_session):
    token = tracking.tokens.get_or_create(db_session, _work_order(db_session)).token
    body = track_api.get_visit_state(token, db_session)
    assert body["available"] is True
    assert "timeline" in body and "destination" in body and "status" in body


def test_confirm(db_session):
    token = tracking.tokens.get_or_create(db_session, _work_order(db_session)).token
    assert track_api.confirm_visit(token, db_session)["confirmed"] is True


def test_reschedule(db_session):
    token = tracking.tokens.get_or_create(db_session, _work_order(db_session)).token
    payload = track_api.TrackRescheduleRequest(preferred_window="Tuesday AM")
    assert track_api.reschedule_visit(token, payload, db_session)["requested"] is True


def test_unknown_token_404(db_session):
    with pytest.raises(HTTPException) as exc_info:
        track_api.get_visit_state("not-a-real-token", db_session)
    assert exc_info.value.status_code == 404


def test_confirm_rejected_on_completed_visit(db_session):
    wo = _work_order(db_session, status=WorkOrderStatus.completed)
    token = tracking.tokens.get_or_create(db_session, wo).token
    with pytest.raises(HTTPException) as exc_info:
        track_api.confirm_visit(token, db_session)
    assert exc_info.value.status_code == 409

"""Mobile-agent inbox actions API — saved filters, snooze, run-macro guards."""

import uuid

import pytest
from fastapi import HTTPException

from app.api.crm import inbox_actions as ia
from app.models.person import Person


def _person(db):
    p = Person(first_name="Ada", last_name="Agent", email=f"ada-{uuid.uuid4().hex[:8]}@example.com")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_saved_filters_crud(db_session):
    auth = {"person_id": str(_person(db_session).id)}
    assert ia.list_saved_filters(db_session, auth) == []

    saved = ia.create_saved_filter(ia.SavedFilterCreate(name="My open", params={"status": "open"}), db_session, auth)
    assert saved["name"] == "My open"

    listed = ia.list_saved_filters(db_session, auth)
    assert len(listed) == 1
    filter_id = listed[0]["id"]

    ia.delete_saved_filter(filter_id, db_session, auth)
    assert ia.list_saved_filters(db_session, auth) == []


def test_saved_filters_require_auth(db_session):
    with pytest.raises(HTTPException) as exc:
        ia.list_saved_filters(db_session, None)
    assert exc.value.status_code == 401


def test_snooze_missing_conversation_404(db_session):
    with pytest.raises(HTTPException) as exc:
        ia.snooze(str(uuid.uuid4()), ia.SnoozeRequest(preset="1_hour"), db_session, {"person_id": None})
    assert exc.value.status_code == 404


def test_run_macro_missing_macro_404(db_session):
    with pytest.raises(HTTPException) as exc:
        ia.run_macro(str(uuid.uuid4()), ia.RunMacroRequest(macro_id=str(uuid.uuid4())), db_session, {"person_id": None})
    assert exc.value.status_code == 404

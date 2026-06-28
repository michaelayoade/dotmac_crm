"""Reseller portal API — reseller-role access guard."""

import uuid

import pytest
from fastapi import HTTPException

from app.api import reseller_portal as reseller_api
from app.models.person import Person


def _person(db):
    p = Person(first_name="Rita", last_name="Seller", email=f"rita-{uuid.uuid4().hex[:8]}@example.com")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_unauthenticated_is_401(db_session):
    with pytest.raises(HTTPException) as exc:
        reseller_api._reseller_actor(auth=None, db=db_session)
    assert exc.value.status_code == 401


def test_non_reseller_person_is_403(db_session):
    person = _person(db_session)
    with pytest.raises(HTTPException) as exc:
        reseller_api._reseller_actor(auth={"person_id": str(person.id)}, db=db_session)
    assert exc.value.status_code == 403

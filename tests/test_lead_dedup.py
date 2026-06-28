"""Tests for lead dedup: one open lead per person."""

from __future__ import annotations

import uuid

from app.models.crm.enums import LeadStatus
from app.models.crm.sales import Lead, Pipeline
from app.models.person import Person
from app.schemas.crm.sales import LeadCreate, LeadUpdate
from app.services.crm.sales import service as svc_mod
from app.services.crm.sales.service import leads


def _person(db) -> Person:
    p = Person(first_name="L", last_name="D", email=f"l-{uuid.uuid4().hex[:8]}@example.com")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _pipeline(db, name="Sales") -> Pipeline:
    pl = Pipeline(name=name)
    db.add(pl)
    db.commit()
    db.refresh(pl)
    return pl


def _count(db, person_id) -> int:
    return db.query(Lead).filter(Lead.person_id == person_id).count()


def test_second_open_lead_returns_existing(db_session):
    person = _person(db_session)
    first = leads.create(db_session, LeadCreate(person_id=person.id, title="Deal A"))
    second = leads.create(db_session, LeadCreate(person_id=person.id, title="Deal B"))
    assert second.id == first.id
    assert _count(db_session, person.id) == 1
    db_session.refresh(second)
    assert second.metadata_ and second.metadata_.get("dedup_hits") == 1


def test_closed_lead_allows_a_new_one(db_session):
    person = _person(db_session)
    first = leads.create(db_session, LeadCreate(person_id=person.id, title="Deal A"))
    leads.update(db_session, str(first.id), LeadUpdate(status="won"))

    second = leads.create(db_session, LeadCreate(person_id=person.id, title="Deal B"))
    assert second.id != first.id
    assert _count(db_session, person.id) == 2


def test_different_people_are_separate(db_session):
    p1 = _person(db_session)
    p2 = _person(db_session)
    leads.create(db_session, LeadCreate(person_id=p1.id, title="A"))
    leads.create(db_session, LeadCreate(person_id=p2.id, title="B"))
    assert _count(db_session, p1.id) == 1
    assert _count(db_session, p2.id) == 1


def test_different_pipeline_is_allowed(db_session):
    person = _person(db_session)
    pa = _pipeline(db_session, "A")
    pb = _pipeline(db_session, "B")
    leads.create(db_session, LeadCreate(person_id=person.id, title="A", pipeline_id=pa.id))
    second = leads.create(db_session, LeadCreate(person_id=person.id, title="B", pipeline_id=pb.id))
    # Different pipelines → not a duplicate.
    assert _count(db_session, person.id) == 2
    assert second.pipeline_id == pb.id


def test_dedup_disabled_allows_duplicates(db_session, monkeypatch):
    def _resolve(db, domain, key, use_cache=True):
        if key == "lead_dedup_enabled":
            return False
        return None

    monkeypatch.setattr(svc_mod.settings_spec, "resolve_value", _resolve)

    person = _person(db_session)
    leads.create(db_session, LeadCreate(person_id=person.id, title="A"))
    leads.create(db_session, LeadCreate(person_id=person.id, title="B"))
    assert _count(db_session, person.id) == 2

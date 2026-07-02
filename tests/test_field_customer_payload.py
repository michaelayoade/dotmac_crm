"""Technicians must always get a reachable phone + a findable address, even for
thin/migrated subscriber records."""

import uuid

from app.models.person import ChannelType, Person, PersonChannel
from app.models.subscriber import Subscriber
from app.services.field.jobs import _best_phone, _site_address


def _person(db, **kw) -> Person:
    p = Person(first_name="A", last_name="B", email=f"p-{uuid.uuid4().hex[:10]}@example.com", **kw)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_best_phone_prefers_person_phone(db_session):
    p = _person(db_session, phone="+2348010000000")
    assert _best_phone(p) == "+2348010000000"


def test_best_phone_falls_back_to_phone_channel(db_session):
    p = _person(db_session)  # no phone field
    db_session.add(PersonChannel(person_id=p.id, channel_type=ChannelType.phone, address="+2348029999999"))
    db_session.commit()
    db_session.refresh(p)
    assert _best_phone(p) == "+2348029999999"


def test_site_address_falls_back_to_person_address(db_session):
    person = _person(db_session, address_line1="12 Person St", city="Lagos")
    sub = Subscriber(person_id=person.id, external_system="selfcare", external_id=uuid.uuid4().hex[:8])
    # no service_address_* set on the subscriber
    db_session.add(sub)
    db_session.commit()
    addr = _site_address(sub, person)
    assert addr is not None and "12 Person St" in addr


def test_site_address_prefers_service_address(db_session):
    person = _person(db_session, address_line1="12 Person St")
    sub = Subscriber(
        person_id=person.id,
        external_system="selfcare",
        external_id=uuid.uuid4().hex[:8],
        service_address_line1="99 Service Rd",
        service_city="Abuja",
    )
    db_session.add(sub)
    db_session.commit()
    addr = _site_address(sub, person)
    assert "99 Service Rd" in addr and "Person St" not in addr

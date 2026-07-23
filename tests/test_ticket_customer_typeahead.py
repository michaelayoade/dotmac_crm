from __future__ import annotations

import json
import uuid

from app.models.person import Person, PersonStatus
from app.models.subscriber import Subscriber
from app.services import typeahead
from app.web.admin.tickets import ticket_customer_lookup


def _person(db, *, name: str, email: str, phone: str | None = None, metadata=None, status=PersonStatus.active):
    person = Person(
        first_name=name,
        last_name="Customer",
        display_name=f"{name} Customer",
        email=email,
        phone=phone,
        metadata_=metadata,
        status=status,
    )
    db.add(person)
    db.flush()
    return person


def test_ticket_customer_search_does_not_trust_legacy_person_metadata(db_session):
    stale = _person(
        db_session,
        name="John",
        email=f"john-{uuid.uuid4().hex[:8]}@example.com",
        metadata={"splynx_id": "9541"},
    )
    for index in range(10):
        _person(
            db_session,
            name=f"Noise{index}",
            email=f"noise-{index}-{uuid.uuid4().hex[:6]}@example.com",
            phone=f"+2348009541{index:02d}",
        )
    db_session.commit()

    results = typeahead.ticket_people(db_session, "9541", limit=8)

    assert all(item["id"] != stale.id for item in results)


def test_ticket_customer_search_ranks_current_person_linked_to_canonical_subscriber(db_session):
    canonical = _person(
        db_session,
        name="Harry",
        email=f"harry-{uuid.uuid4().hex[:8]}@example.com",
    )
    subscriber = Subscriber(
        person_id=canonical.id,
        external_system="selfcare",
        external_id="selfcare-uuid",
        subscriber_number="100009541",
        is_active=True,
    )
    db_session.add(subscriber)
    db_session.commit()

    results = typeahead.ticket_people(db_session, "9541", limit=8)

    assert results[0]["id"] == canonical.id
    assert results[0]["label"].endswith("· 100009541")


def test_ticket_customer_search_excludes_archived_people(db_session):
    archived = _person(
        db_session,
        name="Archived",
        email=f"archived-{uuid.uuid4().hex[:8]}@example.com",
        metadata={"splynx_id": "9541"},
        status=PersonStatus.archived,
    )
    db_session.commit()

    results = typeahead.ticket_people(db_session, "9541", limit=8)

    assert all(item["id"] != archived.id for item in results)


def test_ticket_subscriber_search_hides_inactive_legacy_mirror_and_ranks_canonical_number(db_session):
    customer = _person(
        db_session,
        name="Canonical",
        email=f"canonical-{uuid.uuid4().hex[:8]}@example.com",
        metadata={"splynx_id": "9541"},
    )
    canonical = Subscriber(
        person_id=customer.id,
        external_system="selfcare",
        external_id="selfcare-uuid",
        subscriber_number="100009541",
        sync_metadata={"selfcare_name": "2dotcom Solutions (Harry Adetoyi)"},
        is_active=True,
    )
    legacy = Subscriber(
        external_system="splynx",
        external_id="9541",
        is_active=False,
    )
    db_session.add_all([canonical, legacy])
    db_session.commit()

    results = typeahead.ticket_subscribers(db_session, "9541", limit=8)

    assert [item["id"] for item in results] == [canonical.id]
    assert "2dotcom Solutions (Harry Adetoyi)" in results[0]["label"]
    assert "100009541" in results[0]["label"]


def test_ticket_search_shows_authoritative_sub_name_not_stale_person(db_session):
    stale_john = _person(
        db_session,
        name="John",
        email=f"john-{uuid.uuid4().hex[:8]}@example.com",
        metadata={"splynx_id": "9541"},
    )
    stale_john.display_name = "2dotcom Solutions (John Arikpo)"
    subscriber = Subscriber(
        person_id=stale_john.id,
        external_system="selfcare",
        external_id="selfcare-uuid",
        subscriber_number="100009541",
        sync_metadata={
            "selfcare_name": "2dotcom Solutions (Harry Adetoyi)",
            "selfcare_address": "Katampe road, Mpape, Abuja, Nigeria",
            "selfcare_location": "Eagle FM",
        },
        is_active=True,
    )
    db_session.add(subscriber)
    db_session.commit()

    customer_results = typeahead.ticket_people(db_session, "9541", limit=8)
    subscriber_results = typeahead.ticket_subscribers(db_session, "9541", limit=8)

    assert all(item["id"] != stale_john.id for item in customer_results)
    assert subscriber_results == [
        {
            "id": subscriber.id,
            "label": "2dotcom Solutions (Harry Adetoyi) (100009541)",
        }
    ]


def test_ticket_lookup_uses_authoritative_projection_when_linked_person_conflicts(db_session):
    stale_john = _person(
        db_session,
        name="John",
        email=f"john-{uuid.uuid4().hex[:8]}@example.com",
    )
    stale_john.display_name = "2dotcom Solutions (John Arikpo)"
    subscriber = Subscriber(
        person_id=stale_john.id,
        external_system="selfcare",
        external_id="selfcare-uuid",
        subscriber_number="100009541",
        service_address_line1="Katampe road, Mpape, Abuja",
        sync_metadata={
            "selfcare_name": "2dotcom Solutions (Harry Adetoyi)",
            "selfcare_address": "Katampe road, Mpape, Abuja, Nigeria",
            "selfcare_location": "Eagle FM",
        },
    )
    db_session.add(subscriber)
    db_session.commit()

    response = ticket_customer_lookup(
        request=None,
        db=db_session,
        customer_person_id=None,
        subscriber_id=str(subscriber.id),
    )
    payload = json.loads(response.body)

    assert payload["customer"]["name"] == "2dotcom Solutions (Harry Adetoyi)"
    assert payload["customer"]["street"] == "Katampe road, Mpape, Abuja, Nigeria"
    assert payload["customer"]["location"] == "Eagle FM"
    assert payload["subscriber"]["service_address"] == "Katampe road, Mpape, Abuja, Nigeria"

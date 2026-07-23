from __future__ import annotations

import uuid

from app.models.person import Person, PersonStatus
from app.models.subscriber import Subscriber
from app.services import typeahead


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


def test_ticket_customer_search_ranks_exact_legacy_identity_before_substring_noise(db_session):
    canonical = _person(
        db_session,
        name="Canonical",
        email=f"canonical-{uuid.uuid4().hex[:8]}@example.com",
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

    assert results[0]["id"] == canonical.id
    assert results[0]["label"].endswith("· ID 9541")


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
    assert "Canonical Customer" in results[0]["label"]
    assert "100009541" in results[0]["label"]

from __future__ import annotations

import uuid

from app.models.person import Person, PersonMergeLog, PersonStatus
from app.models.subscriber import Subscriber
from app.services.external_systems import (
    selfcare_subscriber_number_for_splynx_id,
    splynx_id_from_selfcare_subscriber_number,
)
from app.services.selfcare import _resolve_person_for_selfcare_customer
from app.services.subscriber import subscriber as subscriber_service


def _person(db, *, metadata=None, status=PersonStatus.active, email: str | None = None) -> Person:
    person = Person(
        first_name="Identity",
        last_name=uuid.uuid4().hex[:8],
        email=email or f"identity-{uuid.uuid4().hex[:10]}@example.com",
        metadata_=metadata,
        status=status,
    )
    db.add(person)
    db.flush()
    return person


def _record_merge_source(db, source: Person) -> Person:
    target = _person(db)
    db.add(PersonMergeLog(source_person_id=source.id, target_person_id=target.id))
    db.flush()
    return target


def test_migrated_subscriber_number_round_trip():
    assert selfcare_subscriber_number_for_splynx_id("9541") == "100009541"
    assert splynx_id_from_selfcare_subscriber_number("100009541") == "9541"
    assert splynx_id_from_selfcare_subscriber_number("SUB-9541") is None
    assert splynx_id_from_selfcare_subscriber_number("1009541") is None


def test_reconcile_repairs_archived_email_match_from_unique_legacy_identity(db_session):
    archived_wrong_person = _person(db_session, status=PersonStatus.archived)
    _record_merge_source(db_session, archived_wrong_person)
    canonical_person = _person(db_session, metadata={"splynx_id": "9541"})
    subscriber = Subscriber(
        person_id=archived_wrong_person.id,
        external_system="selfcare",
        external_id="selfcare-uuid-9541",
        subscriber_number="100009541",
        sync_metadata={"selfcare_name": f"{canonical_person.first_name} {canonical_person.last_name}"},
    )
    db_session.add(subscriber)
    db_session.commit()

    result = subscriber_service.reconcile_external_people_links(
        db_session,
        external_system="selfcare",
        repair_legacy_merge_sources=True,
        subscriber_number="100009541",
        target_person_id=canonical_person.id,
    )

    db_session.refresh(subscriber)
    db_session.refresh(canonical_person)
    assert subscriber.person_id == canonical_person.id
    assert canonical_person.metadata_["selfcare_id"] == "selfcare-uuid-9541"
    assert result["legacy_identity_matches"] == 1
    assert result["linked_subscribers"] == 1


def test_reconcile_refuses_ambiguous_legacy_identity(db_session):
    archived_wrong_person = _person(db_session, status=PersonStatus.archived)
    _record_merge_source(db_session, archived_wrong_person)
    first = _person(db_session, metadata={"splynx_id": "9541"})
    second = _person(db_session, metadata={"splynx_id": "9541"})
    subscriber = Subscriber(
        person_id=archived_wrong_person.id,
        external_system="selfcare",
        external_id="selfcare-uuid-9541",
        subscriber_number="100009541",
    )
    db_session.add(subscriber)
    db_session.commit()

    result = subscriber_service.reconcile_external_people_links(
        db_session,
        external_system="selfcare",
        repair_legacy_merge_sources=True,
    )

    db_session.refresh(subscriber)
    assert first.id != second.id
    assert subscriber.person_id == archived_wrong_person.id
    assert result["ambiguous_legacy_identity_matches"] == 1
    assert result["linked_subscribers"] == 0


def test_reconcile_refuses_legacy_person_when_authoritative_sub_name_conflicts(db_session):
    archived_wrong_person = _person(db_session, status=PersonStatus.archived)
    _record_merge_source(db_session, archived_wrong_person)
    john = _person(db_session, metadata={"splynx_id": "9541"})
    john.first_name = "2dotcom"
    john.last_name = "Solutions (John Arikpo)"
    john.display_name = "2dotcom Solutions (John Arikpo)"
    subscriber = Subscriber(
        person_id=archived_wrong_person.id,
        external_system="selfcare",
        external_id="selfcare-uuid-9541",
        subscriber_number="100009541",
        sync_metadata={"selfcare_name": "2dotcom Solutions (Harry Adetoyi)"},
    )
    db_session.add(subscriber)
    db_session.commit()

    result = subscriber_service.reconcile_external_people_links(
        db_session,
        external_system="selfcare",
        repair_legacy_merge_sources=True,
        subscriber_number="100009541",
        target_person_id=john.id,
    )

    db_session.refresh(subscriber)
    assert subscriber.person_id == archived_wrong_person.id
    assert result["legacy_identity_matches"] == 0
    assert result["conflicting_legacy_identity_matches"] == 1
    assert result["linked_subscribers"] == 0


def test_reconcile_does_not_bulk_link_unlinked_legacy_candidates(db_session):
    _person(db_session, metadata={"splynx_id": "9541"})
    subscriber = Subscriber(
        external_system="selfcare",
        external_id="selfcare-uuid-9541",
        subscriber_number="100009541",
    )
    db_session.add(subscriber)
    db_session.commit()

    result = subscriber_service.reconcile_external_people_links(
        db_session,
        external_system="selfcare",
        repair_legacy_merge_sources=True,
    )

    db_session.refresh(subscriber)
    assert subscriber.person_id is None
    assert result["legacy_identity_matches"] == 0
    assert result["linked_subscribers"] == 0


def test_reconcile_does_not_reassign_archived_person_without_merge_evidence(db_session):
    archived_person = _person(db_session, status=PersonStatus.archived)
    _person(db_session, metadata={"splynx_id": "9541"})
    subscriber = Subscriber(
        person_id=archived_person.id,
        external_system="selfcare",
        external_id="selfcare-uuid-9541",
        subscriber_number="100009541",
    )
    db_session.add(subscriber)
    db_session.commit()

    result = subscriber_service.reconcile_external_people_links(
        db_session,
        external_system="selfcare",
        repair_legacy_merge_sources=True,
    )

    db_session.refresh(subscriber)
    assert subscriber.person_id == archived_person.id
    assert result["legacy_identity_matches"] == 0
    assert result["linked_subscribers"] == 0


def test_reconcile_prefers_selfcare_identity_over_existing_manual_link(db_session):
    existing_person = _person(db_session)
    canonical_person = _person(db_session, metadata={"selfcare_id": "selfcare-uuid"})
    subscriber = Subscriber(
        person_id=existing_person.id,
        external_system="selfcare",
        external_id="selfcare-uuid",
        subscriber_number="NATIVE-1",
    )
    db_session.add(subscriber)
    db_session.commit()

    result = subscriber_service.reconcile_external_people_links(db_session, external_system="selfcare")

    db_session.refresh(subscriber)
    assert subscriber.person_id == canonical_person.id
    assert result["linked_subscribers"] == 1


def test_selfcare_sync_preserves_explicit_legacy_repair(db_session):
    archived_wrong_person = _person(db_session, status=PersonStatus.archived)
    _record_merge_source(db_session, archived_wrong_person)
    canonical_person = _person(db_session, metadata={"splynx_id": "9541"})
    subscriber = Subscriber(
        person_id=archived_wrong_person.id,
        external_system="selfcare",
        external_id="selfcare-uuid-9541",
        subscriber_number="100009541",
        sync_metadata={"selfcare_name": f"{canonical_person.first_name} {canonical_person.last_name}"},
    )
    db_session.add(subscriber)
    db_session.commit()
    subscriber_service.reconcile_external_people_links(
        db_session,
        external_system="selfcare",
        repair_legacy_merge_sources=True,
        subscriber_number="100009541",
        target_person_id=canonical_person.id,
    )
    db_session.refresh(subscriber)

    resolved = _resolve_person_for_selfcare_customer(
        db_session,
        {
            "id": "selfcare-uuid-9541",
            "subscriber_number": "100009541",
            "email": archived_wrong_person.email,
        },
        existing_subscriber=subscriber,
    )

    assert resolved is not None
    assert resolved.id == canonical_person.id


def test_selfcare_sync_preserves_current_existing_link_without_strong_identity(db_session):
    existing_person = _person(db_session)
    other_person = _person(db_session)
    subscriber = Subscriber(
        person_id=existing_person.id,
        external_system="selfcare",
        external_id="selfcare-uuid",
        subscriber_number="NATIVE-1",
    )
    db_session.add(subscriber)
    db_session.commit()

    resolved = _resolve_person_for_selfcare_customer(
        db_session,
        {
            "id": "selfcare-uuid",
            "subscriber_number": "NATIVE-1",
            "email": other_person.email,
        },
        existing_subscriber=subscriber,
    )

    assert resolved is not None
    assert resolved.id == existing_person.id


def test_selfcare_sync_does_not_match_placeholder_email(db_session):
    _person(db_session, email="shared@placeholder.local")
    db_session.commit()

    resolved = _resolve_person_for_selfcare_customer(
        db_session,
        {"id": "selfcare-uuid", "email": "shared@placeholder.local"},
    )

    assert resolved is None

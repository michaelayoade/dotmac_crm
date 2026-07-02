"""Tests for the splynx -> selfcare convergence status report."""

import uuid

from app.models.person import Person
from app.models.subscriber import Subscriber
from app.services.splynx_convergence import convergence_status


def _person(db, **meta) -> Person:
    p = Person(first_name="C", last_name="R", email=f"p-{uuid.uuid4().hex[:8]}@example.com", metadata_=meta or None)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _subscriber(db, person, external_system) -> Subscriber:
    s = Subscriber(
        person_id=person.id,
        external_system=external_system,
        external_id=uuid.uuid4().hex[:8],
        subscriber_number=f"N-{uuid.uuid4().hex[:8]}",
    )
    db.add(s)
    db.commit()
    return s


def test_status_counts_and_not_converged(db_session):
    p1 = _person(db_session, splynx_id="17897")  # legacy, not yet backfilled
    p2 = _person(db_session, splynx_id="25431", selfcare_id="uuid-a")  # converged identity
    _person(db_session, selfcare_id="uuid-b")  # native selfcare
    _subscriber(db_session, p1, "splynx")
    _subscriber(db_session, p2, "selfcare")

    status = convergence_status(db_session)

    assert status["subscribers_by_external_system"].get("splynx") == 1
    assert status["subscribers_by_external_system"].get("selfcare") == 1
    assert status["subscribers_remaining_splynx"] == 1
    assert status["people_with_splynx_id"] == 2
    assert status["people_splynx_id_without_selfcare_id"] == 1  # only p1
    assert status["converged"] is False


def test_status_converged_when_no_splynx_remains(db_session):
    p = _person(db_session, selfcare_id="uuid-c")
    _subscriber(db_session, p, "selfcare")

    status = convergence_status(db_session)

    assert status["subscribers_remaining_splynx"] == 0
    assert status["people_splynx_id_without_selfcare_id"] == 0
    assert status["converged"] is True


def _keyed_subscriber(db, external_system, external_id, subscriber_number) -> Subscriber:
    s = Subscriber(
        external_system=external_system,
        external_id=external_id,
        subscriber_number=subscriber_number,
    )
    db.add(s)
    db.commit()
    return s


def test_dedupe_soft_deletes_only_duplicates_with_twin(db_session):
    from app.services.splynx_convergence import dedupe_splynx_duplicates

    # splynx 17897 has a canonical selfcare twin (100017897) -> duplicate.
    dup = _keyed_subscriber(db_session, "splynx", "17897", None)
    twin = _keyed_subscriber(db_session, "selfcare", "uuid-twin", "100017897")
    # splynx 99999 has no selfcare twin -> must be left alone.
    orphan = _keyed_subscriber(db_session, "splynx", "99999", None)

    dry = dedupe_splynx_duplicates(db_session, apply=False)
    assert dry["duplicates"] == 1
    assert dry["no_twin"] == 1
    assert dry["soft_deleted"] == 0
    db_session.refresh(dup)
    assert dup.is_active is True  # dry run changed nothing

    applied = dedupe_splynx_duplicates(db_session, apply=True)
    assert applied["soft_deleted"] == 1
    db_session.refresh(dup)
    db_session.refresh(twin)
    db_session.refresh(orphan)
    assert dup.is_active is False  # duplicate retired
    assert twin.is_active is True  # canonical row untouched
    assert orphan.is_active is True  # no-twin splynx row left for review


def test_backfill_person_selfcare_id(db_session):
    from app.services.splynx_convergence import backfill_person_selfcare_id

    # Person with splynx_id only, linked to a selfcare subscriber -> resolvable.
    person = _person(db_session, splynx_id="17897")
    sub = Subscriber(
        person_id=person.id,
        external_system="selfcare",
        external_id="sub-uuid-1",
        subscriber_number="100017897",
    )
    db_session.add(sub)
    # Person with splynx_id only, no linked selfcare subscriber -> unresolvable.
    _person(db_session, splynx_id="99999")
    # Person already converged -> ignored.
    _person(db_session, splynx_id="123", selfcare_id="already")
    db_session.commit()

    dry = backfill_person_selfcare_id(db_session, apply=False)
    assert dry["candidates"] == 1  # only the resolvable one
    db_session.refresh(person)
    assert "selfcare_id" not in (person.metadata_ or {})  # dry run wrote nothing

    applied = backfill_person_selfcare_id(db_session, apply=True)
    assert applied["backfilled"] == 1
    db_session.refresh(person)
    assert person.metadata_["selfcare_id"] == "sub-uuid-1"
    assert person.metadata_["splynx_id"] == "17897"  # preserved

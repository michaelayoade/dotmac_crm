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

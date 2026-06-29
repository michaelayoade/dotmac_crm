"""Tests for CRM→sub contact alignment (re-push on person edit)."""

import uuid
from unittest.mock import MagicMock, patch

from app.models.person import PartyStatus, Person
from app.services.events.handlers import selfcare_customer as sc
from app.services.selfcare import SelfcareCustomerIdentity

IDENTITY = SelfcareCustomerIdentity(selfcare_id="sub-123", subscriber_number="SUB-123")


def _person(db, *, linked=False, **ov) -> Person:
    data = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": f"c-{uuid.uuid4().hex[:10]}@example.com",
        "party_status": PartyStatus.lead,
    }
    data.update(ov)
    person = Person(**data)
    if linked:
        person.metadata_ = {"selfcare_id": "sub-123", "selfcare_subscriber_id": "SUB-123"}
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


# --- resync_person_contact -------------------------------------------------


def test_resync_noop_when_sync_disabled(db_session):
    person = _person(db_session, linked=True)
    with (
        patch.object(sc, "_customer_sync_enabled", return_value=False),
        patch.object(sc, "create_customer") as create,
    ):
        assert sc.resync_person_contact(db_session, str(person.id)) is None
        create.assert_not_called()


def test_resync_noop_when_person_unlinked(db_session):
    person = _person(db_session, linked=False)
    with (
        patch.object(sc, "_customer_sync_enabled", return_value=True),
        patch.object(sc, "create_customer") as create,
    ):
        assert sc.resync_person_contact(db_session, str(person.id)) is None
        create.assert_not_called()


def test_resync_pushes_contact_when_linked(db_session):
    person = _person(db_session, linked=True)
    with (
        patch.object(sc, "_customer_sync_enabled", return_value=True),
        patch.object(sc, "create_customer", return_value=IDENTITY) as create,
        patch.object(sc, "ensure_person_customer") as ensure,
        patch.object(sc, "record_customer_sync_result") as record,
    ):
        result = sc.resync_person_contact(db_session, str(person.id))

    assert result == "SUB-123"
    create.assert_called_once()
    ensure.assert_called_once()
    kwargs = record.call_args.kwargs
    assert kwargs["success"] is True
    assert kwargs["action"] == "contact_updated"


def test_resync_records_failure_when_push_fails(db_session):
    person = _person(db_session, linked=True)
    with (
        patch.object(sc, "_customer_sync_enabled", return_value=True),
        patch.object(sc, "create_customer", return_value=None),
        patch.object(sc, "record_customer_sync_result") as record,
    ):
        assert sc.resync_person_contact(db_session, str(person.id)) is None

    kwargs = record.call_args.kwargs
    assert kwargs["success"] is False
    assert kwargs["action"] == "contact_update_failed"


# --- enqueue_person_contact_resync ----------------------------------------


def test_enqueue_skips_when_no_contact_field_changed(db_session):
    person = _person(db_session, linked=True)
    task = MagicMock()
    with (
        patch.object(sc, "_customer_sync_enabled", return_value=True),
        patch("app.tasks.subscribers.push_selfcare_contact_update", task),
    ):
        sc.enqueue_person_contact_resync(db_session, str(person.id), {"notes", "title"})
        task.delay.assert_not_called()


def test_enqueue_skips_when_unlinked(db_session):
    person = _person(db_session, linked=False)
    task = MagicMock()
    with (
        patch.object(sc, "_customer_sync_enabled", return_value=True),
        patch("app.tasks.subscribers.push_selfcare_contact_update", task),
    ):
        sc.enqueue_person_contact_resync(db_session, str(person.id), {"email"})
        task.delay.assert_not_called()


def test_enqueue_dispatches_for_linked_contact_change(db_session):
    person = _person(db_session, linked=True)
    task = MagicMock()
    with (
        patch.object(sc, "_customer_sync_enabled", return_value=True),
        patch("app.tasks.subscribers.push_selfcare_contact_update", task),
        patch.object(sc, "create_customer") as create,
    ):
        sc.enqueue_person_contact_resync(db_session, str(person.id), {"email", "notes"})
        task.delay.assert_called_once_with(str(person.id))
        create.assert_not_called()  # enqueued, not run inline


def test_enqueue_inline_fallback_when_broker_down(db_session):
    person = _person(db_session, linked=True)
    task = MagicMock()
    task.delay.side_effect = RuntimeError("broker down")
    with (
        patch.object(sc, "_customer_sync_enabled", return_value=True),
        patch("app.tasks.subscribers.push_selfcare_contact_update", task),
        patch.object(sc, "create_customer", return_value=IDENTITY) as create,
        patch.object(sc, "ensure_person_customer"),
        patch.object(sc, "record_customer_sync_result"),
    ):
        sc.enqueue_person_contact_resync(db_session, str(person.id), {"phone"})
        create.assert_called_once()  # fell back to inline resync


# --- reconcile_person_contacts (backfill) ----------------------------------


def test_reconcile_backfills_only_linked_customers(db_session):
    from app.models.person import PartyStatus

    linked = _person(db_session, linked=True)
    linked.party_status = PartyStatus.customer
    unlinked = _person(db_session, linked=False)
    unlinked.party_status = PartyStatus.customer
    db_session.commit()

    pushed_ids = []
    with (
        patch.object(sc, "_customer_sync_enabled", return_value=True),
        patch.object(
            sc,
            "resync_person_contact",
            side_effect=lambda db, pid, **k: (pushed_ids.append(pid), "SUB")[1],
        ),
    ):
        result = sc.reconcile_person_contacts(db_session)

    assert str(linked.id) in pushed_ids
    assert str(unlinked.id) not in pushed_ids  # filtered by _selfcare_identity
    assert result["pushed"] == 1


def test_reconcile_noop_when_disabled(db_session):
    with patch.object(sc, "_customer_sync_enabled", return_value=False):
        result = sc.reconcile_person_contacts(db_session)
    assert result.get("skipped") == "sync_disabled"

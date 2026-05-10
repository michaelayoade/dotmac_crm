from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.workqueue import WorkqueueItemKind, WorkqueueSnooze
from app.services.workqueue.snooze import workqueue_snooze
from app.services.workqueue.types import ItemKind


def test_snooze_until_creates_row(db_session):
    user_id = uuid4()
    item_id = uuid4()
    until = datetime.now(UTC) + timedelta(hours=1)

    workqueue_snooze.snooze(db_session, user_id, ItemKind.conversation, item_id, until=until)

    row = db_session.query(WorkqueueSnooze).filter_by(user_id=user_id, item_id=item_id).one()
    assert row.item_kind == WorkqueueItemKind.conversation
    assert row.snooze_until is not None
    assert row.until_next_reply is False


def test_snooze_until_next_reply_sets_flag(db_session):
    workqueue_snooze.snooze(db_session, uuid4(), ItemKind.conversation, uuid4(), until_next_reply=True)
    row = db_session.query(WorkqueueSnooze).one()
    assert row.until_next_reply is True
    assert row.snooze_until is None


def test_snooze_requires_exactly_one_mode(db_session):
    with pytest.raises(ValueError):
        workqueue_snooze.snooze(db_session, uuid4(), ItemKind.ticket, uuid4())

    with pytest.raises(ValueError):
        workqueue_snooze.snooze(
            db_session,
            uuid4(),
            ItemKind.ticket,
            uuid4(),
            until=datetime.now(UTC) + timedelta(hours=1),
            until_next_reply=True,
        )


def test_snooze_upserts_on_same_user_item(db_session):
    user_id = uuid4()
    item_id = uuid4()
    workqueue_snooze.snooze(
        db_session,
        user_id,
        ItemKind.task,
        item_id,
        until=datetime.now(UTC) + timedelta(hours=1),
    )
    new_until = datetime.now(UTC) + timedelta(hours=5)
    workqueue_snooze.snooze(db_session, user_id, ItemKind.task, item_id, until=new_until)
    rows = db_session.query(WorkqueueSnooze).filter_by(user_id=user_id, item_id=item_id).all()
    assert len(rows) == 1
    # Normalize both to naive UTC for comparison (DB may strip tzinfo on read-back)
    stored = rows[0].snooze_until
    stored_naive = stored.replace(tzinfo=None) if stored.tzinfo else stored
    new_until_naive = new_until.replace(tzinfo=None)
    assert abs((stored_naive - new_until_naive).total_seconds()) < 1


def test_clear_snooze(db_session):
    user_id = uuid4()
    item_id = uuid4()
    workqueue_snooze.snooze(
        db_session,
        user_id,
        ItemKind.task,
        item_id,
        until=datetime.now(UTC) + timedelta(hours=1),
    )
    workqueue_snooze.clear(db_session, user_id, ItemKind.task, item_id)
    assert db_session.query(WorkqueueSnooze).filter_by(user_id=user_id, item_id=item_id).count() == 0


def test_active_snoozed_ids_filters_expired(db_session):
    user_id = uuid4()
    active_id = uuid4()
    expired_id = uuid4()

    workqueue_snooze.snooze(
        db_session,
        user_id,
        ItemKind.ticket,
        active_id,
        until=datetime.now(UTC) + timedelta(hours=1),
    )
    expired = WorkqueueSnooze(
        user_id=user_id,
        item_kind=WorkqueueItemKind.ticket,
        item_id=expired_id,
        snooze_until=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(expired)
    db_session.commit()

    ids_by_kind = workqueue_snooze.active_snoozed_ids(db_session, user_id)
    assert active_id in ids_by_kind[ItemKind.ticket]
    assert expired_id not in ids_by_kind[ItemKind.ticket]


def test_active_snoozed_ids_includes_until_next_reply(db_session):
    user_id = uuid4()
    item_id = uuid4()
    workqueue_snooze.snooze(
        db_session,
        user_id,
        ItemKind.conversation,
        item_id,
        until_next_reply=True,
    )
    ids_by_kind = workqueue_snooze.active_snoozed_ids(db_session, user_id)
    assert item_id in ids_by_kind[ItemKind.conversation]


def test_clear_until_next_reply_for_conversation(db_session):
    user_a = uuid4()
    user_b = uuid4()
    conv_id = uuid4()
    workqueue_snooze.snooze(db_session, user_a, ItemKind.conversation, conv_id, until_next_reply=True)
    workqueue_snooze.snooze(db_session, user_b, ItemKind.conversation, conv_id, until_next_reply=True)

    cleared = workqueue_snooze.clear_until_next_reply_for_conversation(db_session, conv_id)

    assert set(cleared) == {user_a, user_b}
    assert db_session.query(WorkqueueSnooze).filter_by(until_next_reply=True).count() == 0

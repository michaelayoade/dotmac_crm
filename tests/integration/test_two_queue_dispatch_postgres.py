"""PostgreSQL-only locking checks for the two-queue dispatcher.

These deliberately use independent sessions. SQLite cannot validate row-lock
semantics, so they skip unless ``TEST_DATABASE_URL`` selects PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ConversationQueueType, ConversationStatus
from app.models.crm.queue import ConversationQueueDispatchState
from app.services.crm.inbox import dispatch


def _require_postgres(engine) -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("requires TEST_DATABASE_URL backed by PostgreSQL")


def _seed_state(db_session) -> None:
    for queue_type in ConversationQueueType:
        if db_session.get(ConversationQueueDispatchState, queue_type) is None:
            db_session.add(ConversationQueueDispatchState(queue_type=queue_type))
    db_session.commit()


def test_second_worker_cannot_skip_locked_fifo_head(engine, db_session, crm_contact):
    _require_postgres(engine)
    _seed_state(db_session)
    first = Conversation(
        person_id=crm_contact.id, status=ConversationStatus.open, is_active=True, created_at=datetime.now(UTC)
    )
    second = Conversation(
        person_id=crm_contact.id, status=ConversationStatus.open, is_active=True, created_at=datetime.now(UTC)
    )
    db_session.add_all([first, second])
    db_session.flush()
    dispatch.enqueue_classified(
        db_session, conversation=first, queue_type=ConversationQueueType.support, notify_initial=False
    )
    dispatch.enqueue_classified(
        db_session, conversation=second, queue_type=ConversationQueueType.support, notify_initial=False
    )
    db_session.commit()

    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    first_worker = factory()
    second_worker = factory()
    try:
        dispatch._dispatch_state(first_worker, ConversationQueueType.support, lock=True)
        head = (
            first_worker.query(dispatch.ConversationQueueEntry)
            .filter_by(queue_type=ConversationQueueType.support, state="waiting")
            .order_by(dispatch.ConversationQueueEntry.original_arrival_at, dispatch.ConversationQueueEntry.id)
            .with_for_update()
            .first()
        )
        assert head is not None
        second_worker.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError):
            dispatch._dispatch_state(second_worker, ConversationQueueType.support, lock=True)
        second_worker.rollback()
    finally:
        first_worker.rollback()
        first_worker.close()
        second_worker.close()

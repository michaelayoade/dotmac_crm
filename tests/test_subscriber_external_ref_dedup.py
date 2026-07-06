"""Regression tests for deterministic external-ref resolution + create lock (I-2).

The CRM local Subscriber mirror indexes ``(external_system, external_id)``
non-uniquely, so two reconcile/sync passes for the same external ref could each
see "not found" and both insert, and ``get_by_external_id`` then resolved the
duplicate arbitrarily via an unordered ``.first()``. The fix orders resolution
(oldest wins) and takes a transaction-level advisory lock around the
get-or-create. These tests pin both behaviours.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.subscriber import Subscriber, SubscriberStatus
from app.services.subscriber import subscriber as subscriber_service


def _make_subscriber(db_session, external_id: str, created_at: datetime) -> Subscriber:
    sub = Subscriber(
        external_system="selfcare",
        external_id=external_id,
        subscriber_number=f"SUB-{uuid4().hex[:8]}",
        status=SubscriberStatus.active,
        is_active=True,
        created_at=created_at,
    )
    db_session.add(sub)
    db_session.flush()
    return sub


def test_get_by_external_id_resolves_oldest_deterministically(db_session) -> None:
    now = datetime.now(UTC)
    ext = f"ext-{uuid4().hex}"
    older = _make_subscriber(db_session, ext, now - timedelta(days=3))
    _newer = _make_subscriber(db_session, ext, now - timedelta(days=1))
    db_session.commit()

    # Resolution must always return the same (oldest) row, not an arbitrary one.
    for _ in range(3):
        resolved = subscriber_service.get_by_external_id(db_session, "selfcare", ext)
        assert resolved is not None
        assert resolved.id == older.id


def test_lock_external_ref_issues_advisory_lock_on_postgres() -> None:
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"

    subscriber_service._lock_external_ref(db, "selfcare", "abc-123")

    assert db.execute.call_count == 1
    sql, params = db.execute.call_args.args
    assert "pg_advisory_xact_lock" in str(sql)
    assert params == {"key": "subscriber_external:selfcare:abc-123"}


def test_lock_external_ref_noop_off_postgres_or_without_id() -> None:
    pg = MagicMock()
    pg.get_bind.return_value.dialect.name = "postgresql"
    subscriber_service._lock_external_ref(pg, "selfcare", "")  # no external_id
    pg.execute.assert_not_called()

    sqlite = MagicMock()
    sqlite.get_bind.return_value.dialect.name = "sqlite"
    subscriber_service._lock_external_ref(sqlite, "selfcare", "abc-123")
    sqlite.execute.assert_not_called()

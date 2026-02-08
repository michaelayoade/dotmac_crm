"""Tests for inbox outbox helpers."""

from app.services.crm.inbox.outbox import _compute_backoff_seconds


def test_compute_backoff_seconds_increases():
    first = _compute_backoff_seconds(1, base=5, max_backoff=20)
    second = _compute_backoff_seconds(2, base=5, max_backoff=20)
    assert second >= first

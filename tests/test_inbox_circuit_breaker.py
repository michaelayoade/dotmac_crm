from datetime import UTC, datetime, timedelta

import pytest

from app.services.crm.inbox.circuit_breaker import CircuitBreaker, CircuitOpenError


def test_circuit_breaker_opens_and_recovers():
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=10)
    now = datetime.now(UTC)
    times = [now, now, now, now + timedelta(seconds=5), now + timedelta(seconds=11)]

    def _now():
        return times.pop(0)

    breaker._now = _now  # type: ignore[method-assign]

    def _fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        breaker.call(_fail)
    with pytest.raises(RuntimeError):
        breaker.call(_fail)

    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "ok")

    # After recovery timeout, allow half-open and then close on success
    assert breaker.call(lambda: "ok") == "ok"

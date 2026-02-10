import pytest

from app.services.crm.inbox.circuit_breaker import CircuitBreaker, CircuitOpenError


def test_circuit_breaker_opens_and_blocks():
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=60)

    def _fail():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        breaker.call(_fail)
    with pytest.raises(ValueError):
        breaker.call(_fail)
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "ok")


def test_circuit_breaker_recovers_after_timeout():
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0)

    def _fail():
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        breaker.call(_fail)

    result = breaker.call(lambda: "ok")
    assert result == "ok"

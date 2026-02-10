import pytest

from app.services.crm.inbox import rate_limit


def test_rate_limit_in_memory(monkeypatch):
    rate_limit._in_memory_store().clear()
    monkeypatch.setattr(rate_limit, "_redis_client", lambda: None)

    times = iter([1000.0, 1000.1, 1000.2])
    monkeypatch.setattr(rate_limit.time, "time", lambda: next(times))

    rate_limit.check_rate_limit("unit", 2)
    rate_limit.check_rate_limit("unit", 2)
    with pytest.raises(rate_limit.RateLimitExceeded) as exc:
        rate_limit.check_rate_limit("unit", 2)
    assert exc.value.retry_after >= 1


def test_build_rate_limit_key_default():
    assert rate_limit.build_rate_limit_key("email", None) == "email:default"

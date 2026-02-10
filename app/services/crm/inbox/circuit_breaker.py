"""Circuit breaker for outbound provider calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class CircuitState:
    failure_count: int = 0
    last_failure_time: datetime | None = None
    opened_at: datetime | None = None
    state: str = "closed"  # closed, open, half-open


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = CircuitState()
        self._lock = Lock()

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _should_open(self) -> bool:
        return self._state.failure_count >= self.failure_threshold

    def _can_retry(self) -> bool:
        reference_time = self._state.opened_at or self._state.last_failure_time
        if not reference_time:
            return True
        return self._now() - reference_time >= timedelta(
            seconds=self.recovery_timeout
        )

    def _on_success(self) -> None:
        self._state.failure_count = 0
        self._state.last_failure_time = None
        self._state.opened_at = None
        self._state.state = "closed"

    def _on_failure(self) -> None:
        self._state.failure_count += 1
        self._state.last_failure_time = self._now()
        if self._should_open():
            self._state.state = "open"
            if self._state.opened_at is None:
                self._state.opened_at = self._now()

    def call(self, func, *args, **kwargs):
        with self._lock:
            if self._state.state == "open":
                if self._can_retry():
                    self._state.state = "half-open"
                else:
                    raise CircuitOpenError("Circuit breaker is open")

        try:
            result = func(*args, **kwargs)
        except Exception:
            with self._lock:
                self._on_failure()
            raise

        with self._lock:
            self._on_success()
        return result

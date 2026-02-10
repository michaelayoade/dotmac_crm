"""Structured logging context for CRM inbox."""

from __future__ import annotations

import contextvars
import functools
import logging
import uuid

from app.logging import get_logger

request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "inbox_request_id", default=""
)


def set_request_id(value: str | None = None) -> str:
    if not value:
        value = uuid.uuid4().hex[:8]
    request_id.set(value)
    return value


def get_request_id() -> str:
    return request_id.get()


class InboxLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = kwargs.get("extra") or {}
        if "request_id" not in extra:
            rid = get_request_id()
            if rid:
                extra["request_id"] = rid
        kwargs["extra"] = extra
        return msg, kwargs


def get_inbox_logger(name: str) -> logging.LoggerAdapter:
    return InboxLoggerAdapter(get_logger(name), {})


def with_inbox_context(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        set_request_id()
        return func(*args, **kwargs)

    return wrapper

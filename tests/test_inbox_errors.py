"""Tests for CRM inbox error taxonomy."""

from fastapi import HTTPException

from app.services.crm.inbox.errors import (
    InboxValidationError,
    InboxNotFoundError,
    as_http_exception,
)


def test_inbox_error_to_http_exception():
    exc = InboxValidationError("invalid", "Bad")
    http_exc = exc.to_http_exception()
    assert isinstance(http_exc, HTTPException)
    assert http_exc.status_code == 400
    assert http_exc.detail == "Bad"


def test_as_http_exception_passthrough():
    exc = InboxNotFoundError("missing", "Not found")
    http_exc = as_http_exception(exc)
    assert http_exc.status_code == 404
    assert http_exc.detail == "Not found"

"""Unified error taxonomy for CRM inbox services."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException


@dataclass(frozen=True)
class InboxError(Exception):
    code: str
    detail: str
    status_code: int = 400
    retryable: bool = False

    def to_http_exception(self) -> HTTPException:
        return HTTPException(status_code=self.status_code, detail=self.detail)


class InboxValidationError(InboxError):
    def __init__(self, code: str, detail: str):
        super().__init__(code=code, detail=detail, status_code=400, retryable=False)


class InboxNotFoundError(InboxError):
    def __init__(self, code: str, detail: str):
        super().__init__(code=code, detail=detail, status_code=404, retryable=False)


class InboxAuthError(InboxError):
    def __init__(self, code: str, detail: str):
        super().__init__(code=code, detail=detail, status_code=401, retryable=False)


class InboxRateLimitError(InboxError):
    def __init__(self, code: str, detail: str):
        super().__init__(code=code, detail=detail, status_code=429, retryable=True)


class InboxTransientError(InboxError):
    def __init__(self, code: str, detail: str, status_code: int = 503):
        super().__init__(code=code, detail=detail, status_code=status_code, retryable=True)


class InboxConfigError(InboxError):
    def __init__(self, code: str, detail: str):
        super().__init__(code=code, detail=detail, status_code=400, retryable=False)


class InboxExternalError(InboxError):
    def __init__(self, code: str, detail: str, status_code: int = 502, retryable: bool = True):
        super().__init__(code=code, detail=detail, status_code=status_code, retryable=retryable)


def as_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, InboxError):
        return exc.to_http_exception()
    if isinstance(exc, HTTPException):
        return exc
    return HTTPException(status_code=500, detail=str(exc) or "Inbox error")

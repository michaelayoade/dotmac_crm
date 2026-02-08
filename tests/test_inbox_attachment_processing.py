"""Tests for inbox attachment processing helpers."""

import pytest

from app.services.crm.inbox.attachments_processing import _validate_attachment_payload
from app.services.crm.inbox.errors import InboxValidationError


def test_validate_attachment_payload_missing_fields():
    with pytest.raises(InboxValidationError):
        _validate_attachment_payload({"file_name": "a.txt"})
    with pytest.raises(InboxValidationError):
        _validate_attachment_payload({"stored_name": "a", "mime_type": "text/plain"})
    with pytest.raises(InboxValidationError):
        _validate_attachment_payload({"stored_name": "a", "file_name": "a.txt"})
    with pytest.raises(InboxValidationError):
        _validate_attachment_payload({"stored_name": "a", "file_name": "a.txt", "mime_type": "text/plain"})

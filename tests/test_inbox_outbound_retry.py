"""Tests for outbound retry helpers."""

from unittest.mock import patch

import pytest

from app.services.crm.inbox.outbound import (
    PermanentOutboundError,
    TransientOutboundError,
    send_message_with_retry,
)


def test_send_message_with_retry_retries_transient():
    with (
        patch(
            "app.services.crm.inbox.outbound.send_message",
            side_effect=[TransientOutboundError("fail"), "ok"],
        ) as mock_send,
        patch("app.services.crm.inbox.outbound._sleep_with_backoff"),
    ):
        result = send_message_with_retry(
            None,
            payload=None,
            author_id=None,
            max_attempts=2,
        )
    assert result == "ok"
    assert mock_send.call_count == 2


def test_send_message_with_retry_raises_permanent():
    with patch(
        "app.services.crm.inbox.outbound.send_message",
        side_effect=PermanentOutboundError("bad"),
    ) as mock_send, pytest.raises(PermanentOutboundError):
        send_message_with_retry(
            None,
            payload=None,
            author_id=None,
            max_attempts=3,
        )
    assert mock_send.call_count == 1


def test_send_message_with_retry_invalid_attempts():
    with pytest.raises(ValueError):
        send_message_with_retry(
            None,
            payload=None,
            author_id=None,
            max_attempts=0,
        )

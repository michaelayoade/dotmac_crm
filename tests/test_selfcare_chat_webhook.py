"""CRM wakes a backgrounded dotmac_sub app on agent chat replies.

Regression for the chat-push gap: an agent reply must fan out to the
subscriber's devices via the selfcare webhook (no manually-registered generic
webhook subscription), signed with the shared selfcare secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import requests

from app.services import selfcare


def _config():
    return {
        "base_url": "https://sub.example",
        "webhook_path": "/api/v1/webhooks/crm/customers",
        "webhook_secret": "s3cret",
        "timeout_seconds": 5,
    }


class _Resp:
    def raise_for_status(self):
        return None


def test_notify_chat_message_posts_signed(db_session, monkeypatch):
    monkeypatch.setattr(selfcare, "_get_config", lambda db: _config())
    captured: dict = {}

    def _post(url, data, headers, timeout):
        captured.update(url=url, data=data, headers=headers, timeout=timeout)
        return _Resp()

    monkeypatch.setattr(requests, "post", _post)

    ok = selfcare.notify_chat_message(db_session, subscriber_id="s1", conversation_id="c1", preview="Hello there")
    assert ok is True
    assert captured["url"] == "https://sub.example/api/v1/webhooks/crm/chat"
    assert captured["headers"]["X-Webhook-Event"] == "message.outbound"
    expected = "sha256=" + hmac.new(b"s3cret", captured["data"], hashlib.sha256).hexdigest()
    assert captured["headers"]["X-Webhook-Signature-256"] == expected
    body = json.loads(captured["data"])
    assert body == {
        "subscriber_id": "s1",
        "conversation_id": "c1",
        "preview": "Hello there",
    }


def test_notify_chat_message_no_config_is_noop(db_session, monkeypatch):
    monkeypatch.setattr(selfcare, "_get_config", lambda db: None)
    assert selfcare.notify_chat_message(db_session, subscriber_id="s1", conversation_id="c1", preview="x") is False


def test_notify_chat_message_requires_a_target(db_session, monkeypatch):
    monkeypatch.setattr(selfcare, "_get_config", lambda db: _config())
    # Neither subscriber_id nor reseller_id → no-op.
    assert selfcare.notify_chat_message(db_session, conversation_id="c1", preview="x") is False
    assert selfcare.notify_chat_message(db_session, subscriber_id="", conversation_id="c1", preview="x") is False


def test_notify_chat_message_reseller_posts_reseller_id(db_session, monkeypatch):
    monkeypatch.setattr(selfcare, "_get_config", lambda db: _config())
    captured: dict = {}

    def _post(url, data, headers, timeout):
        captured.update(url=url, data=data, headers=headers, timeout=timeout)
        return _Resp()

    monkeypatch.setattr(requests, "post", _post)

    ok = selfcare.notify_chat_message(db_session, reseller_id="r1", conversation_id="c9", preview="hi")
    assert ok is True
    assert captured["url"] == "https://sub.example/api/v1/webhooks/crm/chat"
    body = json.loads(captured["data"])
    assert body == {"conversation_id": "c9", "preview": "hi", "reseller_id": "r1"}
    assert "subscriber_id" not in body


def test_notify_chat_message_swallows_errors(db_session, monkeypatch):
    monkeypatch.setattr(selfcare, "_get_config", lambda db: _config())

    def _boom(*a, **k):
        raise requests.RequestException("down")

    monkeypatch.setattr(requests, "post", _boom)
    assert selfcare.notify_chat_message(db_session, subscriber_id="s1", conversation_id="c1", preview="x") is False

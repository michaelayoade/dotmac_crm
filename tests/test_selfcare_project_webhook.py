"""CRM emits project lifecycle webhooks to dotmac_sub (Installation tracker)."""

from __future__ import annotations

import hashlib
import hmac

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


def test_notify_project_event_posts_signed(db_session, monkeypatch):
    monkeypatch.setattr(selfcare, "_get_config", lambda db: _config())
    captured: dict = {}

    def _post(url, data, headers, timeout):
        captured.update(url=url, data=data, headers=headers, timeout=timeout)
        return _Resp()

    monkeypatch.setattr(requests, "post", _post)

    ok = selfcare.notify_project_event(db_session, "project.completed", {"project_id": "p1", "subscriber_id": "s1"})
    assert ok is True
    assert captured["url"] == "https://sub.example/api/v1/webhooks/crm/projects"
    assert captured["headers"]["X-Webhook-Event"] == "project.completed"
    expected = "sha256=" + hmac.new(b"s3cret", captured["data"], hashlib.sha256).hexdigest()
    assert captured["headers"]["X-Webhook-Signature-256"] == expected


def test_notify_project_event_no_config_is_noop(db_session, monkeypatch):
    monkeypatch.setattr(selfcare, "_get_config", lambda db: None)
    assert selfcare.notify_project_event(db_session, "project.created", {}) is False


def test_notify_project_event_swallows_errors(db_session, monkeypatch):
    monkeypatch.setattr(selfcare, "_get_config", lambda db: _config())

    def _boom(*a, **k):
        raise requests.RequestException("down")

    monkeypatch.setattr(requests, "post", _boom)
    assert selfcare.notify_project_event(db_session, "project.updated", {}) is False

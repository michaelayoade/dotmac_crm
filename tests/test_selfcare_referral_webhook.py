"""CRM emits referral lifecycle webhooks to dotmac_sub (RFC #73)."""

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


def test_notify_referral_event_posts_signed(db_session, monkeypatch):
    monkeypatch.setattr(selfcare, "_get_config", lambda db: _config())
    captured: dict = {}

    def _post(url, data, headers, timeout):
        captured.update(url=url, data=data, headers=headers, timeout=timeout)
        return _Resp()

    monkeypatch.setattr(requests, "post", _post)

    ok = selfcare.notify_referral_event(db_session, "referral.rewarded", {"referral_id": "r1", "subscriber_id": "s1"})
    assert ok is True
    assert captured["url"] == "https://sub.example/api/v1/webhooks/crm/referrals"
    assert captured["headers"]["X-Webhook-Event"] == "referral.rewarded"
    sig = captured["headers"]["X-Webhook-Signature-256"]
    expected = "sha256=" + hmac.new(b"s3cret", captured["data"], hashlib.sha256).hexdigest()
    assert sig == expected


def test_notify_referral_event_no_config_is_noop(db_session, monkeypatch):
    monkeypatch.setattr(selfcare, "_get_config", lambda db: None)
    assert selfcare.notify_referral_event(db_session, "referral.captured", {}) is False


def test_notify_referral_event_swallows_errors(db_session, monkeypatch):
    monkeypatch.setattr(selfcare, "_get_config", lambda db: _config())

    def _boom(*a, **k):
        raise requests.RequestException("down")

    monkeypatch.setattr(requests, "post", _boom)
    # Best-effort: a delivery failure must not raise (reconcile is the backstop).
    assert selfcare.notify_referral_event(db_session, "referral.qualified", {}) is False

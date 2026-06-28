"""Tests for the selfcare/dotmac_sub integration hardening."""

import pytest

from app.models.subscriber import Subscriber, SubscriberStatus
from app.services import selfcare

# ── status mapping (#15) ──────────────────────────────────────────────────────


def test_unknown_status_maps_to_pending_not_active():
    assert selfcare._map_selfcare_status("some_new_upstream_status") == SubscriberStatus.pending.value
    assert selfcare._map_selfcare_status(None) == SubscriberStatus.pending.value
    assert selfcare._map_selfcare_status("disabled") == SubscriberStatus.terminated.value
    assert selfcare._map_selfcare_status("active") == SubscriberStatus.active.value


# ── client hardening helpers (#22/#24) ────────────────────────────────────────


def test_validate_base_url():
    assert selfcare._validate_base_url("https://sub.example.com") == "https://sub.example.com"
    for bad in ("ftp://x", "not-a-url", "file:///etc/passwd", ""):
        with pytest.raises(selfcare.SelfcareProviderError):
            selfcare._validate_base_url(bad)


def test_redact_params_hides_pii():
    redacted = selfcare._redact_params({"q": "jane@example.com", "limit": 50})
    assert redacted == {"q": "***", "limit": 50}


def test_enc_url_encodes_ids():
    assert selfcare._enc("a/b c") == "a%2Fb%20c"


# ── retry/backoff (#20) ───────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status, body="{}"):
        self.status_code = status
        self.text = body

    def json(self):
        return {"ok": True}


def _patch_request(monkeypatch, handler):
    import requests

    monkeypatch.setattr(
        selfcare, "_get_api_config", lambda db: {"base_url": "http://x", "api_token": "t", "timeout_seconds": 5}
    )
    monkeypatch.setattr(selfcare, "_sleep_backoff", lambda attempt: None)
    monkeypatch.setattr(requests, "request", handler)
    return requests


def test_request_retries_then_succeeds(monkeypatch):
    import requests

    calls = {"n": 0}

    def handler(method, url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectionError("boom")
        return _Resp(200)

    _patch_request(monkeypatch, handler)
    assert selfcare._request_json(None, "GET", "/x") == {"ok": True}
    assert calls["n"] == 3


def test_request_does_not_retry_4xx(monkeypatch):
    calls = {"n": 0}

    def handler(method, url, **kw):
        calls["n"] += 1
        return _Resp(404, "nope")

    _patch_request(monkeypatch, handler)
    with pytest.raises(selfcare.SelfcareProviderError):
        selfcare._request_json(None, "GET", "/x")
    assert calls["n"] == 1  # 4xx is a caller error → no retry


def test_error_message_omits_upstream_body(monkeypatch):
    def handler(method, url, **kw):
        return _Resp(500, "INTERNAL STACKTRACE LEAK")

    _patch_request(monkeypatch, handler)
    monkeypatch.setattr(selfcare, "_MAX_ATTEMPTS", 1)
    with pytest.raises(selfcare.SelfcareProviderError) as exc:
        selfcare._request_json(None, "GET", "/x")
    assert "STACKTRACE" not in str(exc.value)


# ── pagination (#21) ──────────────────────────────────────────────────────────


def test_pagination_follows_last_page_meta(monkeypatch):
    pages = {
        1: {"data": [{"id": 1}], "meta": {"last_page": 3, "total": 3}},
        2: {"data": [{"id": 2}], "meta": {"last_page": 3, "total": 3}},
        3: {"data": [{"id": 3}], "meta": {"last_page": 3, "total": 3}},
    }
    monkeypatch.setattr(
        selfcare, "_request_json", lambda db, m, p, *, params=None, json_body=None: pages[params["page"]]
    )
    rows = selfcare._list_paginated(None, "/x", {"per_page": 1})
    assert [r["id"] for r in rows] == [1, 2, 3]


def test_pagination_stops_on_short_page_without_meta(monkeypatch):
    pages = {1: {"data": [{"id": 1}, {"id": 2}]}, 2: {"data": [{"id": 3}]}}
    monkeypatch.setattr(
        selfcare, "_request_json", lambda db, m, p, *, params=None, json_body=None: pages[params["page"]]
    )
    rows = selfcare._list_paginated(None, "/x", {"per_page": 2})
    assert len(rows) == 3  # short page 2 (1 < per_page 2) is the last page


# ── orphan reconciliation (#14) ───────────────────────────────────────────────


def _active_sub(db, ext_id):
    sub = Subscriber(
        external_system="selfcare",
        external_id=ext_id,
        subscriber_number=f"SN-{ext_id}",
        status=SubscriberStatus.active,
        is_active=True,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def test_orphans_terminated_when_unseen(db_session):
    subs = [_active_sub(db_session, str(i)) for i in (1, 2, 3)]
    # all seen → nothing terminated
    assert (
        selfcare._reconcile_selfcare_orphans(db_session, {"1", "2", "3"}, fetched_count=3, logger=selfcare.logger) == 0
    )
    # id 3 missing upstream → terminated
    assert selfcare._reconcile_selfcare_orphans(db_session, {"1", "2"}, fetched_count=3, logger=selfcare.logger) == 1
    db_session.refresh(subs[2])
    assert subs[2].status == SubscriberStatus.terminated


def test_orphans_skipped_on_suspiciously_small_fetch(db_session):
    for i in (1, 2, 3, 4):
        _active_sub(db_session, str(i))
    # fetched only 1 of 4 active → likely partial outage → skip, terminate nothing
    assert selfcare._reconcile_selfcare_orphans(db_session, set(), fetched_count=1, logger=selfcare.logger) == 0
    assert db_session.query(Subscriber).filter(Subscriber.status == SubscriberStatus.active).count() == 4


def test_orphans_small_base_respects_ratio_not_flat_floor(db_session):
    # 10 active, 5 unseen — a plausible fetch, but 5 orphans exceeds the small
    # absolute floor → skip. (A flat floor of 10 would have wiped half the base.)
    for i in range(1, 11):
        _active_sub(db_session, str(i))
    seen = {str(i) for i in range(1, 6)}  # ids 1-5 returned, 6-10 missing
    assert selfcare._reconcile_selfcare_orphans(db_session, seen, fetched_count=8, logger=selfcare.logger) == 0
    assert db_session.query(Subscriber).filter(Subscriber.status == SubscriberStatus.active).count() == 10


# ── POST writes are not retried (#87 follow-up: duplicate-write safety) ────────


def test_post_not_retried_on_5xx(monkeypatch):
    calls = {"n": 0}

    def handler(method, url, **kw):
        calls["n"] += 1
        return _Resp(502, "boom")

    _patch_request(monkeypatch, handler)
    with pytest.raises(selfcare.SelfcareProviderError):
        selfcare._request_json(None, "POST", "/invoices", json_body={"x": 1})
    assert calls["n"] == 1  # non-idempotent → no retry


def test_get_retried_on_5xx(monkeypatch):
    calls = {"n": 0}

    def handler(method, url, **kw):
        calls["n"] += 1
        return _Resp(503, "boom")

    _patch_request(monkeypatch, handler)
    with pytest.raises(selfcare.SelfcareProviderError):
        selfcare._request_json(None, "GET", "/x")
    assert calls["n"] == selfcare._MAX_ATTEMPTS  # idempotent → retried

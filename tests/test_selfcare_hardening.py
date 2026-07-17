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
    import httpx

    monkeypatch.setattr(
        selfcare, "_get_api_config", lambda db: {"base_url": "http://x", "api_token": "t", "timeout_seconds": 5}
    )
    monkeypatch.setattr(selfcare, "_sleep_backoff", lambda attempt: None)
    monkeypatch.setattr(httpx.Client, "request", lambda self, method, url, **kw: handler(method, url, **kw))
    return httpx


def test_request_retries_then_succeeds(monkeypatch):
    import httpx

    calls = {"n": 0}

    def handler(method, url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("boom")
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


def test_pagination_rejects_repeated_page_data(monkeypatch):
    calls: list[int] = []

    def fake_request(db, method, path, *, params=None, json_body=None):
        calls.append(params["page"])
        return {"data": [{"id": 1}, {"id": 2}], "meta": {"total": 4}}

    monkeypatch.setattr(selfcare, "_request_json", fake_request)

    with pytest.raises(selfcare.SelfcareProviderError, match="repeated data on page 2"):
        selfcare._list_paginated(None, "/x", {"per_page": 2})

    assert calls == [1, 2]


def test_pagination_rejects_non_advancing_response_page(monkeypatch):
    def fake_request(db, method, path, *, params=None, json_body=None):
        return {
            "data": [{"id": params["page"]}],
            "meta": {"page": 1, "total": 2},
        }

    monkeypatch.setattr(selfcare, "_request_json", fake_request)

    with pytest.raises(selfcare.SelfcareProviderError, match="requested page 2, received page 1"):
        selfcare._list_paginated(None, "/x", {"per_page": 1})


def test_pagination_row_cap_fails_instead_of_returning_partial_data(monkeypatch):
    monkeypatch.setattr(selfcare, "_PAGINATION_MAX_ROWS", 2)
    monkeypatch.setattr(
        selfcare,
        "_request_json",
        lambda db, method, path, *, params=None, json_body=None: {
            "data": [{"id": f"{params['page']}-1"}, {"id": f"{params['page']}-2"}]
        },
    )

    with pytest.raises(selfcare.SelfcareProviderError, match="exceeded 2 rows"):
        selfcare._list_paginated(None, "/x", {"per_page": 2})


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


# ── opt-in idempotent POST retry (P6: safe only for DB-race-safe endpoints) ────


class _IdResp:
    def __init__(self, status, payload):
        self.status_code = status
        self.text = "{}"
        self._payload = payload

    def json(self):
        return self._payload


def test_post_retried_when_idempotent(monkeypatch):
    calls = {"n": 0}

    def handler(method, url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            return _Resp(503, "boom")
        return _Resp(200)

    _patch_request(monkeypatch, handler)
    assert selfcare._request_json(None, "POST", "/x", idempotent=True) == {"ok": True}
    assert calls["n"] == 3  # idempotent=True opts the POST into retry


def test_record_payment_retries_transient_failure(monkeypatch):
    """record_payment targets sub's DB-race-safe /crm/payments, so it opts into
    retry — a transient 503 no longer drops the payment."""
    calls = {"n": 0}

    def handler(method, url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(503, "boom")
        return _IdResp(200, {"id": "pay_1"})

    _patch_request(monkeypatch, handler)
    result = selfcare.record_payment(None, subscriber_id="s1", amount="100", external_ref="ref-1")
    assert result == "pay_1"
    assert calls["n"] == 2  # retried past the transient failure


def test_invoice_post_retries_only_with_external_ref(monkeypatch):
    """/crm/invoices retries only when external_ref anchors the server-side
    unique (uq_invoices_active_crm_external_ref); without a ref it stays
    single-shot (P6)."""
    calls = {"n": 0}

    def handler(method, url, **kw):
        calls["n"] += 1
        return _Resp(503, "boom")

    _patch_request(monkeypatch, handler)
    with pytest.raises(selfcare.SelfcareProviderError):
        selfcare.create_installation_invoice(
            None, subscriber_id="s1", amount="100", description="Install", external_ref="ref-1"
        )
    assert calls["n"] == 3  # ref present → guarded → retried

    calls["n"] = 0
    with pytest.raises(selfcare.SelfcareProviderError):
        selfcare.create_installation_invoice(
            None, subscriber_id="s1", amount="100", description="Install", external_ref=None
        )
    assert calls["n"] == 1  # no ref → unguarded → single-shot


# --- Outbound auth: scoped ApiKey preferred over the legacy shared bearer ---


def test_api_headers_prefer_scoped_key():
    from app.services.selfcare import _api_headers

    headers = _api_headers({"api_key": "sk-scoped", "api_token": "legacy", "base_url": "http://s"})
    assert headers["X-Api-Key"] == "sk-scoped"
    assert "Authorization" not in headers


def test_api_headers_fall_back_to_legacy_bearer():
    from app.services.selfcare import _api_headers

    headers = _api_headers({"api_key": "", "api_token": "legacy", "base_url": "http://s"})
    assert headers["Authorization"] == "Bearer legacy"
    assert "X-Api-Key" not in headers

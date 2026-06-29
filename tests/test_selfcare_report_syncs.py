from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.models.subscriber import Subscriber, SubscriberStatus
from app.services import selfcare


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _SessionProxy:
    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        return None


def _settings_resolver(values: dict[str, object]):
    def _resolve(_db, _domain, key, use_cache=True):
        return values.get(key)

    return _resolve


def test_selfcare_client_uses_bearer_token_and_unwraps_envelope(db_session, monkeypatch):
    monkeypatch.setattr(
        selfcare.settings_spec,
        "resolve_value",
        _settings_resolver(
            {
                "selfcare_customer_sync_enabled": True,
                "selfcare_base_url": "https://selfcare.example.test",
                "selfcare_api_token": "token-123",
                "selfcare_timeout_seconds": 15,
            }
        ),
    )
    calls = []

    def _request(method, url, headers, params, json, timeout):
        calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params,
                "timeout": timeout,
            }
        )
        return _Response(200, {"data": [{"id": "sc-1"}], "meta": {"page": 1, "total": 1}})

    monkeypatch.setattr("requests.request", _request)

    rows = selfcare.fetch_customers(db_session)

    assert rows == [{"id": "sc-1"}]
    assert calls[0]["headers"]["Authorization"] == "Bearer token-123"
    assert calls[0]["url"] == "https://selfcare.example.test/api/v1/crm/subscribers"
    assert calls[0]["params"]["include"] == "services,billing"


def test_selfcare_client_raises_provider_error_on_non_2xx(db_session, monkeypatch):
    monkeypatch.setattr(
        selfcare.settings_spec,
        "resolve_value",
        _settings_resolver(
            {
                "selfcare_customer_sync_enabled": True,
                "selfcare_base_url": "https://selfcare.example.test",
                "selfcare_api_token": "token-123",
                "selfcare_timeout_seconds": 15,
            }
        ),
    )
    monkeypatch.setattr("requests.request", lambda *args, **kwargs: _Response(503, text="down"))

    with pytest.raises(selfcare.SelfcareProviderError):
        selfcare.ping(db_session)


def test_selfcare_mapper_handles_null_lifecycle_fields_and_preserves_splynx_metadata(db_session):
    mapped = selfcare.map_customer_to_subscriber_data(
        db_session,
        {
            "id": "sc-123",
            "subscriber_number": "SUB-123",
            "status": "suspended",
            "service_plan": "Fiber 50/20 Mbps",
            "balance": None,
            "blocked_date": None,
            "suspended_at": None,
            "terminated_at": None,
            "billing": {
                "subscription_billing_mode": "prepaid",
                "billing_mode": "postpaid",
                "account_billing_mode": "postpaid",
                "billing_type": "prepaid_monthly",
                "invoiced_until": None,
                "total_paid": None,
                "last_payment_date": None,
            },
        },
        include_remote_details=False,
        existing_sync_metadata={"splynx_id": "old-9", "splynx_login": "OLD"},
    )

    assert mapped["status"] == SubscriberStatus.suspended.value
    assert mapped["service_plan"] == "Fiber 50/20 Mbps"
    assert "suspended_at" not in mapped
    assert "terminated_at" not in mapped
    assert mapped["sync_metadata"]["splynx_id"] == "old-9"
    assert mapped["sync_metadata"]["selfcare_id"] == "sc-123"
    assert mapped["sync_metadata"]["source"] == "selfcare"
    assert mapped["sync_metadata"]["subscription_billing_mode"] == "prepaid"
    assert mapped["sync_metadata"]["billing_mode"] == "postpaid"
    assert mapped["sync_metadata"]["account_billing_mode"] == "postpaid"
    assert mapped["sync_metadata"]["billing_type"] == "prepaid_monthly"


def test_fetch_customers_paginates(db_session, monkeypatch):
    monkeypatch.setattr(
        selfcare.settings_spec,
        "resolve_value",
        _settings_resolver(
            {
                "selfcare_customer_sync_enabled": True,
                "selfcare_base_url": "https://selfcare.example.test",
                "selfcare_api_token": "token-123",
                "selfcare_timeout_seconds": 15,
            }
        ),
    )

    def _request(method, url, headers, params, json, timeout):
        page = int(params["page"])
        payload = {"data": [{"id": f"sc-{page}"}], "meta": {"page": page, "total": 2}}
        return _Response(200, payload)

    monkeypatch.setattr("requests.request", _request)

    assert selfcare.fetch_customers(db_session) == [{"id": "sc-1"}, {"id": "sc-2"}]


def test_sync_subscribers_from_selfcare_skips_bad_record(db_session, monkeypatch):
    from app.tasks import subscribers as subscriber_tasks

    monkeypatch.setattr(subscriber_tasks, "SessionLocal", lambda: _SessionProxy(db_session))
    monkeypatch.setattr("app.services.selfcare.ping", lambda session: True)
    monkeypatch.setattr(
        "app.services.selfcare.fetch_customers",
        lambda session, include="services,billing": [
            {"id": "sc-good", "subscriber_number": f"SUB-{uuid.uuid4().hex[:8]}", "status": "active"},
            {"subscriber_number": "missing-id", "status": "active"},
        ],
    )

    result = subscriber_tasks.sync_subscribers_from_selfcare.run()

    assert result["created"] == 1
    assert len(result["errors"]) == 1


def test_online_last_24h_returns_empty_on_selfcare_failure(db_session, monkeypatch):
    from app.services import subscriber_reports

    monkeypatch.setattr(
        "app.services.selfcare.fetch_customers",
        lambda db: (_ for _ in ()).throw(selfcare.SelfcareProviderError("unavailable")),
    )

    assert subscriber_reports.online_customers_last_24h_rows(db_session) == []


def test_retention_writeback_skips_active_status(db_session, monkeypatch):
    subscriber = Subscriber(
        external_system="selfcare",
        external_id="sc-active",
        subscriber_number=f"SUB-{uuid.uuid4().hex[:8]}",
        status=SubscriberStatus.active,
    )
    db_session.add(subscriber)
    db_session.commit()

    monkeypatch.setattr(selfcare, "fetch_customer", lambda db, subscriber_id: {"id": subscriber_id, "status": "active"})
    patched = []
    monkeypatch.setattr(selfcare, "patch_subscriber_status", lambda *args, **kwargs: patched.append(args) or {})

    result = selfcare.deactivate_customer_if_blocked(
        db_session,
        customer_id="sc-active",
        engagement_id=str(uuid.uuid4()),
    )

    assert result["skipped"] is True
    assert result["reason"] == "selfcare_status_not_suspended"
    assert patched == []


def test_retention_writeback_deactivates_suspended_status(db_session, monkeypatch):
    subscriber = Subscriber(
        external_system="selfcare",
        external_id="sc-suspended",
        subscriber_number=f"SUB-{uuid.uuid4().hex[:8]}",
        status=SubscriberStatus.suspended,
        sync_metadata={"splynx_id": "legacy"},
    )
    db_session.add(subscriber)
    db_session.commit()

    monkeypatch.setattr(
        selfcare,
        "fetch_customer",
        lambda db, subscriber_id: {"id": subscriber_id, "status": "suspended"},
    )
    patched = []
    monkeypatch.setattr(selfcare, "patch_subscriber_status", lambda *args, **kwargs: patched.append(args) or {})

    result = selfcare.deactivate_customer_if_blocked(
        db_session,
        customer_id="sc-suspended",
        engagement_id="eng-1",
        subscriber_id=str(subscriber.id),
    )

    db_session.refresh(subscriber)
    assert result["success"] is True
    assert patched
    assert subscriber.status == SubscriberStatus.terminated
    assert subscriber.terminated_at.replace(tzinfo=UTC) <= datetime.now(UTC)
    assert subscriber.sync_metadata["splynx_id"] == "legacy"
    assert subscriber.sync_metadata["retention_selfcare_deactivation"]["status"] == "success"

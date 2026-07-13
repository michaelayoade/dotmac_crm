from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.models.person import Gender, Person
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
    assert calls[0]["params"]["per_page"] == 500


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


def test_fetch_customers_can_omit_include_for_basic_subscriber_rows(db_session, monkeypatch):
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
        calls.append(params)
        return _Response(200, {"data": [{"id": "sc-basic", "billing_mode": "prepaid"}], "meta": {"total": 1}})

    monkeypatch.setattr("requests.request", _request)

    assert selfcare.fetch_customers(db_session, include=None) == [{"id": "sc-basic", "billing_mode": "prepaid"}]
    assert calls[0] == {"per_page": 500, "page": 1}


def test_fetch_locations_uses_paginated_locations_request(db_session, monkeypatch):
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
        calls.append({"method": method, "url": url, "params": params})
        return _Response(
            200,
            {
                "data": [
                    {"id": "09 Dawaki Model City Abuja, NG", "name": "Gwarimpa"},
                    {"id": "spdc-key", "name": "SPDC"},
                ]
            },
        )

    monkeypatch.setattr("requests.request", _request)

    assert selfcare.fetch_locations(db_session) == [
        {"id": "09 Dawaki Model City Abuja, NG", "name": "Gwarimpa"},
        {"id": "spdc-key", "name": "SPDC"},
    ]
    assert len(calls) == 1
    assert calls[0]["url"] == "https://selfcare.example.test/api/v1/crm/locations"
    assert calls[0]["params"] == {"per_page": 500, "page": 1}


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


def test_sync_subscribers_from_selfcare_updates_linked_person_profile(db_session, monkeypatch):
    person = Person(
        first_name="Jane",
        last_name="Doe",
        email="jane@example.com",
        gender=Gender.unknown,
        metadata_={"selfcare_id": "sc-100"},
    )
    db_session.add(person)
    db_session.commit()

    monkeypatch.setattr("app.services.selfcare.ping", lambda session: True)
    monkeypatch.setattr(
        "app.services.selfcare.fetch_customers",
        lambda session, include="services,billing": [
            {
                "id": "sc-100",
                "subscriber_number": "SUB-100",
                "status": "active",
                "email": "jane@example.com",
                "date_of_birth": "1990-01-05",
                "gender": "female",
                "nin": "12345678901",
            }
        ],
    )

    result = selfcare.sync_subscribers_from_selfcare_data(db_session)

    db_session.refresh(person)
    assert result["person_profile_updated"] == 1
    assert person.date_of_birth.isoformat() == "1990-01-05"
    assert person.gender == Gender.female
    assert person.nin == "12345678901"


def test_backfill_person_profiles_reports_conflict_without_force(db_session, monkeypatch):
    person = Person(
        first_name="John",
        last_name="Smith",
        email="john@example.com",
        date_of_birth=datetime(1991, 2, 2, tzinfo=UTC).date(),
        gender=Gender.male,
        nin="11111111111",
        metadata_={"selfcare_id": "sc-200"},
    )
    db_session.add(person)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.selfcare.fetch_customers",
        lambda session, include=None: [
            {
                "id": "sc-200",
                "email": "john@example.com",
                "date_of_birth": "1992-03-03",
                "gender": "female",
                "nin": "22222222222",
            }
        ],
    )

    result = selfcare.backfill_person_profiles_from_selfcare(db_session, force_from_selfcare=False)

    db_session.refresh(person)
    assert result["matched_and_updated"] == 1
    assert person.date_of_birth.isoformat() == "1992-03-03"
    assert person.gender == Gender.female
    assert person.nin == "22222222222"


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


def _enable_selfcare(monkeypatch):
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


def test_create_installation_invoice_posts_to_crm_invoices(db_session, monkeypatch):
    _enable_selfcare(monkeypatch)
    calls = []

    def _request(method, url, headers, params, json, timeout):
        calls.append({"method": method, "url": url, "json": json})
        return _Response(201, {"data": {"id": "inv-9", "total": "25000.00", "status": "issued"}})

    monkeypatch.setattr("requests.request", _request)

    invoice_id = selfcare.create_installation_invoice(
        db_session,
        subscriber_id="sub-uuid-1",
        amount="25000.00",
        description="Installation cost",
        external_ref="project:abc",
    )

    assert invoice_id == "inv-9"
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://selfcare.example.test/api/v1/crm/invoices"
    assert calls[0]["json"] == {
        "subscriber_id": "sub-uuid-1",
        "amount": "25000.00",
        "description": "Installation cost",
        "external_ref": "project:abc",
        "currency": "NGN",
    }


def test_create_installation_invoice_skips_non_positive_amount(db_session, monkeypatch):
    _enable_selfcare(monkeypatch)
    called = []
    monkeypatch.setattr("requests.request", lambda *a, **k: called.append(1) or _Response(201, {"data": {}}))

    assert selfcare.create_installation_invoice(db_session, subscriber_id="s1", amount="0") is None
    assert selfcare.create_installation_invoice(db_session, subscriber_id="s1", amount="not-a-number") is None
    assert called == []  # never hit the network for invalid amounts


def test_create_installation_invoice_raises_on_provider_error(db_session, monkeypatch):
    # A provider failure now propagates so the caller can record a failure marker
    # and retry — rather than silently producing no invoice and no signal.
    _enable_selfcare(monkeypatch)
    monkeypatch.setattr(selfcare, "_sleep_backoff", lambda attempt: None)
    monkeypatch.setattr("requests.request", lambda *a, **k: _Response(502, {}, text="bad gateway"))

    with pytest.raises(selfcare.SelfcareProviderError):
        selfcare.create_installation_invoice(db_session, subscriber_id="s1", amount="100")

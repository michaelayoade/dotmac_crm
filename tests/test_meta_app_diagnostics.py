import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from app.services import meta_app_diagnostics


def _run_async(coro):
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def test_fingerprint_secret_returns_safe_prefix():
    value = meta_app_diagnostics.fingerprint_secret("super-secret-value")
    assert value is not None
    assert len(value) == 12
    assert value != "super-secret-value"


def test_collect_runtime_secret_report_logs_fingerprints_without_secret_leakage(monkeypatch, caplog):
    monkeypatch.setattr(
        meta_app_diagnostics.meta_oauth,
        "get_meta_settings",
        lambda _db: {
            "meta_app_id": "1601825273763276",
            "whatsapp_app_id": "1057271179481523",
            "meta_app_secret": "meta-secret-raw",
            "whatsapp_app_secret": "wa-secret-raw",
        },
    )
    monkeypatch.setattr(
        meta_app_diagnostics,
        "_runtime_secret_source",
        lambda _db, key, env_var: "domain_settings",
    )

    with caplog.at_level(logging.INFO):
        report = meta_app_diagnostics.collect_runtime_secret_report(db=None)

    assert report.meta_app_secret_fingerprint == meta_app_diagnostics.fingerprint_secret("meta-secret-raw")
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "instagram_signature_runtime_source" in messages
    assert "meta-secret-raw" not in messages
    assert "wa-secret-raw" not in messages


def test_classify_consistency_returns_app_mismatch():
    instagram_state = meta_app_diagnostics.SubscriptionState(
        account_type="instagram_business",
        account_id="17841403813819361",
        status="ok",
        subscribed_apps=[meta_app_diagnostics.SubscriptionApp(app_id="999", name="Other", subscribed_fields=[])],
    )
    page_state = meta_app_diagnostics.SubscriptionState(
        account_type="page",
        account_id="75592117926",
        status="ok",
        subscribed_apps=[
            meta_app_diagnostics.SubscriptionApp(app_id="1601825273763276", name="Configured", subscribed_fields=[])
        ],
    )

    classification = meta_app_diagnostics._classify_consistency("1601825273763276", instagram_state, page_state)

    assert classification == "app_mismatch"


def test_classify_consistency_returns_missing_subscription():
    instagram_state = meta_app_diagnostics.SubscriptionState(
        account_type="instagram_business",
        account_id="17841403813819361",
        status="missing_subscription",
        subscribed_apps=[],
    )

    classification = meta_app_diagnostics._classify_consistency("1601825273763276", instagram_state, None)

    assert classification == "missing_subscription"


def test_check_meta_app_consistency_logs_without_secret_leakage(monkeypatch, caplog):
    runtime = meta_app_diagnostics.RuntimeSecretReport(
        meta_app_id="1601825273763276",
        whatsapp_app_id="1057271179481523",
        meta_app_secret_fingerprint="abc123def456",
        whatsapp_app_secret_fingerprint="fff111eee222",
        meta_app_secret_source="domain_settings",
        whatsapp_app_secret_source="domain_settings",
    )
    instagram_state = meta_app_diagnostics.SubscriptionState(
        account_type="instagram_business",
        account_id="17841403813819361",
        status="ok",
        subscribed_apps=[
            meta_app_diagnostics.SubscriptionApp(
                app_id="1601825273763276",
                name="Configured",
                subscribed_fields=["messages"],
            )
        ],
    )
    page_state = meta_app_diagnostics.SubscriptionState(
        account_type="page",
        account_id="75592117926",
        status="ok",
        subscribed_apps=[
            meta_app_diagnostics.SubscriptionApp(
                app_id="1601825273763276",
                name="Configured",
                subscribed_fields=["messages", "messaging_postbacks"],
            )
        ],
    )

    monkeypatch.setattr(meta_app_diagnostics, "collect_runtime_secret_report", lambda _db: runtime)
    monkeypatch.setattr(
        meta_app_diagnostics.meta_oauth,
        "get_token_for_instagram",
        lambda _db, _instagram_id: SimpleNamespace(access_token="ig-token"),
    )
    monkeypatch.setattr(
        meta_app_diagnostics.meta_oauth,
        "get_token_for_page",
        lambda _db, _page_id: SimpleNamespace(access_token="page-token"),
    )

    async def _fake_fetch_subscription_state(*, db, account_type, account_id, token):
        if account_type == "instagram_business":
            return instagram_state
        return page_state

    monkeypatch.setattr(meta_app_diagnostics, "_fetch_subscription_state", _fake_fetch_subscription_state)

    with caplog.at_level(logging.INFO):
        report = _run_async(
            meta_app_diagnostics.check_meta_app_consistency(
                db=None,
                instagram_account_id="17841403813819361",
                page_id="75592117926",
            )
        )

    assert report.classification == "matching_app"
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "meta_app_consistency_check" in messages
    assert "meta_webhook_subscription_state" in messages
    assert "ig-token" not in messages
    assert "page-token" not in messages


def test_audit_meta_tokens_classifies_app_mismatch_and_duplicate_override(monkeypatch):
    runtime = meta_app_diagnostics.RuntimeSecretReport(
        meta_app_id="1601825273763276",
        whatsapp_app_id="1057271179481523",
        meta_app_secret_fingerprint="abc123def456",
        whatsapp_app_secret_fingerprint="fff111eee222",
        meta_app_secret_source="domain_settings",
        whatsapp_app_secret_source="domain_settings",
    )
    fake_token_value = "same-token"
    monkeypatch.setattr(meta_app_diagnostics, "collect_runtime_secret_report", lambda _db: runtime)
    monkeypatch.setattr(
        meta_app_diagnostics,
        "_inventory_override_tokens",
        lambda _db: [
            {
                "token_label": "settings:meta_facebook_access_token_override",
                "token_source": "domain_settings",
                "token_type_guess": "facebook_override",
                "token_value": fake_token_value,
                "roles": ["override"],
                "account_type": "page",
                "associated_page_id": None,
                "associated_instagram_account_id": None,
                "associated_account_name": None,
                "connector_id": None,
                "connector_name": None,
            }
        ],
    )
    monkeypatch.setattr(meta_app_diagnostics, "_inventory_connector_auth_tokens", lambda _db: [])
    monkeypatch.setattr(
        meta_app_diagnostics,
        "_inventory_oauth_tokens",
        lambda _db: [
            {
                "token_label": "oauth:page:75592117926",
                "token_source": "oauth_tokens",
                "token_type_guess": "page",
                "token_value": fake_token_value,
                "connector_id": "connector-1",
                "connector_name": "Meta",
                "account_type": "page",
                "associated_page_id": "75592117926",
                "associated_instagram_account_id": None,
                "associated_account_name": "Dotmac Fiber",
                "expires_at_dt": None,
                "is_expired_db": False,
                "scopes_db": ["pages_messaging", "pages_show_list"],
                "roles": ["primary"],
                "metadata": {},
            }
        ],
    )

    async def _fake_debug_meta_token(db, input_token, app_id, app_secret):
        return {
            "data": {
                "is_valid": True,
                "app_id": "9999999999999999",
                "scopes": ["pages_messaging", "pages_show_list"],
            }
        }

    monkeypatch.setattr(meta_app_diagnostics, "_debug_meta_token", _fake_debug_meta_token)

    async def _probe(*args, **kwargs):
        return True, None

    monkeypatch.setattr(meta_app_diagnostics, "_probe_endpoint", _probe)
    monkeypatch.setattr(
        meta_app_diagnostics.meta_oauth,
        "get_meta_settings",
        lambda _db: {"meta_app_secret": "meta-secret"},
    )

    inventory = _run_async(
        meta_app_diagnostics.audit_meta_tokens(
            db=None,
            instagram_account_id="17841403813819361",
            page_id="75592117926",
        )
    )

    assert len(inventory) == 2
    assert all(entry.status == "app_mismatch" for entry in inventory)
    duplicate_entries = [entry for entry in inventory if entry.duplicate_of]
    assert len(duplicate_entries) == 1
    assert "duplicate" in duplicate_entries[0].roles


def test_audit_meta_tokens_flags_missing_permissions(monkeypatch):
    runtime = meta_app_diagnostics.RuntimeSecretReport(
        meta_app_id="1601825273763276",
        whatsapp_app_id=None,
        meta_app_secret_fingerprint="abc123def456",
        whatsapp_app_secret_fingerprint=None,
        meta_app_secret_source="domain_settings",
        whatsapp_app_secret_source="missing",
    )
    monkeypatch.setattr(meta_app_diagnostics, "collect_runtime_secret_report", lambda _db: runtime)
    monkeypatch.setattr(meta_app_diagnostics, "_inventory_override_tokens", lambda _db: [])
    monkeypatch.setattr(meta_app_diagnostics, "_inventory_connector_auth_tokens", lambda _db: [])
    monkeypatch.setattr(
        meta_app_diagnostics,
        "_inventory_oauth_tokens",
        lambda _db: [
            {
                "token_label": "oauth:instagram_business:17841403813819361",
                "token_source": "oauth_tokens",
                "token_type_guess": "instagram_business",
                "token_value": "ig-token",
                "connector_id": "connector-1",
                "connector_name": "Meta",
                "account_type": "instagram_business",
                "associated_page_id": None,
                "associated_instagram_account_id": "17841403813819361",
                "associated_account_name": "dotmac_ng",
                "expires_at_dt": None,
                "is_expired_db": False,
                "scopes_db": ["instagram_basic"],
                "roles": ["primary"],
                "metadata": {},
            }
        ],
    )

    async def _fake_debug_meta_token(db, input_token, app_id, app_secret):
        return {
            "data": {
                "is_valid": True,
                "app_id": "1601825273763276",
                "scopes": ["instagram_basic"],
            }
        }

    monkeypatch.setattr(meta_app_diagnostics, "_debug_meta_token", _fake_debug_meta_token)

    async def _probe(*args, **kwargs):
        return False, "insufficient_permissions"

    monkeypatch.setattr(meta_app_diagnostics, "_probe_endpoint", _probe)
    monkeypatch.setattr(
        meta_app_diagnostics.meta_oauth,
        "get_meta_settings",
        lambda _db: {"meta_app_secret": "meta-secret"},
    )

    inventory = _run_async(
        meta_app_diagnostics.audit_meta_tokens(
            db=None,
            instagram_account_id="17841403813819361",
            page_id="75592117926",
        )
    )

    assert inventory[0].status == "insufficient_permissions"
    assert "instagram_messaging" in inventory[0].missing_permissions

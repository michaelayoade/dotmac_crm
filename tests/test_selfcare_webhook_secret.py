"""D4: the selfcare customer-webhook secret resolves through secret references.

The webhook secret must go through resolve_secret like selfcare_api_key does,
so an OpenBao/env reference stored in settings is resolved to the real value
while plain values pass through unchanged.
"""

from app.services import selfcare


def _settings(webhook_secret):
    return {
        "selfcare_customer_sync_enabled": True,
        "selfcare_base_url": "https://sub.example.com",
        "selfcare_customer_webhook_secret": webhook_secret,
    }


def _patch_settings(monkeypatch, values):
    monkeypatch.setattr(
        "app.services.settings_spec.resolve_value",
        lambda db, domain, key, **kw: values.get(key),
    )


def test_plain_webhook_secret_passes_through(db_session, monkeypatch):
    _patch_settings(monkeypatch, _settings("plain-shared-secret"))

    config = selfcare._get_config(db_session)

    assert config is not None
    assert config["webhook_secret"] == "plain-shared-secret"


def test_env_reference_webhook_secret_is_resolved(db_session, monkeypatch):
    monkeypatch.setenv("TEST_SELFCARE_WEBHOOK_SECRET", "resolved-from-env")
    _patch_settings(monkeypatch, _settings("env://TEST_SELFCARE_WEBHOOK_SECRET"))

    config = selfcare._get_config(db_session)

    assert config is not None
    assert config["webhook_secret"] == "resolved-from-env"


def test_bao_reference_webhook_secret_is_resolved(db_session, monkeypatch):
    _patch_settings(monkeypatch, _settings("bao://integration/selfcare#webhook_secret"))
    monkeypatch.setattr(
        "app.services.secrets.resolve_openbao_ref",
        lambda ref: "resolved-from-openbao",
    )

    config = selfcare._get_config(db_session)

    assert config is not None
    assert config["webhook_secret"] == "resolved-from-openbao"


def test_unresolvable_reference_disables_webhook_config(db_session, monkeypatch):
    monkeypatch.delenv("MISSING_SELFCARE_WEBHOOK_SECRET", raising=False)
    _patch_settings(monkeypatch, _settings("env://MISSING_SELFCARE_WEBHOOK_SECRET"))

    config = selfcare._get_config(db_session)

    assert config is None

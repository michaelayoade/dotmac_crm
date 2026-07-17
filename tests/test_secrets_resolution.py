"""Secret-reference resolution (ported from dotmac_sub, auth S5)."""

from __future__ import annotations

from app.services.secrets import is_openbao_ref, resolve_secret


def test_plaintext_passthrough():
    assert resolve_secret("plain-token") == "plain-token"
    assert resolve_secret(None) is None
    assert resolve_secret("") == ""


def test_env_reference(monkeypatch):
    monkeypatch.setenv("CRM_S5_TEST_SECRET", "resolved-value")
    assert resolve_secret("env://CRM_S5_TEST_SECRET") == "resolved-value"


def test_openbao_ref_detection():
    assert is_openbao_ref("bao://secret/data/integrations#token")
    assert is_openbao_ref("vault://secret/x")
    assert not is_openbao_ref("plain")

from app.metrics import AI_PROVIDER_CIRCUIT_OPEN
from app.models.crm.conversation import Conversation, Message
from app.models.domain_settings import SettingValueType
from app.services.ai.client import AIClientError, AIResponse
from app.services.ai.gateway import ai_gateway
from app.services.ai.provider_health import run_provider_healthcheck
from app.services.domain_settings import integration_settings


def _seed_provider_settings(db_session) -> None:
    ai_gateway._circuit_states.clear()
    integration_settings.ensure_by_key(
        db_session,
        key="vllm_label",
        value_type=SettingValueType.string,
        value_text="primary",
    )
    integration_settings.ensure_by_key(
        db_session,
        key="vllm_base_url",
        value_type=SettingValueType.string,
        value_text="https://primary.example.test",
    )
    integration_settings.ensure_by_key(
        db_session,
        key="vllm_model",
        value_type=SettingValueType.string,
        value_text="primary-model",
    )
    integration_settings.ensure_by_key(
        db_session,
        key="vllm_api_key",
        value_type=SettingValueType.string,
        value_text="primary-key",
        is_secret=True,
    )
    integration_settings.ensure_by_key(
        db_session,
        key="vllm_require_api_key",
        value_type=SettingValueType.boolean,
        value_text="true",
        value_json=True,
    )
    integration_settings.ensure_by_key(
        db_session,
        key="vllm_secondary_label",
        value_type=SettingValueType.string,
        value_text="secondary",
    )
    integration_settings.ensure_by_key(
        db_session,
        key="vllm_secondary_base_url",
        value_type=SettingValueType.string,
        value_text="https://secondary.example.test",
    )
    integration_settings.ensure_by_key(
        db_session,
        key="vllm_secondary_model",
        value_type=SettingValueType.string,
        value_text="secondary-model",
    )
    integration_settings.ensure_by_key(
        db_session,
        key="vllm_secondary_api_key",
        value_type=SettingValueType.string,
        value_text="secondary-key",
        is_secret=True,
    )
    integration_settings.ensure_by_key(
        db_session,
        key="vllm_secondary_require_api_key",
        value_type=SettingValueType.boolean,
        value_text="true",
        value_json=True,
    )


def test_healthcheck_timeout_on_primary_triggers_secondary(db_session, monkeypatch):
    _seed_provider_settings(db_session)
    monkeypatch.setattr("app.services.ai.provider_health._dns_check", lambda base_url: (True, ["127.0.0.1"], None))
    monkeypatch.setattr("app.services.ai.provider_health._tls_check", lambda base_url, timeout: (True, None))

    def _generate(self, system: str, prompt: str, max_tokens: int = 2048):
        if self.provider == "primary":
            raise AIClientError(
                "timeout",
                provider=self.provider,
                model=self.model,
                endpoint="primary",
                failure_type="timeout",
                transient=True,
                timeout_type="read",
                retry_count=1,
            )
        return AIResponse(content='{"ok":true}', tokens_in=1, tokens_out=1, model=self.model, provider=self.provider)

    monkeypatch.setattr("app.services.ai.client.VllmClient.generate", _generate)

    report = run_provider_healthcheck(db_session, mode="fallback")

    assert report.overall_success is True
    assert report.fallback_used is True
    assert len(report.results) == 2
    assert report.results[0].failure_type == "timeout"
    assert report.results[1].success is True
    assert report.results[1].used_fallback is True


def test_healthcheck_respects_open_circuit_without_provider_request(db_session, monkeypatch):
    _seed_provider_settings(db_session)
    monkeypatch.setattr("app.services.ai.provider_health._dns_check", lambda base_url: (True, ["127.0.0.1"], None))
    monkeypatch.setattr("app.services.ai.provider_health._tls_check", lambda base_url, timeout: (True, None))

    cfg = ai_gateway.get_endpoint_config(db_session, "primary")
    error = AIClientError(
        "timeout",
        provider=cfg.label,
        model=cfg.model,
        endpoint="primary",
        failure_type="timeout",
        transient=True,
    )
    for _ in range(3):
        ai_gateway._record_failure(cfg, "primary", error)

    called = {"count": 0}

    def _generate(self, system: str, prompt: str, max_tokens: int = 2048):
        called["count"] += 1
        raise AssertionError("healthcheck should not call provider while circuit is open")

    monkeypatch.setattr("app.services.ai.client.VllmClient.generate", _generate)

    report = run_provider_healthcheck(db_session, mode="primary")

    assert report.overall_success is False
    assert report.results[0].failure_type == "circuit_open"
    assert called["count"] == 0
    assert AI_PROVIDER_CIRCUIT_OPEN.labels(provider=cfg.label, model=cfg.model, endpoint="primary")._value.get() == 1


def test_healthcheck_success_clears_open_circuit_when_bypassing_circuit(db_session, monkeypatch):
    _seed_provider_settings(db_session)
    monkeypatch.setattr("app.services.ai.provider_health._dns_check", lambda base_url: (True, ["127.0.0.1"], None))
    monkeypatch.setattr("app.services.ai.provider_health._tls_check", lambda base_url, timeout: (True, None))
    monkeypatch.setattr(
        "app.services.ai.client.VllmClient.generate",
        lambda self, system, prompt, max_tokens=2048: AIResponse(
            content='{"ok":true}',
            tokens_in=1,
            tokens_out=1,
            model=self.model,
            provider=self.provider,
        ),
    )

    cfg = ai_gateway.get_endpoint_config(db_session, "primary")
    error = AIClientError(
        "timeout",
        provider=cfg.label,
        model=cfg.model,
        endpoint="primary",
        failure_type="timeout",
        transient=True,
    )
    for _ in range(3):
        ai_gateway._record_failure(cfg, "primary", error)

    report = run_provider_healthcheck(db_session, mode="primary", respect_circuit=False)
    state = ai_gateway.circuit_state(db_session, "primary")

    assert report.overall_success is True
    assert state["is_open"] is False
    assert state["consecutive_failures"] == 0


def test_healthcheck_never_mutates_crm_state(db_session, monkeypatch):
    _seed_provider_settings(db_session)
    monkeypatch.setattr("app.services.ai.provider_health._dns_check", lambda base_url: (True, ["127.0.0.1"], None))
    monkeypatch.setattr("app.services.ai.provider_health._tls_check", lambda base_url, timeout: (True, None))
    monkeypatch.setattr(
        "app.services.ai.client.VllmClient.generate",
        lambda self, system, prompt, max_tokens=2048: AIResponse(
            content='{"ok":true}',
            tokens_in=1,
            tokens_out=1,
            model=self.model,
            provider=self.provider,
        ),
    )

    before_conversations = db_session.query(Conversation).count()
    before_messages = db_session.query(Message).count()

    report = run_provider_healthcheck(db_session, mode="primary")

    after_conversations = db_session.query(Conversation).count()
    after_messages = db_session.query(Message).count()

    assert report.overall_success is True
    assert before_conversations == after_conversations
    assert before_messages == after_messages


def test_healthcheck_simulated_auth_failure_does_not_fallback(db_session, monkeypatch):
    _seed_provider_settings(db_session)
    monkeypatch.setattr("app.services.ai.provider_health._dns_check", lambda base_url: (True, ["127.0.0.1"], None))
    monkeypatch.setattr("app.services.ai.provider_health._tls_check", lambda base_url, timeout: (True, None))

    report = run_provider_healthcheck(db_session, mode="fallback", simulate_primary_failure="auth")

    assert report.overall_success is False
    assert report.fallback_used is False
    assert len(report.results) == 1
    assert report.results[0].failure_type == "auth"

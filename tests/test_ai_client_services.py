from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.ai.client import AIClientError, VllmClient, build_ai_client
from app.services.ai.gateway import AIEndpointConfig, AIGateway


def test_build_ai_client_defaults_to_vllm():
    with patch(
        "app.services.ai.client._resolve_integration_ai_settings",
        return_value={"vllm_base_url": "http://localhost:8001/v1", "vllm_model": "qwen"},
    ):
        client = build_ai_client(db=MagicMock())

    assert isinstance(client, VllmClient)
    assert client.provider == "vllm"


def test_build_ai_client_missing_required_settings_raises():
    with (
        patch(
            "app.services.ai.client._resolve_integration_ai_settings",
            return_value={"llm_provider": "vllm"},
        ),
        pytest.raises(AIClientError),
    ):
        build_ai_client(db=MagicMock())


def test_vllm_generate_normalizes_response():
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "model": "qwen-test",
        "choices": [{"message": {"content": "vllm output"}}],
        "usage": {"prompt_tokens": 77, "completion_tokens": 33},
    }

    client_cm = MagicMock()
    client_cm.__enter__.return_value.request.return_value = response

    with patch("app.services.ai.client.httpx.Client", return_value=client_cm):
        client = VllmClient(api_key=None, model="m", base_url="http://localhost:8001/v1")
        result = client.generate(system="sys", prompt="user")

    assert result.provider == "vllm"
    assert result.content == "vllm output"
    assert result.tokens_in == 77
    assert result.tokens_out == 33


def test_vllm_generate_classifies_auth_failure():
    response = MagicMock()
    response.status_code = 401
    response.text = "Authentication Fails (governor)"
    response.headers = {}
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401 unauthorized",
        request=MagicMock(),
        response=response,
    )

    request = MagicMock(return_value=response)
    client_cm = MagicMock()
    client_cm.__enter__.return_value.request = request

    with (
        patch("app.services.ai.client.httpx.Client", return_value=client_cm),
        pytest.raises(AIClientError) as exc_info,
    ):
        client = VllmClient(api_key="secret", model="deepseek-chat", base_url="https://api.deepseek.com")
        client.generate(system="sys", prompt="user")

    assert exc_info.value.failure_type == "auth"
    assert exc_info.value.status_code == 401
    assert exc_info.value.transient is False
    assert exc_info.value.response_preview == "Authentication Fails (governor)"
    assert request.call_count == 1


def test_vllm_generate_retries_transient_timeout_then_succeeds():
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "model": "deepseek-chat",
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }

    request = MagicMock(side_effect=[httpx.ReadTimeout("read timed out"), response])
    client_cm = MagicMock()
    client_cm.__enter__.return_value.request = request

    with (
        patch("app.services.ai.client.httpx.Client", return_value=client_cm),
        patch("app.services.ai.client.sleep") as mock_sleep,
        patch("app.services.ai.client.random.uniform", return_value=0.0),
    ):
        client = VllmClient(
            api_key="secret",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
            max_retries=1,
        )
        result = client.generate(system="sys", prompt="user")

    assert result.content == "ok"
    assert request.call_count == 2
    mock_sleep.assert_called_once()


def test_gateway_circuit_breaker_opens_after_repeated_transient_failures():
    gateway = AIGateway()
    cfg = AIEndpointConfig(
        label="primary",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        api_key="secret",
        require_api_key=True,
        timeout_seconds=20.0,
        max_retries=1,
        max_tokens=512,
    )
    error = AIClientError(
        "timeout",
        provider="primary",
        model="deepseek-chat",
        endpoint="primary",
        failure_type="timeout",
        transient=True,
    )

    for _ in range(3):
        gateway._record_failure(cfg, "primary", error)

    with pytest.raises(AIClientError) as exc_info:
        gateway._before_request(cfg, "primary")

    assert exc_info.value.failure_type == "circuit_open"

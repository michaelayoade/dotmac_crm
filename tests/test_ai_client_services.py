from unittest.mock import MagicMock, patch

import pytest

from app.services.ai.client import AIClientError, VllmClient, build_ai_client


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

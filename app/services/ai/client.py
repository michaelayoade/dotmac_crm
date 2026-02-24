from __future__ import annotations

import logging
from dataclasses import dataclass
from time import sleep
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.services import settings_spec

logger = logging.getLogger(__name__)


class AIClientError(RuntimeError):
    """Raised when AI generation fails."""


@dataclass(frozen=True)
class AIResponse:
    content: str
    tokens_in: int | None
    tokens_out: int | None
    model: str
    provider: str


def _coerce_int(value: object | None, default: int, minimum: int = 0) -> int:
    if value is None:
        parsed = default
    elif isinstance(value, bool):
        parsed = int(value)
    elif isinstance(value, int | float):
        parsed = int(value)
    elif isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            parsed = default
    else:
        parsed = default
    return max(parsed, minimum)


def _coerce_float(value: object | None, default: float, minimum: float = 0.0) -> float:
    if value is None:
        parsed = default
    elif isinstance(value, bool):
        parsed = float(value)
    elif isinstance(value, int | float):
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            parsed = default
    else:
        parsed = default
    return max(parsed, minimum)


class _BaseHttpAIClient:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        temperature: float = 0.4,
    ) -> None:
        self.provider = provider
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.temperature = temperature

    def _request_json(
        self, *, method: str, url: str, headers: dict[str, str], payload: dict[str, Any]
    ) -> dict[str, Any]:
        attempts = max(self.max_retries, 0) + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.request(method=method, url=url, headers=headers, json=payload)
                if (response.status_code in {408, 409, 425, 429} or response.status_code >= 500) and attempt < attempts:
                    sleep(min(2**attempt, 5))
                    continue
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict):
                    return data
                raise AIClientError("Invalid AI response payload")
            except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
                last_error = exc
                if attempt < attempts:
                    sleep(min(2**attempt, 5))
                    continue
                break
        raise AIClientError(f"AI request failed for provider={self.provider}") from last_error


class VllmClient(_BaseHttpAIClient):
    """OpenAI-compatible client intended for vLLM (or any /v1/chat/completions endpoint).

    Note: despite the name, this works for hosted OpenAI-compatible providers (e.g. DeepSeek)
    and self-hosted gateways. The provider label is used for audit/debug only.
    """

    def __init__(
        self,
        *,
        provider: str = "vllm",
        api_key: str | None,
        model: str,
        base_url: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        temperature: float = 0.4,
    ) -> None:
        super().__init__(
            provider=provider,
            model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            temperature=temperature,
        )
        self.api_key = (api_key or "").strip() or None
        self.base_url = base_url.rstrip("/")

    def _endpoint(self) -> str:
        # Allow base_url to be either ".../v1" or the root URL.
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def generate(self, system: str, prompt: str, max_tokens: int = 2048) -> AIResponse:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"

        data = self._request_json(
            method="POST",
            url=self._endpoint(),
            headers=headers,
            payload=payload,
        )

        usage = data.get("usage") if isinstance(data, dict) else {}
        if not isinstance(usage, dict):
            usage = {}

        content = ""
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    raw_content = message.get("content")
                    content = raw_content if isinstance(raw_content, str) else ""
                else:
                    raw_text = first.get("text")
                    content = raw_text if isinstance(raw_text, str) else ""

        return AIResponse(
            content=content.strip(),
            tokens_in=usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else None,
            tokens_out=usage.get("completion_tokens") if isinstance(usage.get("completion_tokens"), int) else None,
            model=str(data.get("model") or self.model),
            provider=self.provider,
        )


def _resolve_integration_ai_settings(db: Session) -> dict[str, Any]:
    keys = [
        "llm_provider",
        "vllm_api_key",
        "vllm_model",
        "vllm_base_url",
        "vllm_timeout_seconds",
        "vllm_max_retries",
        "vllm_require_api_key",
    ]
    return settings_spec.resolve_values_atomic(db, SettingDomain.integration, keys)


def build_ai_client(db: Session) -> VllmClient:
    values = _resolve_integration_ai_settings(db)
    provider = str(values.get("llm_provider") or "vllm").strip().lower()
    if provider != "vllm":
        logger.warning("Unsupported llm_provider=%s, falling back to vllm", provider)

    base_url = str(values.get("vllm_base_url") or "").strip()
    model = str(values.get("vllm_model") or "").strip()
    if not base_url:
        raise AIClientError("Missing integration setting: vllm_base_url")
    if not model:
        raise AIClientError("Missing integration setting: vllm_model")

    return VllmClient(
        api_key=str(values.get("vllm_api_key") or "").strip() or None,
        model=model,
        base_url=base_url,
        timeout_seconds=_coerce_float(values.get("vllm_timeout_seconds"), default=30.0, minimum=1.0),
        max_retries=_coerce_int(values.get("vllm_max_retries"), default=2, minimum=0),
    )

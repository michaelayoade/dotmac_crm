from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.services.ai.client import AIClientError, AIResponse, VllmClient, _coerce_float, _coerce_int
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)

AIEndpoint = Literal["primary", "secondary"]


@dataclass(frozen=True)
class AIEndpointConfig:
    label: str
    base_url: str
    model: str
    api_key: str | None
    require_api_key: bool
    timeout_seconds: float
    max_retries: int
    max_tokens: int


def _get_bool(db: Session, domain: SettingDomain, key: str, default: bool = False) -> bool:
    value = resolve_value(db, domain, key)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _load_primary_config(db: Session) -> AIEndpointConfig:
    label = str(resolve_value(db, SettingDomain.integration, "vllm_label") or "primary").strip() or "primary"
    base_url = str(resolve_value(db, SettingDomain.integration, "vllm_base_url") or "").strip()
    model = str(resolve_value(db, SettingDomain.integration, "vllm_model") or "").strip()
    api_key = str(resolve_value(db, SettingDomain.integration, "vllm_api_key") or "").strip() or None
    require_api_key = _get_bool(db, SettingDomain.integration, "vllm_require_api_key", default=False)
    timeout_seconds = _coerce_float(
        resolve_value(db, SettingDomain.integration, "vllm_timeout_seconds"), default=30.0, minimum=1.0
    )
    max_retries = _coerce_int(resolve_value(db, SettingDomain.integration, "vllm_max_retries"), default=2, minimum=0)
    max_tokens = _coerce_int(resolve_value(db, SettingDomain.integration, "vllm_max_tokens"), default=2048, minimum=1)
    return AIEndpointConfig(
        label=label,
        base_url=base_url,
        model=model,
        api_key=api_key,
        require_api_key=require_api_key,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        max_tokens=max_tokens,
    )


def _load_secondary_config(db: Session) -> AIEndpointConfig:
    label = (
        str(resolve_value(db, SettingDomain.integration, "vllm_secondary_label") or "secondary").strip() or "secondary"
    )
    base_url = str(resolve_value(db, SettingDomain.integration, "vllm_secondary_base_url") or "").strip()
    model = str(resolve_value(db, SettingDomain.integration, "vllm_secondary_model") or "").strip()
    api_key = str(resolve_value(db, SettingDomain.integration, "vllm_secondary_api_key") or "").strip() or None
    require_api_key = _get_bool(db, SettingDomain.integration, "vllm_secondary_require_api_key", default=False)
    timeout_seconds = _coerce_float(
        resolve_value(db, SettingDomain.integration, "vllm_secondary_timeout_seconds"),
        default=30.0,
        minimum=1.0,
    )
    max_retries = _coerce_int(
        resolve_value(db, SettingDomain.integration, "vllm_secondary_max_retries"), default=1, minimum=0
    )
    max_tokens = _coerce_int(
        resolve_value(db, SettingDomain.integration, "vllm_secondary_max_tokens"), default=2048, minimum=1
    )
    return AIEndpointConfig(
        label=label,
        base_url=base_url,
        model=model,
        api_key=api_key,
        require_api_key=require_api_key,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        max_tokens=max_tokens,
    )


class AIGateway:
    """
    Central place for AI calls.

    - Keeps all provider settings + retries + max token policy in one place.
    - Supports two endpoints (primary + secondary) so you can combine DeepSeek + self-hosted Llama.
    """

    def enabled(self, db: Session) -> bool:
        return _get_bool(db, SettingDomain.integration, "ai_enabled", default=False)

    def endpoint_ready(self, db: Session, endpoint: AIEndpoint) -> bool:
        cfg = _load_primary_config(db) if endpoint == "primary" else _load_secondary_config(db)
        if not (cfg.base_url and cfg.model):
            return False
        return not (cfg.require_api_key and not cfg.api_key)

    def _client_for(self, cfg: AIEndpointConfig) -> VllmClient:
        return VllmClient(
            provider=cfg.label,
            api_key=cfg.api_key,
            model=cfg.model,
            base_url=cfg.base_url,
            timeout_seconds=cfg.timeout_seconds,
            max_retries=cfg.max_retries,
        )

    def generate(
        self,
        db: Session,
        *,
        endpoint: AIEndpoint,
        system: str,
        prompt: str,
        max_tokens: int | None = None,
    ) -> AIResponse:
        if not self.enabled(db):
            raise AIClientError("AI features are disabled (integration.ai_enabled=false)")

        cfg = _load_primary_config(db) if endpoint == "primary" else _load_secondary_config(db)
        if not (cfg.base_url and cfg.model):
            raise AIClientError(f"AI endpoint not configured: {endpoint}")
        if cfg.require_api_key and not cfg.api_key:
            raise AIClientError(f"AI endpoint requires an API key: {endpoint}")

        effective_max_tokens = min(int(max_tokens or cfg.max_tokens), int(cfg.max_tokens))
        client = self._client_for(cfg)
        return client.generate(system, prompt, max_tokens=effective_max_tokens)

    def generate_with_fallback(
        self,
        db: Session,
        *,
        primary: AIEndpoint = "primary",
        fallback: AIEndpoint = "secondary",
        system: str,
        prompt: str,
        max_tokens: int | None = None,
    ) -> tuple[AIResponse, dict[str, Any]]:
        """
        Try primary; if it fails and fallback is configured, try fallback.
        Returns (result, metadata) where metadata indicates whether fallback was used.
        """
        try:
            result = self.generate(db, endpoint=primary, system=system, prompt=prompt, max_tokens=max_tokens)
            return result, {"endpoint": primary, "fallback_used": False}
        except AIClientError as exc:
            logger.warning("AI primary endpoint failed (%s). Trying fallback.", primary)
            if not self.endpoint_ready(db, fallback):
                raise
            result = self.generate(db, endpoint=fallback, system=system, prompt=prompt, max_tokens=max_tokens)
            return result, {"endpoint": fallback, "fallback_used": True, "primary_error": str(exc)}


ai_gateway = AIGateway()

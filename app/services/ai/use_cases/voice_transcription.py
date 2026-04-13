from __future__ import annotations

import os
from dataclasses import dataclass
from time import sleep
from typing import TypedDict

import httpx
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.services.ai.client import AIClientError, _coerce_float, _coerce_int
from app.services.audit_helpers import log_audit_event
from app.services.settings_spec import resolve_values_atomic

MAX_AUDIO_BYTES = 25 * 1024 * 1024
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"


@dataclass(frozen=True)
class VoiceTranscription:
    text: str
    meta: dict[str, str]


class _VoiceTranscriptionConfig(TypedDict):
    enabled: bool
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float
    max_retries: int


def _endpoint(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}/audio/transcriptions"
    return f"{normalized}/v1/audio/transcriptions"


def _load_config(db: Session) -> _VoiceTranscriptionConfig:
    values = resolve_values_atomic(
        db,
        SettingDomain.integration,
        [
            "ai_enabled",
            "voice_transcription_base_url",
            "voice_transcription_model",
            "voice_transcription_api_key",
            "voice_transcription_timeout_seconds",
            "voice_transcription_max_retries",
            "vllm_base_url",
            "vllm_api_key",
        ],
    )
    return {
        "enabled": bool(values.get("ai_enabled")),
        "base_url": str(
            values.get("voice_transcription_base_url")
            or os.getenv("VOICE_TRANSCRIPTION_BASE_URL")
            or values.get("vllm_base_url")
            or os.getenv("VLLM_BASE_URL")
            or ""
        ).strip(),
        "model": str(
            values.get("voice_transcription_model")
            or os.getenv("VOICE_TRANSCRIPTION_MODEL")
            or DEFAULT_TRANSCRIPTION_MODEL
        ).strip(),
        "api_key": str(
            values.get("voice_transcription_api_key")
            or os.getenv("VOICE_TRANSCRIPTION_API_KEY")
            or values.get("vllm_api_key")
            or os.getenv("VLLM_API_KEY")
            or ""
        ).strip(),
        "timeout_seconds": _coerce_float(
            values.get("voice_transcription_timeout_seconds"),
            default=45.0,
            minimum=1.0,
        ),
        "max_retries": _coerce_int(
            values.get("voice_transcription_max_retries"),
            default=1,
            minimum=0,
        ),
    }


def transcribe_voice_audio(
    db: Session,
    *,
    request,
    audio: bytes,
    filename: str,
    content_type: str,
    actor_person_id: str | None,
    context: str | None = None,
) -> VoiceTranscription:
    if not audio:
        raise ValueError("Voice audio is required")
    if len(audio) > MAX_AUDIO_BYTES:
        raise ValueError("Voice audio is too large")

    cfg = _load_config(db)
    if not cfg["enabled"]:
        raise AIClientError("AI features are disabled (integration.ai_enabled=false)")

    base_url = str(cfg["base_url"])
    model = str(cfg["model"])
    api_key = str(cfg["api_key"])
    if not base_url:
        raise AIClientError("Missing integration setting: voice_transcription_base_url or vllm_base_url")
    if not model:
        raise AIClientError("Missing integration setting: voice_transcription_model")

    headers: dict[str, str] = {}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    attempts = int(cfg["max_retries"]) + 1
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=float(cfg["timeout_seconds"])) as client:
                response = client.post(
                    _endpoint(base_url),
                    headers=headers,
                    data={"model": model, "response_format": "json"},
                    files={"file": (filename or "voice.webm", audio, content_type or "application/octet-stream")},
                )
            if (response.status_code in {408, 409, 425, 429} or response.status_code >= 500) and attempt < attempts:
                sleep(min(2**attempt, 5))
                continue
            response.raise_for_status()
            data = response.json()
            text = str(data.get("text") or "").strip() if isinstance(data, dict) else ""
            if not text:
                raise AIClientError("Transcription returned no text")

            log_audit_event(
                db,
                request,
                action="ai_voice_transcription",
                entity_type="voice_input",
                entity_id=None,
                actor_id=actor_person_id,
                metadata={
                    "context": str(context or ""),
                    "audio_bytes": len(audio),
                    "content_type": content_type,
                    "llm_provider": "voice_transcription",
                    "llm_model": model,
                    "llm_endpoint": base_url,
                },
                status_code=200,
                is_success=True,
            )
            return VoiceTranscription(text=text, meta={"provider": "voice_transcription", "model": model})
        except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError, ValueError, AIClientError) as exc:
            last_error = exc
            if attempt < attempts:
                sleep(min(2**attempt, 5))
                continue
            break

    raise AIClientError("Voice transcription request failed") from last_error

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.services.settings_spec import resolve_value

_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}

_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(DEEPSEEK_API_KEY|OPENAI_API_KEY|VLLM_API_KEY|VOICE_TRANSCRIPTION_API_KEY|authorization)\s*[:=]\s*([^\s,;]+)"
)
_SK_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


def env_bool(name: str) -> bool | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in _FALSE_VALUES:
        return False
    if normalized in _TRUE_VALUES:
        return True
    return None


def ai_disabled_by_env() -> bool:
    return env_bool("AI_ENABLED") is False


def ai_enabled(db: Session) -> bool:
    if ai_disabled_by_env():
        return False
    value = resolve_value(db, SettingDomain.integration, "ai_enabled")
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_VALUES
    return False


def redact_secret_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    return _SK_TOKEN_RE.sub("sk-<redacted>", text)


def is_deepseek_endpoint(base_url: str | None) -> bool:
    if not base_url:
        return False
    parsed = urlparse(base_url.strip())
    host = (parsed.netloc or parsed.path).lower()
    return "deepseek" in host


def resolve_provider_api_key(
    *,
    configured_api_key: object | None,
    base_url: str,
    env_var: str = "VLLM_API_KEY",
) -> str | None:
    if is_deepseek_endpoint(base_url):
        return (os.getenv("DEEPSEEK_API_KEY") or "").strip() or None
    return (
        (os.getenv(env_var) or "").strip()
        or (str(configured_api_key or "").strip() if configured_api_key is not None else "")
        or None
    )


def validate_deepseek_startup_env(*, base_urls: list[str] | None = None) -> None:
    if ai_disabled_by_env():
        return
    if env_bool("AI_ENABLED") is not True:
        return

    urls = list(base_urls or [])
    urls.extend(
        [
            os.getenv("VLLM_BASE_URL") or "",
            os.getenv("VLLM_SECONDARY_BASE_URL") or "",
        ]
    )
    if any(is_deepseek_endpoint(url) for url in urls) and not (os.getenv("DEEPSEEK_API_KEY") or "").strip():
        raise RuntimeError("AI_ENABLED=true with a DeepSeek endpoint requires DEEPSEEK_API_KEY")

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_TOKEN_RE = re.compile(r"\b(?:sk|api|token|key)[A-Za-z0-9_-]{6,}\b", re.IGNORECASE)


def redact_text(value: str, *, max_chars: int = 1200) -> str:
    # Keep it simple and predictable. This is not a full PII scrubber.
    redacted = _EMAIL_RE.sub("[redacted-email]", value or "")
    redacted = _PHONE_RE.sub("[redacted-phone]", redacted)
    redacted = _TOKEN_RE.sub("[redacted-token]", redacted)
    redacted = re.sub(r"\s+", " ", redacted).strip()
    return redacted[: max(100, max_chars)]

"""Search normalization helpers for CRM inbox."""

from __future__ import annotations


def normalize_search(value: str | None) -> str | None:
    if not value:
        return None
    text = " ".join(value.strip().split())
    if not text:
        return None
    return text.lower()

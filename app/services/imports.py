"""Import helpers for legacy CSV workflows.

This module provides compatibility shims for import CLI commands that reference
services removed from the current application. The implementations intentionally
no-op while preserving expected return shapes for callers.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session


def import_subscriber_custom_fields_from_csv(
    db: Session,
    content: str,
) -> tuple[int, list[dict[str, Any]]]:
    """Compatibility shim for removed subscriber custom fields import."""
    _ = db
    _ = content
    return 0, []

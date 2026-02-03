"""Pure-logic condition evaluator for automation rules.

Evaluates JSON condition arrays against event context dictionaries.
No database or SQLAlchemy imports — easy to unit test.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

_MISSING = object()


def evaluate_conditions(conditions: list[dict], context: dict) -> bool:
    """Evaluate all conditions against context (AND logic).

    Returns True if all conditions pass, or if conditions list is empty.
    """
    if not conditions:
        return True

    for condition in conditions:
        field = condition.get("field", "")
        op = condition.get("op", "")
        value = condition.get("value")

        field_value = _resolve_field(context, field)

        if not _evaluate_single(field_value, op, value):
            return False

    return True


def _resolve_field(context: dict, field_path: str) -> Any:
    """Resolve a dot-separated field path from context dict.

    Returns _MISSING sentinel if field is not found.
    """
    if not field_path:
        return _MISSING

    parts = field_path.split(".")
    current: Any = context

    for part in parts:
        if isinstance(current, dict):
            if part in current:
                current = current[part]
            else:
                return _MISSING
        else:
            return _MISSING

    return current


def _evaluate_single(field_value: Any, op: str, expected: Any) -> bool:
    """Evaluate a single condition.

    Returns False for unknown operators (fail-closed).
    """
    if op == "exists":
        return field_value is not _MISSING

    if op == "not_exists":
        return field_value is _MISSING

    # For remaining ops, missing field means condition fails
    if field_value is _MISSING:
        return False

    if op == "eq":
        return _loose_equals(field_value, expected)

    if op == "neq":
        return not _loose_equals(field_value, expected)

    if op == "in":
        if isinstance(expected, list):
            return field_value in expected
        return False

    if op == "not_in":
        if isinstance(expected, list):
            return field_value not in expected
        return True

    if op == "contains":
        if isinstance(field_value, str) and isinstance(expected, str):
            return expected in field_value
        if isinstance(field_value, list | tuple):
            return expected in field_value
        return False

    if op in ("gt", "lt", "gte", "lte"):
        return _compare_numeric(field_value, op, expected)

    logger.warning("Unknown automation condition operator: %s", op)
    return False


def _loose_equals(a: Any, b: Any) -> bool:
    """Compare with type coercion for numeric strings.

    Handles the case where form inputs produce string values (e.g. "5")
    that need to compare against numeric payload values (e.g. 5).
    """
    if a == b:
        return True
    # Try numeric coercion if types differ
    if type(a) is not type(b):
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            pass
    return False


def _compare_numeric(field_value: Any, op: str, expected: Any) -> bool:
    """Attempt numeric comparison, returning False on coercion failure."""
    try:
        a = float(field_value)
        b = float(expected)
    except (TypeError, ValueError):
        return False

    if op == "gt":
        return a > b
    if op == "lt":
        return a < b
    if op == "gte":
        return a >= b
    if op == "lte":
        return a <= b
    return False

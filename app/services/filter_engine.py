from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, or_

from app.models.projects import Project, ProjectTask
from app.models.tickets import Ticket
from app.services.common import coerce_uuid
from app.services.filter_contract import FILTER_SCHEMA, FilterExpression, FilterTerm, normalize_doctype

MODEL_BY_DOCTYPE = {
    "Ticket": Ticket,
    "Project": Project,
    "Project Task": ProjectTask,
}


def _parse_datetime_like(value: Any) -> date | datetime:
    if isinstance(value, (date, datetime)):
        return value
    if not isinstance(value, str):
        raise ValueError("Date/datetime value must be a valid ISO-8601 string.")
    if len(value) <= 10:
        return date.fromisoformat(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _coerce_term_value(term: FilterTerm, value: Any) -> Any:
    spec = FILTER_SCHEMA[term.doctype][term.field]
    if value is None:
        return None
    if spec.type == "uuid":
        return coerce_uuid(value)
    if spec.type in {"date", "datetime"}:
        return _parse_datetime_like(value)
    return value


def _build_predicate(term: FilterTerm, target_doctype: str):
    if term.doctype != target_doctype:
        raise ValueError(
            f"Mixed doctypes are not supported in a single query. Expected '{target_doctype}', got '{term.doctype}'."
        )
    model = MODEL_BY_DOCTYPE[target_doctype]
    column = getattr(model, term.field, None)
    if column is None:
        raise ValueError(f"Field '{term.field}' is not queryable on doctype '{target_doctype}'.")

    op = term.operator
    value = term.value

    if op in {"in", "not in"}:
        values = [_coerce_term_value(term, item) for item in value]
        return column.in_(values) if op == "in" else ~column.in_(values)

    if op in {"like", "not like"}:
        pattern = f"%{value}%"
        return column.ilike(pattern) if op == "like" else ~column.ilike(pattern)

    coerced = _coerce_term_value(term, value)
    if op == "=":
        return column == coerced
    if op == "!=":
        return column != coerced
    if op == ">":
        return column > coerced
    if op == "<":
        return column < coerced
    if op == ">=":
        return column >= coerced
    if op == "<=":
        return column <= coerced
    if op == "is":
        return column.is_(None) if coerced is None else column == coerced
    if op == "is not":
        return column.is_not(None) if coerced is None else column != coerced
    raise ValueError(f"Unsupported operator '{op}'.")


def apply_filter_expression(query, doctype: str, expression: FilterExpression):
    target_doctype = normalize_doctype(doctype)
    if target_doctype not in MODEL_BY_DOCTYPE:
        raise ValueError(f"Unsupported doctype '{doctype}'.")

    predicates = []
    for term in expression.and_terms:
        predicates.append(_build_predicate(term, target_doctype))
    for group in expression.or_groups:
        predicates.append(or_(*[_build_predicate(term, target_doctype) for term in group.items]))

    if not predicates:
        return query
    return query.filter(and_(*predicates))


def apply_filter_payload(query, doctype: str, payload: list[Any]):
    expression = FilterExpression.parse_payload(payload)
    return apply_filter_expression(query, doctype, expression)


def parse_filter_payload_json(raw: str | None) -> list[Any] | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("filters must be a JSON array.")
    return parsed

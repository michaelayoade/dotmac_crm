from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

Operator = Literal["=", "!=", "like", "not like", "in", "not in", ">", "<", ">=", "<=", "is", "is not"]
FieldType = Literal["string", "select", "uuid", "date", "datetime", "number", "boolean"]


@dataclass(frozen=True)
class FieldSpec:
    type: FieldType
    options: set[str] | None = None


# Canonical doctype labels and accepted aliases.
DOCTYPE_ALIASES: dict[str, str] = {
    "ticket": "Ticket",
    "tickets": "Ticket",
    "project": "Project",
    "projects": "Project",
    "project_task": "Project Task",
    "project tasks": "Project Task",
    "project task": "Project Task",
}


FILTER_SCHEMA: dict[str, dict[str, FieldSpec]] = {
    "Ticket": {
        "id": FieldSpec("uuid"),
        "number": FieldSpec("string"),
        "title": FieldSpec("string"),
        "description": FieldSpec("string"),
        "status": FieldSpec(
            "select",
            options={
                "new",
                "open",
                "pending",
                "waiting_on_customer",
                "lastmile_rerun",
                "site_under_construction",
                "on_hold",
                "resolved",
                "closed",
                "canceled",
            },
        ),
        "priority": FieldSpec(
            "select",
            options={"lower", "low", "medium", "normal", "high", "urgent"},
        ),
        "ticket_type": FieldSpec("string"),
        "channel": FieldSpec("select", options={"web", "email", "phone", "chat", "api"}),
        "subscriber_id": FieldSpec("uuid"),
        "lead_id": FieldSpec("uuid"),
        "customer_person_id": FieldSpec("uuid"),
        "created_by_person_id": FieldSpec("uuid"),
        "assigned_to_person_id": FieldSpec("uuid"),
        "ticket_manager_person_id": FieldSpec("uuid"),
        "assistant_manager_person_id": FieldSpec("uuid"),
        "service_team_id": FieldSpec("uuid"),
        "region": FieldSpec("string"),
        "due_at": FieldSpec("datetime"),
        "resolved_at": FieldSpec("datetime"),
        "closed_at": FieldSpec("datetime"),
        "created_at": FieldSpec("datetime"),
        "updated_at": FieldSpec("datetime"),
        "is_active": FieldSpec("boolean"),
    },
    "Project": {
        "id": FieldSpec("uuid"),
        "number": FieldSpec("string"),
        "name": FieldSpec("string"),
        "code": FieldSpec("string"),
        "description": FieldSpec("string"),
        "project_type": FieldSpec(
            "select",
            options={
                "cable_rerun",
                "fiber_optics_relocation",
                "air_fiber_relocation",
                "fiber_optics_installation",
                "air_fiber_installation",
            },
        ),
        "status": FieldSpec("select", options={"open", "planned", "active", "on_hold", "completed", "canceled"}),
        "priority": FieldSpec(
            "select",
            options={"lower", "low", "medium", "normal", "high", "urgent"},
        ),
        "subscriber_id": FieldSpec("uuid"),
        "lead_id": FieldSpec("uuid"),
        "created_by_person_id": FieldSpec("uuid"),
        "owner_person_id": FieldSpec("uuid"),
        "manager_person_id": FieldSpec("uuid"),
        "project_manager_person_id": FieldSpec("uuid"),
        "assistant_manager_person_id": FieldSpec("uuid"),
        "service_team_id": FieldSpec("uuid"),
        "region": FieldSpec("string"),
        "start_at": FieldSpec("datetime"),
        "due_at": FieldSpec("datetime"),
        "completed_at": FieldSpec("datetime"),
        "created_at": FieldSpec("datetime"),
        "updated_at": FieldSpec("datetime"),
        "is_active": FieldSpec("boolean"),
    },
    "Project Task": {
        "id": FieldSpec("uuid"),
        "number": FieldSpec("string"),
        "project_id": FieldSpec("uuid"),
        "title": FieldSpec("string"),
        "description": FieldSpec("string"),
        "status": FieldSpec(
            "select",
            options={"backlog", "todo", "in_progress", "blocked", "done", "canceled"},
        ),
        "priority": FieldSpec(
            "select",
            options={"lower", "low", "medium", "normal", "high", "urgent"},
        ),
        "assigned_to_person_id": FieldSpec("uuid"),
        "created_by_person_id": FieldSpec("uuid"),
        "ticket_id": FieldSpec("uuid"),
        "work_order_id": FieldSpec("uuid"),
        "start_at": FieldSpec("datetime"),
        "due_at": FieldSpec("datetime"),
        "completed_at": FieldSpec("datetime"),
        "effort_hours": FieldSpec("number"),
        "created_at": FieldSpec("datetime"),
        "updated_at": FieldSpec("datetime"),
        "is_active": FieldSpec("boolean"),
    },
}


def _normalize_doctype(value: str) -> str:
    raw = value.strip()
    if raw in FILTER_SCHEMA:
        return raw
    alias_key = raw.lower().replace("-", "_")
    canonical = DOCTYPE_ALIASES.get(alias_key)
    if canonical:
        return canonical
    raise ValueError(f"Unsupported doctype '{value}'.")


def _validate_datetime_like(value: Any) -> None:
    if isinstance(value, (date, datetime)):
        return
    if not isinstance(value, str):
        raise ValueError("Date/datetime value must be a valid ISO-8601 string.")
    try:
        # Accept both YYYY-MM-DD and full datetime values.
        if len(value) <= 10:
            date.fromisoformat(value)
        else:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Date/datetime value must be a valid ISO-8601 string.") from exc


class FilterTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doctype: str
    field: str
    operator: Operator
    value: Any

    @model_validator(mode="after")
    def validate_term(self) -> FilterTerm:
        self.doctype = _normalize_doctype(self.doctype)
        fields = FILTER_SCHEMA[self.doctype]
        if self.field not in fields:
            raise ValueError(f"Field '{self.field}' is not allowed for doctype '{self.doctype}'.")

        spec = fields[self.field]
        operator = self.operator
        value = self.value

        if operator in {"in", "not in"}:
            if not isinstance(value, list) or not value:
                raise ValueError(f"Operator '{operator}' requires a non-empty array value.")
            for item in value:
                self._validate_scalar_compatibility(spec, operator, item)
            return self

        if operator in {"is", "is not"}:
            if value is None:
                return self
            if isinstance(value, str) and value.strip().lower() in {"null", "none"}:
                self.value = None
                return self
            self._validate_scalar_compatibility(spec, operator, value)
            return self

        self._validate_scalar_compatibility(spec, operator, value)
        return self

    @staticmethod
    def _validate_scalar_compatibility(spec: FieldSpec, operator: Operator, value: Any) -> None:
        if operator in {"like", "not like"}:
            if spec.type not in {"string", "select", "uuid"}:
                raise ValueError(f"Operator '{operator}' is not allowed for field type '{spec.type}'.")
            if not isinstance(value, str):
                raise ValueError(f"Operator '{operator}' requires a string value.")
            return

        if operator in {">", "<", ">=", "<="}:
            if spec.type not in {"number", "date", "datetime"}:
                raise ValueError(f"Operator '{operator}' is not allowed for field type '{spec.type}'.")
            if spec.type == "number":
                if not isinstance(value, (int, float)):
                    raise ValueError(f"Operator '{operator}' requires a numeric value.")
            else:
                _validate_datetime_like(value)
            return

        if spec.type == "select":
            if not isinstance(value, str):
                raise ValueError("Select field value must be a string.")
            if spec.options and value not in spec.options:
                raise ValueError(f"Invalid option '{value}'.")
            return

        if spec.type in {"string", "uuid"} and not isinstance(value, str):
            raise ValueError(f"Field type '{spec.type}' requires a string value.")
        if spec.type == "number" and not isinstance(value, (int, float)):
            raise ValueError("Number field requires a numeric value.")
        if spec.type == "boolean" and not isinstance(value, bool):
            raise ValueError("Boolean field requires a boolean value.")
        if spec.type in {"date", "datetime"}:
            _validate_datetime_like(value)

    @classmethod
    def from_row(cls, row: list[Any]) -> FilterTerm:
        if not isinstance(row, list) or len(row) != 4:
            raise ValueError("Filter row must be a 4-item array: [doctype, field, operator, value].")
        return cls(
            doctype=str(row[0]),
            field=str(row[1]),
            operator=cast(Operator, str(row[2])),
            value=row[3],
        )

    def to_row(self) -> list[Any]:
        return [self.doctype, self.field, self.operator, self.value]


class OrGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[FilterTerm]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> OrGroup:
        if "or" not in payload:
            raise ValueError("OR group must include key 'or'.")
        rows = payload.get("or")
        if not isinstance(rows, list) or not rows:
            raise ValueError("OR group must contain at least one filter term.")
        items: list[FilterTerm] = []
        for row in rows:
            if isinstance(row, list):
                items.append(FilterTerm.from_row(row))
            elif isinstance(row, dict):
                items.append(FilterTerm.model_validate(row))
            else:
                raise ValueError("OR group entries must be filter rows or filter objects.")
        return cls(items=items)


class FilterExpression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    and_terms: list[FilterTerm] = Field(default_factory=list)
    or_groups: list[OrGroup] = Field(default_factory=list)

    @classmethod
    def parse_payload(cls, payload: list[Any]) -> FilterExpression:
        if not isinstance(payload, list):
            raise ValueError("Filter payload must be a list.")

        and_terms: list[FilterTerm] = []
        or_groups: list[OrGroup] = []
        for entry in payload:
            if isinstance(entry, list):
                and_terms.append(FilterTerm.from_row(entry))
                continue
            if isinstance(entry, dict) and "or" in entry:
                or_groups.append(OrGroup.from_payload(entry))
                continue
            raise ValueError("Each filter entry must be a row array or an OR group object.")
        return cls(and_terms=and_terms, or_groups=or_groups)

    def to_payload(self) -> list[Any]:
        payload: list[Any] = [term.to_row() for term in self.and_terms]
        payload.extend([{"or": [term.to_row() for term in group.items]} for group in self.or_groups])
        return payload


def normalize_doctype(value: str) -> str:
    return _normalize_doctype(value)

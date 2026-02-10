from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.domain_settings import SettingValueType


class CSVRowModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    @field_validator("*", mode="before")
    @classmethod
    def _normalize_csv_value(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if stripped == "":
                return None
            lowered = stripped.lower()
            if lowered in {"true", "false", "yes", "no", "1", "0"}:
                return lowered in {"true", "yes", "1"}
            return stripped
        return value


class PersonImportRow(CSVRowModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    organization_id: UUID | None = None
    is_active: bool = True
    notes: str | None = None


class DomainSettingImportRow(CSVRowModel):
    domain: str
    key: str
    value_type: SettingValueType = SettingValueType.string
    value_text: str | None = None
    value_json: dict | None = None
    is_secret: bool = False
    is_active: bool = True

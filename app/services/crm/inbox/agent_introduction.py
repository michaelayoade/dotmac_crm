"""Agent introduction template helpers for CRM inbox."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.person import Person

DEFAULT_INTRODUCTION_TEMPLATE = "Hi, my name is {agent_name} and I will be assisting you today."
INTRODUCTION_TEMPLATE_METADATA_KEY = "crm_inbox_introduction_template"
SUPPORTED_VARIABLES = {"agent_name"}
_VARIABLE_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@dataclass(frozen=True)
class IntroductionTemplateResult:
    ok: bool
    template: str
    error_detail: str | None = None


def _person_name(person: Person | None) -> str:
    if not person:
        return ""
    display_name = str(person.display_name or "").strip()
    if display_name:
        return display_name
    full_name = " ".join(part for part in [person.first_name, person.last_name] if str(part or "").strip()).strip()
    return full_name


def _current_user_name(current_user: dict | None) -> str:
    if not current_user:
        return ""
    for key in ("display_name", "full_name", "name"):
        value = str(current_user.get(key) or "").strip()
        if value and value.lower() != "unknown user":
            return value
    first = str(current_user.get("first_name") or "").strip()
    last = str(current_user.get("last_name") or "").strip()
    return " ".join(part for part in [first, last] if part).strip()


def _coerce_person_id(current_user: dict | None) -> uuid.UUID | None:
    raw = (current_user or {}).get("person_id") or (current_user or {}).get("id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None


def get_agent_person(db: Session, current_user: dict | None) -> Person | None:
    person_id = _coerce_person_id(current_user)
    if not person_id:
        return None
    return db.get(Person, person_id)


def resolve_agent_name(db: Session, current_user: dict | None) -> str:
    person = get_agent_person(db, current_user)
    return _person_name(person) or _current_user_name(current_user) or "your support agent"


def validate_introduction_template(template: str | None) -> IntroductionTemplateResult:
    normalized = str(template or "").strip()
    if not normalized:
        normalized = DEFAULT_INTRODUCTION_TEMPLATE
    if len(normalized) > 500:
        return IntroductionTemplateResult(False, normalized, "Introduction template must be 500 characters or fewer.")
    variables = set(_VARIABLE_RE.findall(normalized))
    unsupported = sorted(variables - SUPPORTED_VARIABLES)
    if unsupported:
        names = ", ".join(f"{{{name}}}" for name in unsupported)
        return IntroductionTemplateResult(
            False, normalized, f"Unsupported variable: {names}. Only {{agent_name}} is supported."
        )
    return IntroductionTemplateResult(True, normalized)


def get_introduction_template(db: Session, current_user: dict | None) -> str:
    person = get_agent_person(db, current_user)
    metadata = person.metadata_ if person and isinstance(person.metadata_, dict) else {}
    saved = str(metadata.get(INTRODUCTION_TEMPLATE_METADATA_KEY) or "").strip()
    return saved or DEFAULT_INTRODUCTION_TEMPLATE


def render_introduction_template(db: Session, current_user: dict | None) -> str:
    template = get_introduction_template(db, current_user)
    return template.replace("{agent_name}", resolve_agent_name(db, current_user))


def save_introduction_template(
    db: Session,
    current_user: dict | None,
    template: str | None,
) -> IntroductionTemplateResult:
    result = validate_introduction_template(template)
    if not result.ok:
        return result

    person = get_agent_person(db, current_user)
    if not person:
        return IntroductionTemplateResult(False, result.template, "Agent profile not found.")

    metadata = dict(person.metadata_) if isinstance(person.metadata_, dict) else {}
    if result.template == DEFAULT_INTRODUCTION_TEMPLATE:
        metadata.pop(INTRODUCTION_TEMPLATE_METADATA_KEY, None)
    else:
        metadata[INTRODUCTION_TEMPLATE_METADATA_KEY] = result.template
    person.metadata_ = metadata
    db.add(person)
    db.commit()
    return result

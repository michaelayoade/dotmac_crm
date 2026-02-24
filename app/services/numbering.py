from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.inventory import InventoryItem
from app.models.material_request import MaterialRequest
from app.models.projects import Project, ProjectTask
from app.models.sequence import DocumentSequence
from app.models.tickets import Ticket
from app.services import settings_spec


def _format_number(prefix: str | None, padding: int | None, value: int) -> str:
    prefix_value = prefix or ""
    pad = max(int(padding or 0), 0)
    if pad > 0:
        return f"{prefix_value}{value:0{pad}d}"
    return f"{prefix_value}{value}"


def _resolve_dynamic_prefix(prefix: str | None) -> tuple[str, bool]:
    if not prefix:
        return "", False
    now = datetime.now(UTC)
    tokens = {
        "{YYYY}": now.strftime("%Y"),
        "{MM}": now.strftime("%m"),
        "{DD}": now.strftime("%d"),
        "{YYYYMM}": now.strftime("%Y%m"),
        "{YYYYMMDD}": now.strftime("%Y%m%d"),
    }
    rendered = prefix
    dynamic = False
    for token, value in tokens.items():
        if token in rendered:
            rendered = rendered.replace(token, value)
            dynamic = True
    return rendered, dynamic


def _next_sequence_value(db: Session, key: str, start_value: int) -> int:
    sequence = db.query(DocumentSequence).filter(DocumentSequence.key == key).with_for_update().first()
    if not sequence:
        sequence = DocumentSequence(key=key, next_value=start_value)
        db.add(sequence)
        db.flush()
    value = sequence.next_value
    sequence.next_value = value + 1
    db.flush()
    return value


def _resolve_setting(db: Session, domain: SettingDomain, key: str):
    return settings_spec.resolve_value(db, domain, key)


def generate_number(
    db: Session,
    domain: SettingDomain,
    sequence_key: str,
    enabled_key: str,
    prefix_key: str,
    padding_key: str,
    start_key: str,
) -> str | None:
    enabled = _resolve_setting(db, domain, enabled_key)
    if enabled is False:
        return None
    prefix_template = _resolve_setting(db, domain, prefix_key)
    prefix, has_dynamic_prefix = _resolve_dynamic_prefix(prefix_template)
    padding = _resolve_setting(db, domain, padding_key)
    start_value = _resolve_setting(db, domain, start_key)
    try:
        start_value_int = int(start_value) if start_value is not None else 1
    except (TypeError, ValueError):
        start_value_int = 1
    effective_sequence_key = f"{sequence_key}:{prefix}" if has_dynamic_prefix else sequence_key
    value = _next_sequence_value(db, effective_sequence_key, start_value_int)
    return _format_number(prefix, padding, value)


def backfill_number_prefixes(db: Session) -> dict[str, int]:
    """Prefix existing numbers if missing (no padding changes)."""
    prefix_map = {
        "tickets": _resolve_setting(db, SettingDomain.numbering, "ticket_number_prefix") or "",
        "projects": _resolve_setting(db, SettingDomain.numbering, "project_number_prefix") or "",
        "project_tasks": _resolve_setting(db, SettingDomain.numbering, "project_task_number_prefix") or "",
        "material_requests": _resolve_setting(db, SettingDomain.numbering, "material_request_number_prefix") or "",
        "inventory_items": _resolve_setting(db, SettingDomain.numbering, "inventory_item_number_prefix") or "",
    }
    updated = {"tickets": 0, "projects": 0, "project_tasks": 0, "material_requests": 0, "inventory_items": 0}

    def _apply_prefix(model, label: str, prefix: str, field_name: str = "number"):
        if not prefix:
            return
        _rendered_prefix, has_dynamic_prefix = _resolve_dynamic_prefix(prefix)
        if has_dynamic_prefix:
            # Dynamic prefixes are time-based; avoid backfilling with the current token value.
            return
        field = getattr(model, field_name)
        rows = db.query(model).filter(field.isnot(None)).filter(~field.startswith(prefix)).all()
        for row in rows:
            current = getattr(row, field_name)
            setattr(row, field_name, f"{prefix}{current}")
        updated[label] = len(rows)

    _apply_prefix(Ticket, "tickets", prefix_map["tickets"])
    _apply_prefix(Project, "projects", prefix_map["projects"])
    _apply_prefix(ProjectTask, "project_tasks", prefix_map["project_tasks"])
    _apply_prefix(MaterialRequest, "material_requests", prefix_map["material_requests"])
    _apply_prefix(InventoryItem, "inventory_items", prefix_map["inventory_items"], field_name="sku")

    if any(updated.values()):
        db.commit()
    return updated

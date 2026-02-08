"""Admin system management web routes."""

import json
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, Query, Request, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Any, Optional, cast
from uuid import UUID
from pydantic import ValidationError

from app.db import SessionLocal
from app.models.auth import ApiKey, MFAMethod, UserCredential, Session as AuthSession
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.sales import Lead, Quote
from app.models.crm.team import CrmAgent
from app.models.projects import TaskStatus
from app.models.projects import Project, ProjectTask, ProjectComment, ProjectTaskComment
from app.models.rbac import Permission, PersonPermission, PersonRole, Role, RolePermission
from app.models.vendor import VendorUser
from app.models.subscriber import ResellerUser
from app.models.tickets import Ticket, TicketComment, TicketStatus
from app.models.workflow import WorkflowEntityType
from app.models.workforce import WorkOrder, WorkOrderAssignment, WorkOrderNote, WorkOrderStatus
from app.models.webhook import WebhookDelivery, WebhookDeliveryStatus, WebhookEndpoint, WebhookSubscription
from app.schemas.auth import UserCredentialCreate
from app.schemas.crm.campaign_sender import CampaignSenderCreate, CampaignSenderUpdate
from app.schemas.crm.campaign_smtp import CampaignSmtpCreate, CampaignSmtpUpdate
from app.schemas.person import PersonCreate, PersonUpdate
from app.schemas.settings import DomainSettingUpdate
from app.schemas.workflow import (
    ProjectTaskStatusTransitionCreate,
    SlaPolicyCreate,
    SlaTargetCreate,
    TicketStatusTransitionCreate,
    WorkOrderStatusTransitionCreate,
)
from app.schemas.rbac import (
    PermissionCreate,
    PermissionUpdate,
    PersonRoleCreate,
    RoleCreate,
    RolePermissionCreate,
    RoleUpdate,
)
from app.services import (
    audit as audit_service,
    auth as auth_service,
    auth_flow as auth_flow_service,
    email as email_service,
    rbac as rbac_service,
    settings_api as settings_service,
    scheduler as scheduler_service,
    person as person_service,
    workflow as workflow_service,
)
from app.services import settings_spec
from app.services.crm.campaign_senders import campaign_senders
from app.services.crm.campaign_smtp_configs import campaign_smtp_configs
from app.services import branding_assets
from app.services.auth_flow import hash_password
from app.services.auth_dependencies import require_permission
from app.services.common import coerce_uuid

templates = Jinja2Templates(directory="templates")
router = APIRouter(prefix="/system", tags=["web-admin-system"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _is_admin_request(request: Request) -> bool:
    auth = getattr(request.state, "auth", {}) or {}
    roles = auth.get("roles") or []
    return any(str(role).lower() == "admin" for role in roles)


def _placeholder_context(request: Request, db: Session, title: str, active_page: str):
    from app.web.admin import get_sidebar_stats, get_current_user
    return {
        "request": request,
        "active_page": active_page,
        "active_menu": "system",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "page_title": title,
        "heading": title,
        "description": f"{title} configuration will appear here.",
        "empty_title": f"No {title.lower()} yet",
        "empty_message": "System configuration will appear once it is enabled.",
    }


@router.get("/health", response_class=HTMLResponse)
def system_health_page(request: Request, db: Session = Depends(get_db)):
    from app.models.domain_settings import SettingDomain
    from app.services import system_health as system_health_service, settings_spec
    from app.web.admin import get_sidebar_stats, get_current_user

    health = system_health_service.get_system_health()
    thresholds: dict[str, float | None] = {
        "disk_warn_pct": cast(
            float | None,
            settings_spec.resolve_value(
            db, SettingDomain.network_monitoring, "server_health_disk_warn_pct"
            ),
        ),
        "disk_crit_pct": cast(
            float | None,
            settings_spec.resolve_value(
            db, SettingDomain.network_monitoring, "server_health_disk_crit_pct"
            ),
        ),
        "mem_warn_pct": cast(
            float | None,
            settings_spec.resolve_value(
            db, SettingDomain.network_monitoring, "server_health_mem_warn_pct"
            ),
        ),
        "mem_crit_pct": cast(
            float | None,
            settings_spec.resolve_value(
            db, SettingDomain.network_monitoring, "server_health_mem_crit_pct"
            ),
        ),
        "load_warn": cast(
            float | None,
            settings_spec.resolve_value(
            db, SettingDomain.network_monitoring, "server_health_load_warn"
            ),
        ),
        "load_crit": cast(
            float | None,
            settings_spec.resolve_value(
            db, SettingDomain.network_monitoring, "server_health_load_crit"
            ),
        ),
    }
    for key, value in thresholds.items():
        if value is None:
            thresholds[key] = None
            continue
        if isinstance(value, (int, float)):
            thresholds[key] = float(value)
            continue
        if isinstance(value, str):
            try:
                thresholds[key] = float(value)
            except ValueError:
                thresholds[key] = None
            continue
        thresholds[key] = None
    health_status = system_health_service.evaluate_health(health, thresholds)
    context: dict[str, object] = {
        "request": request,
        "active_page": "system-health",
        "active_menu": "system",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "health": health,
        "health_status": health_status,
    }
    return templates.TemplateResponse("admin/system/health.html", context)


def _form_bool(value: object | None) -> bool:
    value_str = _form_str(value).strip().lower()
    if not value_str:
        return False
    return value_str in {"1", "true", "yes", "on"}


def _form_str(value: object | None) -> str:
    return value if isinstance(value, str) else ""


def _form_str_opt(value: object | None) -> str | None:
    value_str = _form_str(value).strip()
    return value_str or None


def _linked_user_labels(db: Session, person_id) -> list[str]:
    checks = [
        ("CRM agent", db.query(CrmAgent.id).filter(CrmAgent.person_id == person_id)),
        ("CRM conversations", db.query(Conversation.id).filter(Conversation.person_id == person_id)),
        ("CRM assignments", db.query(ConversationAssignment.id).filter(ConversationAssignment.assigned_by_id == person_id)),
        ("CRM leads", db.query(Lead.id).filter(Lead.person_id == person_id)),
        ("CRM quotes", db.query(Quote.id).filter(Quote.person_id == person_id)),
        (
            "Tickets",
            db.query(Ticket.id).filter(
                or_(
                    Ticket.created_by_person_id == person_id,
                    Ticket.assigned_to_person_id == person_id,
                )
            ),
        ),
        ("Ticket comments", db.query(TicketComment.id).filter(TicketComment.author_person_id == person_id)),
        ("Work orders", db.query(WorkOrder.id).filter(WorkOrder.assigned_to_person_id == person_id)),
        ("Work order assignments", db.query(WorkOrderAssignment.id).filter(WorkOrderAssignment.person_id == person_id)),
        ("Work order notes", db.query(WorkOrderNote.id).filter(WorkOrderNote.author_person_id == person_id)),
        (
            "Projects",
            db.query(Project.id).filter(
                or_(
                    Project.created_by_person_id == person_id,
                    Project.owner_person_id == person_id,
                    Project.manager_person_id == person_id,
                )
            ),
        ),
        (
            "Project tasks",
            db.query(ProjectTask.id).filter(
                or_(
                    ProjectTask.assigned_to_person_id == person_id,
                    ProjectTask.created_by_person_id == person_id,
                )
            ),
        ),
        ("Project task comments", db.query(ProjectTaskComment.id).filter(ProjectTaskComment.author_person_id == person_id)),
        ("Project comments", db.query(ProjectComment.id).filter(ProjectComment.author_person_id == person_id)),
    ]
    linked = []
    for label, query in checks:
        if query.first():
            linked.append(label)
    return linked


def _blocked_delete_response(request: Request, linked: list[str], detail: str | None = None):
    if detail is None:
        if linked:
            detail = f"Cannot delete user. Linked to: {', '.join(linked)}."
        else:
            detail = "Cannot delete user. Linked records exist."
    if request.headers.get("HX-Request"):
        trigger = {
            "showToast": {
                "type": "error",
                "title": "Delete blocked",
                "message": detail,
            }
        }
        return Response(status_code=409, headers={"HX-Trigger": json.dumps(trigger)})
    raise HTTPException(status_code=409, detail=detail)


def _humanize_integrity_error(exc: IntegrityError) -> str:
    raw = str(getattr(exc, "orig", exc) or "").lower()
    if "user_credentials" in raw and "username" in raw and "already exists" in raw:
        return "Username already exists. Choose a different username or email."
    if "people" in raw and "email" in raw and "already exists" in raw:
        return "Email already exists. Use a different email address."
    if "unique" in raw and "username" in raw:
        return "Username already exists. Choose a different username or email."
    if "unique" in raw and "email" in raw:
        return "Email already exists. Use a different email address."
    return "Could not save this user because the record already exists."


def _error_banner(message: str, status_code: int = 409) -> HTMLResponse:
    return HTMLResponse(
        '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">'
        f"{message}"
        "</div>",
        status_code=status_code,
    )


ENFORCEMENT_DOMAIN = "enforcement"


def _settings_domains() -> list[dict]:
    domains = sorted(
        {spec.domain for spec in settings_spec.SETTINGS_SPECS} | {SettingDomain.campaigns},
        key=lambda domain: domain.value,
    )
    items = [
        {"value": domain.value, "label": domain.value.replace("_", " ").title()}
        for domain in domains
    ]
    items.insert(0, {"value": ENFORCEMENT_DOMAIN, "label": "Enforcement & FUP"})
    return items


# Domain groupings by business function
# Note: billing, catalog, collections, usage, radius, subscriber, snmp, tr069 domains have been removed
SETTINGS_DOMAIN_GROUPS = {
    "Enforcement": [ENFORCEMENT_DOMAIN],
    "Notifications": ["notification", "comms", "campaigns"],
    "Services": ["provisioning"],
    "Network": ["network", "network_monitoring", "bandwidth", "gis", "geocoding"],
    "Operations": ["workflow", "projects", "scheduler", "inventory", "numbering"],
    "Integrations": ["integration"],
    "Security & System": ["auth", "audit", "imports"],
}


def _grouped_settings_domains() -> dict[str, list[dict]]:
    """Return settings domains grouped by business function."""
    all_domains = {d["value"]: d for d in _settings_domains()}
    grouped = {}
    used = set()

    for group_name, domain_values in SETTINGS_DOMAIN_GROUPS.items():
        group_domains = []
        for dv in domain_values:
            if dv in all_domains:
                group_domains.append(all_domains[dv])
                used.add(dv)
        if group_domains:
            grouped[group_name] = group_domains

    # Add any remaining domains to "Other"
    other = [d for v, d in all_domains.items() if v not in used]
    if other:
        grouped["Other"] = sorted(other, key=lambda x: x["value"])

    return grouped


def _resolve_settings_domain(value: str | None) -> SettingDomain:
    domains = _settings_domains()
    default_value = domains[0]["value"] if domains else SettingDomain.auth.value
    raw = value or default_value
    if raw == ENFORCEMENT_DOMAIN:
        return SettingDomain.auth
    try:
        return SettingDomain(raw)
    except ValueError:
        return SettingDomain(default_value)


def _enforcement_specs() -> list[settings_spec.SettingSpec]:
    ordered_keys = {
        SettingDomain.network: [
            "mikrotik_session_kill_enabled",
            "address_list_block_enabled",
            "default_mikrotik_address_list",
        ],
    }
    spec_map = {
        (spec.domain, spec.key): spec for spec in settings_spec.SETTINGS_SPECS
    }
    specs: list[settings_spec.SettingSpec] = []
    for domain, keys in ordered_keys.items():
        for key in keys:
            spec = spec_map.get((domain, key))
            if spec:
                specs.append(spec)
    return specs


def _build_settings_context(db: Session, domain_value: str | None) -> dict:
    if domain_value == ENFORCEMENT_DOMAIN:
        sections: list[dict] = []
        for domain, title in (
            (SettingDomain.network, "Network Controls"),
        ):
            specs = [spec for spec in _enforcement_specs() if spec.domain == domain]
            service = settings_spec.DOMAIN_SETTINGS_SERVICE.get(domain)
            existing = {}
            if service:
                items = service.list(db, None, True, "key", "asc", 1000, 0)
                existing = {item.key: item for item in items}
            section_settings = []
            for spec in specs:
                setting = existing.get(spec.key)
                raw = settings_spec.extract_db_value(setting)
                if raw is None:
                    raw = spec.default
                value, error = settings_spec.coerce_value(spec, raw)
                if error:
                    value = spec.default
                display_value = value
                if spec.value_type == settings_spec.SettingValueType.json:
                    if value is None:
                        display_value = ""
                    elif isinstance(value, str):
                        display_value = value
                    else:
                        display_value = json.dumps(value, indent=2, sort_keys=True)
                section_settings.append(
                    {
                        "key": spec.key,
                        "label": spec.label or spec.key.replace("_", " ").title(),
                        "value": value if value is not None else "",
                        "display_value": display_value if display_value is not None else "",
                        "value_type": spec.value_type.value,
                        "allowed": sorted(spec.allowed) if spec.allowed else None,
                        "min_value": spec.min_value,
                        "max_value": spec.max_value,
                        "is_secret": spec.is_secret,
                        "required": spec.required,
                    }
                )
            sections.append({"title": title, "settings": section_settings})
        return {
            "domain": ENFORCEMENT_DOMAIN,
            "domains": _settings_domains(),
            "grouped_domains": _grouped_settings_domains(),
            "settings": [],
            "settings_by_key": {},
            "sections": sections,
        }

    selected_domain = _resolve_settings_domain(domain_value)
    if selected_domain == SettingDomain.campaigns:
        senders = campaign_senders.list(
            db=db,
            is_active=None,
            order_by="name",
            order_dir="asc",
            limit=500,
            offset=0,
        )
        smtp_profiles = campaign_smtp_configs.list(
            db=db,
            is_active=None,
            order_by="name",
            order_dir="asc",
            limit=500,
            offset=0,
        )
        return {
            "domain": selected_domain.value,
            "domains": _settings_domains(),
            "grouped_domains": _grouped_settings_domains(),
            "settings": [],
            "settings_by_key": {},
            "campaign_senders": senders,
            "campaign_smtp_profiles": smtp_profiles,
        }
    domain_specs = settings_spec.list_specs(selected_domain)
    service = settings_spec.DOMAIN_SETTINGS_SERVICE.get(selected_domain)
    existing = {}
    if service:
        items = service.list(db, None, True, "key", "asc", 1000, 0)
        existing = {item.key: item for item in items}
    settings = []
    for spec in domain_specs:
        setting = existing.get(spec.key)
        raw = settings_spec.extract_db_value(setting)
        if raw is None:
            raw = spec.default
        value, error = settings_spec.coerce_value(spec, raw)
        if error:
            value = spec.default
        display_value = value
        if spec.value_type == settings_spec.SettingValueType.json:
            if value is None:
                display_value = ""
            elif isinstance(value, str):
                display_value = value
            else:
                display_value = json.dumps(value, indent=2, sort_keys=True)
        settings.append(
            {
                "key": spec.key,
                "label": spec.label or spec.key.replace("_", " ").title(),
                "value": value if value is not None else "",
                "display_value": display_value if display_value is not None else "",
                "value_type": spec.value_type.value,
                "allowed": sorted(spec.allowed) if spec.allowed else None,
                "min_value": spec.min_value,
                "max_value": spec.max_value,
                "is_secret": spec.is_secret,
                "required": spec.required,
            }
        )
    settings_by_key = {item["key"]: item for item in settings}
    context = {
        "domain": selected_domain.value,
        "domains": _settings_domains(),
        "grouped_domains": _grouped_settings_domains(),
        "settings": settings,
        "settings_by_key": settings_by_key,
    }
    if selected_domain == SettingDomain.projects:
        from app.web.admin.projects import REGION_OPTIONS
        from app.services import dispatch as dispatch_service

        technicians = dispatch_service.technicians.list(
            db=db,
            person_id=None,
            region=None,
            is_active=True,
            order_by="created_at",
            order_dir="desc",
            limit=500,
            offset=0,
        )
        technicians = sorted(
            technicians,
            key=lambda tech: (
                tech.person.first_name if tech.person else "",
                tech.person.last_name if tech.person else "",
            ),
        )
        assignments = settings_by_key.get("region_pm_assignments", {}).get("value")
        if not isinstance(assignments, dict):
            assignments = {}
        normalized = {}
        for region in REGION_OPTIONS:
            entry = assignments.get(region)
            if isinstance(entry, dict):
                normalized[region] = {
                    "manager_person_id": entry.get("manager_person_id") or "",
                    "assistant_person_id": entry.get("assistant_person_id") or "",
                }
            elif isinstance(entry, str):
                normalized[region] = {"manager_person_id": entry, "assistant_person_id": ""}
            else:
                normalized[region] = {"manager_person_id": "", "assistant_person_id": ""}
        context["region_pm_regions"] = REGION_OPTIONS
        context["region_pm_assignments"] = normalized
        context["technicians"] = technicians
    if selected_domain == SettingDomain.comms:
        context["ticket_types_list"] = _normalize_ticket_types(
            settings_by_key.get("ticket_types", {}).get("value")
        )
    return context


def _normalize_ticket_types(raw: object) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    priority_map = {
        "lower": "lower",
        "low": "low",
        "normal": "normal",
        "medium": "medium",
        "high": "high",
        "urgent": "urgent",
    }
    normalized = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        priority_raw = (item.get("priority") or "").strip().lower()
        priority = priority_map.get(priority_raw) if priority_raw else ""
        is_active = item.get("is_active")
        is_active = True if is_active is None else bool(is_active)
        normalized.append(
            {"name": name, "priority": priority, "is_active": is_active}
        )
    return normalized


def _extract_ticket_types_from_form(form) -> list[dict]:
    items: list[dict] = []
    priority_map = {
        "lower": "lower",
        "low": "low",
        "normal": "normal",
        "medium": "medium",
        "high": "high",
        "urgent": "urgent",
    }
    indices: list[int] = []
    for key in form.keys():
        if key.startswith("ticket_type_name_"):
            try:
                indices.append(int(key.split("_")[-1]))
            except ValueError:
                continue
    for idx in sorted(set(indices)):
        name = _form_str_opt(form.get(f"ticket_type_name_{idx}")) or ""
        name = name.strip()
        if not name:
            continue
        priority_raw = _form_str_opt(form.get(f"ticket_type_priority_{idx}")) or ""
        priority = priority_map.get(priority_raw.strip().lower()) if priority_raw else None
        is_active = form.get(f"ticket_type_active_{idx}") == "true"
        items.append(
            {"name": name, "priority": priority, "is_active": is_active}
        )
    return items


def _extract_region_pm_assignments_from_form(form) -> dict:
    from app.web.admin.projects import REGION_OPTIONS

    assignments: dict[str, dict[str, str]] = {}
    for region in REGION_OPTIONS:
        manager_key = f"region_pm_manager_{region}"
        assistant_key = f"region_pm_assistant_{region}"
        manager = _form_str_opt(form.get(manager_key)) or ""
        assistant = _form_str_opt(form.get(assistant_key)) or ""
        assignments[region] = {
            "manager_person_id": manager.strip(),
            "assistant_person_id": assistant.strip(),
        }
    return assignments


def _user_stats(db: Session) -> dict:
    credential_exists = (
        db.query(UserCredential.id)
        .filter(UserCredential.person_id == Person.id)
        .exists()
    )

    total = db.query(Person).filter(credential_exists).count()
    active = db.query(Person).filter(credential_exists).filter(Person.is_active.is_(True)).count()

    admin_role = (
        db.query(Role)
        .filter(Role.name.ilike("admin"))
        .filter(Role.is_active.is_(True))
        .first()
    )
    if admin_role:
        admins = (
            db.query(PersonRole.person_id)
            .join(Person, Person.id == PersonRole.person_id)
            .filter(credential_exists)
            .filter(PersonRole.role_id == admin_role.id)
            .distinct()
            .count()
        )
    else:
        admins = 0

    active_credential = (
        db.query(UserCredential.id)
        .filter(UserCredential.person_id == Person.id)
        .filter(UserCredential.is_active.is_(True))
        .exists()
    )
    pending_credential = (
        db.query(UserCredential.id)
        .filter(UserCredential.person_id == Person.id)
        .filter(UserCredential.is_active.is_(True))
        .filter(UserCredential.must_change_password.is_(True))
        .exists()
    )
    pending = (
        db.query(Person)
        .filter(credential_exists)
        .filter(or_(~active_credential, pending_credential))
        .count()
    )

    return {"total": total, "active": active, "admins": admins, "pending": pending}


def _build_users(
    db: Session,
    search: str | None,
    role_id: str | None,
    status: str | None,
    offset: int,
    limit: int,
):
    query = db.query(Person)
    credential_exists = (
        db.query(UserCredential.id)
        .filter(UserCredential.person_id == Person.id)
        .exists()
    )
    query = query.filter(credential_exists)
    needs_distinct = False

    if search:
        search_value = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Person.first_name.ilike(search_value),
                Person.last_name.ilike(search_value),
                Person.email.ilike(search_value),
                Person.display_name.ilike(search_value),
            )
        )

    if role_id:
        query = query.join(PersonRole).filter(PersonRole.role_id == coerce_uuid(role_id))
        needs_distinct = True

    if status:
        if status == "active":
            query = query.filter(Person.is_active.is_(True))
        elif status == "inactive":
            query = query.filter(Person.is_active.is_(False))
        elif status == "pending":
            active_credential = (
                db.query(UserCredential.id)
                .filter(UserCredential.person_id == Person.id)
                .filter(UserCredential.is_active.is_(True))
                .exists()
            )
            pending_credential = (
                db.query(UserCredential.id)
                .filter(UserCredential.person_id == Person.id)
                .filter(UserCredential.is_active.is_(True))
                .filter(UserCredential.must_change_password.is_(True))
                .exists()
            )
            query = query.filter(or_(~active_credential, pending_credential))

    if needs_distinct:
        total = query.with_entities(Person.id).distinct().count()
        query = query.distinct(Person.id)
    else:
        total = query.count()
    order_by: list = [Person.last_name.asc(), Person.first_name.asc()]
    if needs_distinct:
        order_by.insert(0, Person.id.asc())
    people = query.order_by(*order_by).offset(offset).limit(limit).all()

    person_ids = [person.id for person in people]
    if not person_ids:
        return [], total

    credentials = (
        db.query(UserCredential)
        .filter(UserCredential.person_id.in_(person_ids))
        .all()
    )
    credential_info: dict = {}
    for credential in credentials:
        info = credential_info.setdefault(
            credential.person_id,
            {"last_login": None, "has_active": False, "must_change_password": False},
        )
        if credential.is_active:
            info["has_active"] = True
            if credential.must_change_password:
                info["must_change_password"] = True
        if credential.last_login_at:
            if info["last_login"] is None or credential.last_login_at > info["last_login"]:
                info["last_login"] = credential.last_login_at

    mfa_enabled = {
        method.person_id
        for method in db.query(MFAMethod)
        .filter(MFAMethod.person_id.in_(person_ids))
        .filter(MFAMethod.enabled.is_(True))
        .filter(MFAMethod.is_active.is_(True))
        .all()
    }

    roles_query = (
        db.query(PersonRole, Role)
        .join(Role, Role.id == PersonRole.role_id)
        .filter(PersonRole.person_id.in_(person_ids))
        .order_by(PersonRole.assigned_at.desc())
        .all()
    )
    role_map: dict = {}
    for person_role, role in roles_query:
        if person_role.person_id not in role_map:
            role_map[person_role.person_id] = []
        role_map[person_role.person_id].append({
            "id": str(role.id),
            "name": role.name,
            "is_active": role.is_active,
        })

    users = []
    for person in people:
        name = person.display_name or f"{person.first_name} {person.last_name}".strip()
        info = credential_info.get(person.id, {})
        users.append(
            {
                "id": str(person.id),
                "name": name,
                "email": person.email,
                "roles": role_map.get(person.id, []),
                "is_active": bool(person.is_active),
                "mfa_enabled": person.id in mfa_enabled,
                "last_login": info.get("last_login"),
            }
        )

    return users, total


def _workflow_context(request: Request, db: Session, error: str | None = None):
    from app.web.admin import get_sidebar_stats, get_current_user
    policies = workflow_service.sla_policies.list(
        db=db,
        entity_type=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    targets = workflow_service.sla_targets.list(
        db=db,
        policy_id=None,
        priority=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    ticket_transitions = workflow_service.ticket_transitions.list(
        db=db,
        from_status=None,
        to_status=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    work_order_transitions = workflow_service.work_order_transitions.list(
        db=db,
        from_status=None,
        to_status=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    project_task_transitions = workflow_service.project_task_transitions.list(
        db=db,
        from_status=None,
        to_status=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    context: dict[str, object] = {
        "request": request,
        "active_page": "workflow",
        "active_menu": "system",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "policies": policies,
        "targets": targets,
        "ticket_transitions": ticket_transitions,
        "work_order_transitions": work_order_transitions,
        "project_task_transitions": project_task_transitions,
        "workflow_entities": [item.value for item in WorkflowEntityType],
        "ticket_statuses": [item.value for item in TicketStatus],
        "work_order_statuses": [item.value for item in WorkOrderStatus],
        "task_statuses": [item.value for item in TaskStatus],
    }
    if error:
        context["error"] = error
    return context

@router.get("", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:read"))])
def system_overview(request: Request, db: Session = Depends(get_db)):
    """System settings overview."""
    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/system/index.html",
        {
            "request": request,
            "active_page": "system",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get("/configuration", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:read"))])
def system_configuration(request: Request, db: Session = Depends(get_db)):
    """System configuration overview."""
    from app.web.admin import get_sidebar_stats, get_current_user
    from app.models.connector import ConnectorConfig
    from app.models.webhook import WebhookEndpoint
    from app.models.projects import ProjectTemplate
    from app.models.crm.team import CrmAgent

    pop_sites_count = 0
    connectors_count = (
        db.query(ConnectorConfig).filter(ConnectorConfig.is_active.is_(True)).count()
    )
    webhooks_count = (
        db.query(WebhookEndpoint).filter(WebhookEndpoint.is_active.is_(True)).count()
    )
    project_templates_count = (
        db.query(ProjectTemplate).filter(ProjectTemplate.is_active.is_(True)).count()
    )
    crm_agents_count = (
        db.query(CrmAgent).filter(CrmAgent.is_active.is_(True)).count()
    )

    return templates.TemplateResponse(
        "admin/system/configuration/index.html",
        {
            "request": request,
            "active_page": "configuration",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "pop_sites_count": pop_sites_count,
            "connectors_count": connectors_count,
            "webhooks_count": webhooks_count,
            "project_templates_count": project_templates_count,
            "crm_agents_count": crm_agents_count,
        },
    )


@router.get("/users", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:roles:read"))])
def users_list(
    request: Request,
    search: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    offset: Optional[int] = Query(None, ge=0),
    limit: Optional[int] = Query(None, ge=5, le=100),
    db: Session = Depends(get_db),
):
    """List system users."""
    if limit is None:
        limit = per_page
    if offset is None:
        offset = (page - 1) * limit

    users, total = _build_users(db, search, role, status, offset, limit)
    roles = rbac_service.roles.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    stats = _user_stats(db)
    pagination = total > limit

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "admin/system/users/_table_rows.html",
            {"request": request, "users": users},
        )

    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/system/users/index.html",
        {
            "request": request,
            "users": users,
            "search": search,
            "role": role,
            "status": status,
            "stats": stats,
            "roles": roles,
            "pagination": pagination,
            "total": total,
            "offset": offset,
            "limit": limit,
            "htmx_url": "/admin/system/users/filter",
            "htmx_target": "users-table-body",
            "active_page": "users",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get("/users/search", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:roles:read"))])
def users_search(
    request: Request,
    search: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=5, le=100),
    db: Session = Depends(get_db),
):
    users, _ = _build_users(db, search, role, status, offset, limit)
    return templates.TemplateResponse(
        "admin/system/users/_table_rows.html",
        {"request": request, "users": users},
    )


@router.get("/users/filter", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:roles:read"))])
def users_filter(
    request: Request,
    search: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=5, le=100),
    db: Session = Depends(get_db),
):
    users, _ = _build_users(db, search, role, status, offset, limit)
    return templates.TemplateResponse(
        "admin/system/users/_table_rows.html",
        {"request": request, "users": users},
    )


@router.get("/users/profile", response_class=HTMLResponse)
def user_profile(request: Request, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user

    current_user = get_current_user(request)

    # Get person record
    person = None
    credential = None
    mfa_enabled = False
    api_key_count = 0

    if current_user and current_user.get("person_id"):
        person_id = current_user["person_id"]
        person = db.get(Person, coerce_uuid(person_id))
        if person:
            # Get credential
            credential = db.query(UserCredential).filter(
                UserCredential.person_id == person.id,
                UserCredential.is_active.is_(True)
            ).first()
            # Check MFA
            mfa_method = db.query(MFAMethod).filter(
                MFAMethod.person_id == person.id,
                MFAMethod.enabled.is_(True)
            ).first()
            mfa_enabled = mfa_method is not None
            # Count API keys
            api_key_count = db.query(ApiKey).filter(
                ApiKey.person_id == person.id,
                ApiKey.is_active.is_(True),
                ApiKey.revoked_at.is_(None)
            ).count()

    context: dict[str, object] = {
        "request": request,
        "active_page": "users",
        "active_menu": "system",
        "current_user": current_user,
        "sidebar_stats": get_sidebar_stats(db),
        "person": person,
        "credential": credential,
        "mfa_enabled": mfa_enabled,
        "api_key_count": api_key_count,
        "error": None,
        "success": None,
    }
    return templates.TemplateResponse("admin/system/profile.html", context)


@router.post("/users/profile", response_class=HTMLResponse)
def user_profile_update(
    request: Request,
    first_name: str = Form(None),
    last_name: str = Form(None),
    email: str = Form(None),
    phone: str = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user

    current_user = get_current_user(request)
    error = None
    success = None
    person = None

    if current_user and current_user.get("person_id"):
        person_id = current_user["person_id"]
        person = db.get(Person, coerce_uuid(person_id))
        if person:
            try:
                payload = PersonUpdate(
                    first_name=first_name or None,
                    last_name=last_name or None,
                    email=email or None,
                    phone=phone or None,
                )
                person_service.people.update(db, str(person.id), payload)
                person = db.get(Person, person.id)  # Refresh
                success = "Profile updated successfully."
            except Exception as e:
                error = str(e)

    # Get related data
    credential = None
    mfa_enabled = False
    api_key_count = 0
    if person:
        credential = db.query(UserCredential).filter(
            UserCredential.person_id == person.id,
            UserCredential.is_active.is_(True)
        ).first()
        mfa_method = db.query(MFAMethod).filter(
            MFAMethod.person_id == person.id,
            MFAMethod.enabled.is_(True)
        ).first()
        mfa_enabled = mfa_method is not None
        api_key_count = db.query(ApiKey).filter(
            ApiKey.person_id == person.id,
            ApiKey.is_active.is_(True),
            ApiKey.revoked_at.is_(None)
        ).count()

    context: dict[str, object] = {
        "request": request,
        "active_page": "users",
        "active_menu": "system",
        "current_user": current_user,
        "sidebar_stats": get_sidebar_stats(db),
        "person": person,
        "credential": credential,
        "mfa_enabled": mfa_enabled,
        "api_key_count": api_key_count,
        "error": error,
        "success": success,
    }
    return templates.TemplateResponse("admin/system/profile.html", context)


@router.get("/users/{user_id}", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:roles:read"))])
def user_detail(request: Request, user_id: str, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user

    person = person_service.people.get(db, user_id)
    if not person:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "User not found"},
            status_code=404,
        )

    # Get user's roles
    person_roles = (
        db.query(PersonRole)
        .filter(PersonRole.person_id == person.id)
        .all()
    )
    roles = []
    for pr in person_roles:
        role = db.get(Role, pr.role_id)
        if role and role.is_active:
            roles.append(role)

    # Get user's credential
    credential = (
        db.query(UserCredential)
        .filter(UserCredential.person_id == person.id)
        .filter(UserCredential.is_active.is_(True))
        .first()
    )

    # Get MFA methods
    mfa_methods = (
        db.query(MFAMethod)
        .filter(MFAMethod.person_id == person.id)
        .all()
    )

    return templates.TemplateResponse(
        "admin/system/users/detail.html",
        {
            "request": request,
            "user": person,
            "roles": roles,
            "credential": credential,
            "mfa_methods": mfa_methods,
            "active_page": "users",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get("/users/{user_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:assign"))])
def user_edit(request: Request, user_id: str, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user

    person = person_service.people.get(db, user_id)
    if not person:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "User not found"},
            status_code=404,
        )

    roles = rbac_service.roles.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=1000,
        offset=0,
    )

    # Get all roles assigned to this user
    current_roles = (
        db.query(PersonRole)
        .filter(PersonRole.person_id == person.id)
        .all()
    )
    current_role_ids = {str(pr.role_id) for pr in current_roles}

    # Get all permissions for direct assignment UI
    all_permissions = rbac_service.permissions.list(
        db=db,
        is_active=True,
        order_by="key",
        order_dir="asc",
        limit=1000,
        offset=0,
    )

    # Get direct permissions assigned to this user
    direct_permissions = rbac_service.person_permissions.list_for_person(db, str(person.id))
    direct_permission_ids = {str(pp.permission_id) for pp in direct_permissions}

    return templates.TemplateResponse(
        "admin/system/users/edit.html",
        {
            "request": request,
            "user": person,
            "roles": roles,
            "current_role_ids": current_role_ids,
            "all_permissions": all_permissions,
            "direct_permission_ids": direct_permission_ids,
            "can_update_password": False,
            "active_page": "users",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post("/users/{user_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:assign"))])
async def user_edit_submit(
    request: Request,
    user_id: str,
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user

    person = person_service.people.get(db, user_id)
    if not person:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "User not found"},
            status_code=404,
        )

    # Parse form data manually to handle multiple checkbox values
    form_data = await request.form()
    first_name = _form_str(form_data.get("first_name"))
    last_name = _form_str(form_data.get("last_name"))
    display_name = _form_str_opt(form_data.get("display_name"))
    email = _form_str(form_data.get("email"))
    phone = _form_str_opt(form_data.get("phone"))
    is_active = form_data.get("is_active")
    new_password = _form_str_opt(form_data.get("new_password"))
    confirm_password = _form_str_opt(form_data.get("confirm_password"))
    require_password_change = _form_str_opt(form_data.get("require_password_change"))

    # Get multiple values for role_ids and direct_permission_ids
    role_ids = [
        value
        for value in (_form_str(item).strip() for item in form_data.getlist("role_ids"))
        if value
    ]
    direct_permission_ids = [
        value
        for value in (
            _form_str(item).strip() for item in form_data.getlist("direct_permission_ids")
        )
        if value
    ]

    status_value = "active" if _form_bool(is_active) else "inactive"
    payload = PersonUpdate(
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        display_name=display_name,
        email=email.strip(),
        phone=phone,
        is_active=_form_bool(is_active),
        status=status_value,
    )

    roles = rbac_service.roles.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=1000,
        offset=0,
    )
    all_permissions = rbac_service.permissions.list(
        db=db,
        is_active=True,
        order_by="key",
        order_dir="asc",
        limit=1000,
        offset=0,
    )

    try:
        person_service.people.update(db, user_id, payload)
        db.query(UserCredential).filter(
            UserCredential.person_id == person.id,
            UserCredential.is_active.is_(True),
        ).update({"username": email.strip()})

        # Sync roles - add new, remove deselected, keep existing
        desired_role_ids = set(role_ids)
        existing_roles = db.query(PersonRole).filter(PersonRole.person_id == person.id).all()
        existing_role_map = {str(pr.role_id): pr for pr in existing_roles}

        # Remove roles not in desired set
        for role_id_str, person_role in existing_role_map.items():
            if role_id_str not in desired_role_ids:
                db.delete(person_role)

        # Add new roles
        for role_id_str in desired_role_ids:
            if role_id_str not in existing_role_map:
                db.add(PersonRole(person_id=person.id, role_id=UUID(role_id_str)))

        # Sync direct permissions
        rbac_service.person_permissions.sync_for_person(
            db,
            str(person.id),
            set(direct_permission_ids),
            granted_by=getattr(request.state, "actor_id", None),
        )

        if new_password or confirm_password:
            raise ValueError("Password updates are disabled on this page.")
        db.commit()
    except Exception as exc:
        db.rollback()
        current_roles = db.query(PersonRole).filter(PersonRole.person_id == person.id).all()
        current_role_ids = {str(pr.role_id) for pr in current_roles}
        direct_permissions = rbac_service.person_permissions.list_for_person(db, str(person.id))
        direct_permission_ids_set = {str(pp.permission_id) for pp in direct_permissions}
        return templates.TemplateResponse(
            "admin/system/users/edit.html",
            {
                "request": request,
                "user": person,
                "roles": roles,
                "current_role_ids": current_role_ids,
                "all_permissions": all_permissions,
                "direct_permission_ids": direct_permission_ids_set,
                "can_update_password": False,
                "active_page": "users",
                "active_menu": "system",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse(url=f"/admin/system/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/activate", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:assign"))])
def user_activate(request: Request, user_id: str, db: Session = Depends(get_db)):
    person = person_service.people.get(db, user_id)
    person_service.people.update(
        db, user_id, PersonUpdate(is_active=True, status="active")
    )
    db.query(UserCredential).filter(
        UserCredential.person_id == person.id
    ).update({"is_active": True})
    db.commit()
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    return RedirectResponse(url=f"/admin/system/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/deactivate", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:assign"))])
def user_deactivate(request: Request, user_id: str, db: Session = Depends(get_db)):
    person = person_service.people.get(db, user_id)
    person_service.people.update(
        db, user_id, PersonUpdate(is_active=False, status="inactive")
    )
    db.query(UserCredential).filter(
        UserCredential.person_id == person.id
    ).update({"is_active": False})
    db.commit()
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    return RedirectResponse(url=f"/admin/system/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/disable-mfa", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:assign"))])
def user_disable_mfa(request: Request, user_id: str, db: Session = Depends(get_db)):
    person = person_service.people.get(db, user_id)
    db.query(MFAMethod).filter(MFAMethod.person_id == person.id).update(
        {"enabled": False, "is_active": False}
    )
    db.commit()
    return Response(status_code=204)


@router.post("/users/{user_id}/reset-password", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:assign"))])
def user_reset_password(request: Request, user_id: str, db: Session = Depends(get_db)):
    person = person_service.people.get(db, user_id)
    temp_password = secrets.token_urlsafe(16)
    db.query(UserCredential).filter(
        UserCredential.person_id == person.id,
        UserCredential.is_active.is_(True),
    ).update(
        {
            "password_hash": hash_password(temp_password),
            "must_change_password": True,
            "password_updated_at": datetime.now(timezone.utc),
        }
    )
    db.commit()
    trigger = {
        "showToast": {
            "type": "success",
            "title": "Password reset",
            "message": f"Temporary password: {temp_password}",
            "duration": 12000,
        }
    }
    return Response(status_code=204, headers={"HX-Trigger": json.dumps(trigger)})


@router.post("/users", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:assign"))])
def user_create(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    role_id: str = Form(...),
    send_invite: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    role = rbac_service.roles.get(db, role_id)
    temp_password = secrets.token_urlsafe(16)

    def _promote_existing_person(existing_person: Person) -> HTMLResponse | None:
        existing_credential = (
            db.query(UserCredential)
            .filter(UserCredential.person_id == existing_person.id)
            .filter(UserCredential.is_active.is_(True))
            .first()
        )
        if existing_credential:
            return _error_banner("User already exists for this email.")

        existing_role = (
            db.query(PersonRole)
            .filter(PersonRole.person_id == existing_person.id)
            .filter(PersonRole.role_id == role.id)
            .first()
        )
        if not existing_role:
            rbac_service.person_roles.create(
                db,
                PersonRoleCreate(person_id=existing_person.id, role_id=role.id),
            )

        try:
            auth_service.user_credentials.create(
                db,
                UserCredentialCreate(
                    person_id=existing_person.id,
                    username=email,
                    password_hash=hash_password(temp_password),
                    must_change_password=True,
                ),
            )
        except IntegrityError:
            db.rollback()
            return _error_banner("Username already in use.")
        return None

    try:
        person = person_service.people.create(
            db,
            PersonCreate(
                first_name=first_name,
                last_name=last_name,
                display_name=f"{first_name} {last_name}".strip(),
                email=email,
            ),
        )

        rbac_service.person_roles.create(
            db,
            PersonRoleCreate(person_id=person.id, role_id=role.id),
        )

        auth_service.user_credentials.create(
            db,
            UserCredentialCreate(
                person_id=person.id,
                username=email,
                password_hash=hash_password(temp_password),
                must_change_password=True,
            ),
        )
    except HTTPException as exc:
        db.rollback()
        if exc.status_code != 409:
            return _error_banner(str(exc.detail))
        person = (
            db.query(Person)
            .filter(Person.email.ilike(email))
            .first()
        )
        if not person:
            return _error_banner(str(exc.detail))
        error_response = _promote_existing_person(person)
        if error_response is not None:
            return error_response
    except IntegrityError as exc:
        db.rollback()
        person = (
            db.query(Person)
            .filter(Person.email.ilike(email))
            .first()
        )
        if not person:
            return _error_banner(_humanize_integrity_error(exc))
        error_response = _promote_existing_person(person)
        if error_response is not None:
            return error_response

    note = "User created. Ask the user to reset their password."
    if send_invite:
        reset = auth_flow_service.request_password_reset(db=db, email=email)
        if reset and reset.get("token"):
            sent = email_service.send_user_invite_email(
                db,
                to_email=email,
                reset_token=reset["token"],
                person_name=reset.get("person_name"),
            )
            if sent:
                note = "Invitation sent. Password reset email delivered."
            else:
                note = "User created, but the reset email could not be sent."
        else:
            note = "User created, but no reset token was generated."
    if request.headers.get("HX-Request"):
        trigger = {
            "showToast": {
                "type": "success",
                "title": "User created",
                "message": note,
                "duration": 8000,
            }
        }
        return Response(
            status_code=200,
            headers={
                "HX-Redirect": "/admin/system/users",
                "HX-Trigger": json.dumps(trigger),
            },
        )
    return HTMLResponse(
        '<div class="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">'
        f"{note}"
        "</div>"
    )


@router.delete("/users/{user_id}", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:assign"))])
def user_delete(request: Request, user_id: str, db: Session = Depends(get_db)):
    try:
        person_service.people.hard_delete_user(db, user_id)
    except HTTPException as e:
        linked = person_service.people.linked_user_labels(db, user_id) if "Linked" in str(e.detail) else []
        return _blocked_delete_response(request, linked, detail=e.detail)
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": "/admin/system/users"})
    return RedirectResponse(url="/admin/system/users", status_code=303)


@router.get("/roles", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:roles:read"))])
def roles_list(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    db: Session = Depends(get_db),
):
    """List roles and permissions."""
    from sqlalchemy import func

    offset = (page - 1) * per_page

    roles = rbac_service.roles.list(
        db=db,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )

    all_roles = rbac_service.roles.list(
        db=db,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
    )
    total = len(all_roles)
    total_pages = (total + per_page - 1) // per_page

    # Get user counts per role
    user_counts_query = (
        db.query(PersonRole.role_id, func.count(PersonRole.person_id.distinct()))
        .group_by(PersonRole.role_id)
        .all()
    )
    user_counts = {str(role_id): count for role_id, count in user_counts_query}

    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/system/roles.html",
        {
            "request": request,
            "roles": roles,
            "user_counts": user_counts,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "active_page": "roles",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get("/roles/new", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:roles:write"))])
def role_new(request: Request, db: Session = Depends(get_db)):
    permissions = rbac_service.permissions.list(
        db=db,
        is_active=None,
        order_by="key",
        order_dir="asc",
        limit=1000,
        offset=0,
    )
    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/system/roles_form.html",
        {
            "request": request,
            "role": None,
            "permissions": permissions,
            "selected_permission_ids": set(),
            "action_url": "/admin/system/roles",
            "form_title": "New Role",
            "submit_label": "Create Role",
            "active_page": "roles",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post("/roles", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:roles:write"))])
def role_create(
    request: Request,
    name: str = Form(...),
    description: str | None = Form(None),
    is_active: str | None = Form(None),
    permission_ids: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    description_value = description.strip() if description else None
    payload = RoleCreate(
        name=name.strip(),
        description=description_value or None,
        is_active=_form_bool(is_active),
    )
    try:
        role = rbac_service.roles.create(db, payload)
        for permission_id in permission_ids:
            if not permission_id:
                continue
            rbac_service.role_permissions.create(
                db,
                RolePermissionCreate(
                    role_id=role.id,
                    permission_id=UUID(permission_id),
                ),
            )
    except Exception as exc:
        permissions = rbac_service.permissions.list(
            db=db,
            is_active=None,
            order_by="key",
            order_dir="asc",
            limit=1000,
            offset=0,
        )
        selected_permission_ids = set()
        for permission_id in permission_ids:
            try:
                selected_permission_ids.add(str(UUID(permission_id)))
            except ValueError:
                continue
        from app.web.admin import get_sidebar_stats, get_current_user
        return templates.TemplateResponse(
            "admin/system/roles_form.html",
            {
                "request": request,
                "role": payload.model_dump(),
                "permissions": permissions,
                "selected_permission_ids": selected_permission_ids,
                "action_url": "/admin/system/roles",
                "form_title": "New Role",
                "submit_label": "Create Role",
                "error": str(exc),
                "active_page": "roles",
                "active_menu": "system",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )
    return RedirectResponse(url="/admin/system/roles", status_code=303)


@router.get("/roles/{role_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:roles:write"))])
def role_edit(request: Request, role_id: str, db: Session = Depends(get_db)):
    try:
        role = rbac_service.roles.get(db, role_id)
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "Role not found"},
            status_code=404,
        )
    permissions = rbac_service.permissions.list(
        db=db,
        is_active=None,
        order_by="key",
        order_dir="asc",
        limit=1000,
        offset=0,
    )
    role_permissions = (
        db.query(RolePermission)
        .filter(RolePermission.role_id == role.id)
        .all()
    )
    selected_permission_ids = {str(link.permission_id) for link in role_permissions}
    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/system/roles_form.html",
        {
            "request": request,
            "role": role,
            "permissions": permissions,
            "selected_permission_ids": selected_permission_ids,
            "action_url": f"/admin/system/roles/{role_id}",
            "form_title": "Edit Role",
            "submit_label": "Save Changes",
            "active_page": "roles",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post("/roles/{role_id}", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:roles:write"))])
def role_update(
    request: Request,
    role_id: str,
    name: str = Form(...),
    description: str | None = Form(None),
    is_active: str | None = Form(None),
    permission_ids: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    description_value = description.strip() if description else None
    payload = RoleUpdate(
        name=name.strip(),
        description=description_value or None,
        is_active=_form_bool(is_active),
    )
    try:
        role = rbac_service.roles.update(db, role_id, payload)
        desired_ids: set[UUID] = set()
        for permission_id in permission_ids:
            if not permission_id:
                continue
            desired_ids.add(UUID(permission_id))
        if desired_ids:
            found_ids = {
                str(row[0])
                for row in db.query(Permission.id)
                .filter(Permission.id.in_(desired_ids))
                .all()
            }
            missing = {str(item) for item in desired_ids} - found_ids
            if missing:
                raise ValueError("One or more permissions were not found.")
        existing_links = (
            db.query(RolePermission)
            .filter(RolePermission.role_id == role.id)
            .all()
        )
        existing_ids: dict[UUID, RolePermission] = {
            link.permission_id: link for link in existing_links
        }
        for perm_id, link in existing_ids.items():
            if perm_id not in desired_ids:
                db.delete(link)
        for perm_id in desired_ids - set(existing_ids.keys()):
            db.add(RolePermission(role_id=role.id, permission_id=perm_id))
        db.commit()
    except Exception as exc:
        permissions = rbac_service.permissions.list(
            db=db,
            is_active=None,
            order_by="key",
            order_dir="asc",
            limit=1000,
            offset=0,
        )
        selected_permission_ids = set()
        for permission_id in permission_ids:
            try:
                selected_permission_ids.add(str(UUID(permission_id)))
            except ValueError:
                continue
        from app.web.admin import get_sidebar_stats, get_current_user
        return templates.TemplateResponse(
            "admin/system/roles_form.html",
            {
                "request": request,
                "role": {"id": role_id, **payload.model_dump()},
                "permissions": permissions,
                "selected_permission_ids": selected_permission_ids,
                "action_url": f"/admin/system/roles/{role_id}",
                "form_title": "Edit Role",
                "submit_label": "Save Changes",
                "error": str(exc),
                "active_page": "roles",
                "active_menu": "system",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )
    return RedirectResponse(url="/admin/system/roles", status_code=303)


@router.post("/roles/{role_id}/delete", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:roles:delete"))])
def role_delete(request: Request, role_id: str, db: Session = Depends(get_db)):
    rbac_service.roles.delete(db, role_id)
    return RedirectResponse(url="/admin/system/roles", status_code=303)


@router.get("/permissions", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:permissions:read"))])
def permissions_list(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(10000, ge=10, le=10000),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * per_page

    permissions = rbac_service.permissions.list(
        db=db,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )

    all_permissions = rbac_service.permissions.list(
        db=db,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
    )
    total = len(all_permissions)
    total_pages = (total + per_page - 1) // per_page

    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/system/permissions.html",
        {
            "request": request,
            "permissions": permissions,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "active_page": "roles",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get("/permissions/new", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:permissions:write"))])
def permission_new(request: Request, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/system/permissions_form.html",
        {
            "request": request,
            "permission": None,
            "action_url": "/admin/system/permissions",
            "form_title": "New Permission",
            "submit_label": "Create Permission",
            "active_page": "roles",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post("/permissions", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:permissions:write"))])
def permission_create(
    request: Request,
    key: str = Form(...),
    description: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    description_value = description.strip() if description else None
    try:
        payload = PermissionCreate(
            key=key.strip(),
            description=description_value or None,
            is_active=_form_bool(is_active),
        )
        rbac_service.permissions.create(db, payload)
    except ValidationError as exc:
        from app.web.admin import get_sidebar_stats, get_current_user
        return templates.TemplateResponse(
            "admin/system/permissions_form.html",
            {
                "request": request,
                "permission": {
                    "key": key.strip(),
                    "description": description_value or None,
                    "is_active": _form_bool(is_active),
                },
                "action_url": "/admin/system/permissions",
                "form_title": "New Permission",
                "submit_label": "Create Permission",
                "error": str(exc),
                "active_page": "roles",
                "active_menu": "system",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )
    except Exception as exc:
        from app.web.admin import get_sidebar_stats, get_current_user
        return templates.TemplateResponse(
            "admin/system/permissions_form.html",
            {
                "request": request,
                "permission": {
                    "key": key.strip(),
                    "description": description_value or None,
                    "is_active": _form_bool(is_active),
                },
                "action_url": "/admin/system/permissions",
                "form_title": "New Permission",
                "submit_label": "Create Permission",
                "error": str(exc),
                "active_page": "roles",
                "active_menu": "system",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )
    return RedirectResponse(url="/admin/system/permissions", status_code=303)


@router.get("/permissions/{permission_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:permissions:write"))])
def permission_edit(request: Request, permission_id: str, db: Session = Depends(get_db)):
    try:
        permission = rbac_service.permissions.get(db, permission_id)
    except Exception:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "Permission not found"},
            status_code=404,
        )
    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/system/permissions_form.html",
        {
            "request": request,
            "permission": permission,
            "action_url": f"/admin/system/permissions/{permission_id}",
            "form_title": "Edit Permission",
            "submit_label": "Save Changes",
            "active_page": "roles",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post("/permissions/{permission_id}", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:permissions:write"))])
def permission_update(
    request: Request,
    permission_id: str,
    key: str = Form(...),
    description: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    description_value = description.strip() if description else None
    try:
        payload = PermissionUpdate(
            key=key.strip(),
            description=description_value or None,
            is_active=_form_bool(is_active),
        )
        rbac_service.permissions.update(db, permission_id, payload)
    except ValidationError as exc:
        from app.web.admin import get_sidebar_stats, get_current_user
        return templates.TemplateResponse(
            "admin/system/permissions_form.html",
            {
                "request": request,
                "permission": {
                    "id": permission_id,
                    "key": key.strip(),
                    "description": description_value or None,
                    "is_active": _form_bool(is_active),
                },
                "action_url": f"/admin/system/permissions/{permission_id}",
                "form_title": "Edit Permission",
                "submit_label": "Save Changes",
                "error": str(exc),
                "active_page": "roles",
                "active_menu": "system",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )
    except Exception as exc:
        from app.web.admin import get_sidebar_stats, get_current_user
        return templates.TemplateResponse(
            "admin/system/permissions_form.html",
            {
                "request": request,
                "permission": {
                    "id": permission_id,
                    "key": key.strip(),
                    "description": description_value or None,
                    "is_active": _form_bool(is_active),
                },
                "action_url": f"/admin/system/permissions/{permission_id}",
                "form_title": "Edit Permission",
                "submit_label": "Save Changes",
                "error": str(exc),
                "active_page": "roles",
                "active_menu": "system",
                "current_user": get_current_user(request),
                "sidebar_stats": get_sidebar_stats(db),
            },
            status_code=400,
        )
    return RedirectResponse(url="/admin/system/permissions", status_code=303)


@router.post("/permissions/{permission_id}/delete", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:permissions:delete"))])
def permission_delete(
    request: Request, permission_id: str, db: Session = Depends(get_db)
):
    rbac_service.permissions.delete(db, permission_id)
    return RedirectResponse(url="/admin/system/permissions", status_code=303)


@router.get("/api-keys", response_class=HTMLResponse)
def api_keys_list(request: Request, new_key: str | None = None, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user

    current_user = get_current_user(request)
    api_keys = []

    if current_user and current_user.get("person_id"):
        person_id = current_user["person_id"]
        api_keys = db.query(ApiKey).filter(
            ApiKey.person_id == coerce_uuid(person_id)
        ).order_by(ApiKey.created_at.desc()).all()

    context: dict[str, object] = {
        "request": request,
        "active_page": "api-keys",
        "active_menu": "system",
        "current_user": current_user,
        "sidebar_stats": get_sidebar_stats(db),
        "api_keys": api_keys,
        "new_key": new_key,
        "now": datetime.now(timezone.utc),
    }
    return templates.TemplateResponse("admin/system/api_keys.html", context)


@router.get("/api-keys/new", response_class=HTMLResponse)
def api_key_new(request: Request, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user

    context: dict[str, object] = {
        "request": request,
        "active_page": "api-keys",
        "active_menu": "system",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "error": None,
    }
    return templates.TemplateResponse("admin/system/api_key_form.html", context)


@router.post("/api-keys", response_class=HTMLResponse)
def api_key_create(
    request: Request,
    label: str = Form(...),
    expires_in: str = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user
    from datetime import timedelta

    current_user = get_current_user(request)

    if not current_user or not current_user.get("person_id"):
        return RedirectResponse(url="/admin/system/api-keys", status_code=303)

    try:
        # Generate a random API key
        raw_key = secrets.token_urlsafe(32)
        key_hash = hash_password(raw_key)

        # Calculate expiration
        expires_at = None
        if expires_in:
            days = int(expires_in)
            expires_at = datetime.now(timezone.utc) + timedelta(days=days)

        # Create the API key
        api_key = ApiKey(
            person_id=coerce_uuid(current_user["person_id"]),
            label=label,
            key_hash=key_hash,
            is_active=True,
            expires_at=expires_at,
        )
        db.add(api_key)
        db.commit()

        # Return to list with the new key shown
        return RedirectResponse(
            url=f"/admin/system/api-keys?new_key={raw_key}",
            status_code=303
        )
    except Exception as e:
        context: dict[str, object] = {
            "request": request,
            "active_page": "api-keys",
            "active_menu": "system",
            "current_user": current_user,
            "sidebar_stats": get_sidebar_stats(db),
            "error": str(e),
        }
        return templates.TemplateResponse("admin/system/api_key_form.html", context)


@router.post("/api-keys/{key_id}/revoke", response_class=HTMLResponse)
def api_key_revoke(request: Request, key_id: str, db: Session = Depends(get_db)):
    api_key = db.get(ApiKey, coerce_uuid(key_id))
    if api_key:
        api_key.revoked_at = datetime.now(timezone.utc)
        api_key.is_active = False
        db.commit()
    return RedirectResponse(url="/admin/system/api-keys", status_code=303)


@router.get("/webhooks", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:read"))])
def webhooks_list(request: Request, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user
    from datetime import timedelta

    # Get all webhook endpoints
    endpoints = db.query(WebhookEndpoint).order_by(WebhookEndpoint.created_at.desc()).all()
    active_count = sum(1 for e in endpoints if e.is_active)

    # Get delivery stats for last 24 hours
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    deliveries_24h = db.query(WebhookDelivery).filter(
        WebhookDelivery.created_at >= cutoff
    ).all()
    delivery_count_24h = len(deliveries_24h)
    failed_count_24h = sum(1 for d in deliveries_24h if d.status == WebhookDeliveryStatus.failed)

    context = {
        "request": request,
        "active_page": "webhooks",
        "active_menu": "system",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "endpoints": endpoints,
        "active_count": active_count,
        "delivery_count_24h": delivery_count_24h,
        "failed_count_24h": failed_count_24h,
    }
    return templates.TemplateResponse("admin/system/webhooks.html", context)


@router.get("/webhooks/new", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
def webhook_new(request: Request, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user

    context: dict[str, object] = {
        "request": request,
        "active_page": "webhooks",
        "active_menu": "system",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "endpoint": None,
        "subscribed_events": [],
        "action_url": "/admin/system/webhooks",
        "error": None,
    }
    return templates.TemplateResponse("admin/system/webhook_form.html", context)


@router.post("/webhooks", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
def webhook_create(
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    secret: str = Form(None),
    is_active: str = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user

    try:
        # Generate secret if not provided
        if not secret:
            secret = secrets.token_urlsafe(32)

        # Create endpoint
        endpoint = WebhookEndpoint(
            name=name,
            url=url,
            secret=secret,
            is_active=is_active == "true",
        )
        db.add(endpoint)
        db.commit()
        db.refresh(endpoint)

        return RedirectResponse(url="/admin/system/webhooks", status_code=303)
    except Exception as e:
        context: dict[str, object] = {
            "request": request,
            "active_page": "webhooks",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "endpoint": None,
            "subscribed_events": [],
            "action_url": "/admin/system/webhooks",
            "error": str(e),
        }
        return templates.TemplateResponse("admin/system/webhook_form.html", context)


@router.get("/webhooks/{endpoint_id}/edit", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
def webhook_edit(request: Request, endpoint_id: str, db: Session = Depends(get_db)):
    from app.web.admin import get_sidebar_stats, get_current_user

    endpoint = db.get(WebhookEndpoint, coerce_uuid(endpoint_id))
    if not endpoint:
        return RedirectResponse(url="/admin/system/webhooks", status_code=303)

    # Get subscribed events
    subscribed_events = [sub.event_type.value for sub in endpoint.subscriptions if sub.is_active]

    context = {
        "request": request,
        "active_page": "webhooks",
        "active_menu": "system",
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "endpoint": endpoint,
        "subscribed_events": subscribed_events,
        "action_url": f"/admin/system/webhooks/{endpoint_id}",
        "error": None,
    }
    return templates.TemplateResponse("admin/system/webhook_form.html", context)


@router.post("/webhooks/{endpoint_id}", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
def webhook_update(
    request: Request,
    endpoint_id: str,
    name: str = Form(...),
    url: str = Form(...),
    secret: str = Form(None),
    is_active: str = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_sidebar_stats, get_current_user

    endpoint = db.get(WebhookEndpoint, coerce_uuid(endpoint_id))
    if not endpoint:
        return RedirectResponse(url="/admin/system/webhooks", status_code=303)

    try:
        endpoint.name = name
        endpoint.url = url
        if secret:
            endpoint.secret = secret
        endpoint.is_active = is_active == "true"
        db.commit()

        return RedirectResponse(url="/admin/system/webhooks", status_code=303)
    except Exception as e:
        subscribed_events = [sub.event_type.value for sub in endpoint.subscriptions if sub.is_active]
        context = {
            "request": request,
            "active_page": "webhooks",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "endpoint": endpoint,
            "subscribed_events": subscribed_events,
            "action_url": f"/admin/system/webhooks/{endpoint_id}",
            "error": str(e),
        }
        return templates.TemplateResponse("admin/system/webhook_form.html", context)


@router.get("/audit", response_class=HTMLResponse, dependencies=[Depends(require_permission("audit:read"))])
def audit_log(
    request: Request,
    actor_id: Optional[str] = None,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=100),
    db: Session = Depends(get_db),
):
    """View audit log."""
    offset = (page - 1) * per_page

    actor_id_value: str | None = None
    if actor_id:
        try:
            actor_id_value = str(UUID(actor_id))
        except ValueError:
            actor_id_value = None

    events = audit_service.audit_events.list(
        db=db,
        actor_id=actor_id_value,
        actor_type=None,
        action=action if action else None,
        entity_type=entity_type if entity_type else None,
        entity_id=None,
        request_id=None,
        is_success=None,
        status_code=None,
        is_active=None,
        order_by="occurred_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )

    from app.models.person import Person
    from app.services.audit_helpers import (
        extract_changes,
        format_audit_datetime,
        format_changes,
        humanize_action,
        humanize_entity,
    )

    from app.models.audit import AuditActorType

    def _is_user_actor(actor_type) -> bool:
        return actor_type in {AuditActorType.user, AuditActorType.user.value, "user"}

    actor_ids = {
        event.actor_id
        for event in events
        if event.actor_id and _is_user_actor(getattr(event, "actor_type", None))
    }
    people = {}
    if actor_ids:
        try:
            people = {
                str(person.id): person
                for person in db.query(Person).filter(Person.id.in_(actor_ids)).all()
            }
        except Exception:
            people = {}

    event_views = []
    for event in events:
        actor_name = None
        is_user_actor = _is_user_actor(getattr(event, "actor_type", None))
        if event.actor_id and is_user_actor:
            actor = people.get(str(event.actor_id))
            if actor:
                actor_name = (
                    actor.display_name
                    or f"{actor.first_name} {actor.last_name}".strip()
                    or actor.email
                )
        if not actor_name:
            metadata = getattr(event, "metadata_", None) or {}
            if is_user_actor:
                actor_name = metadata.get("actor_email") or event.actor_id or "User"
            else:
                actor_name = (
                    metadata.get("actor_name")
                    or metadata.get("actor_email")
                    or event.actor_id
                    or "System"
                )
        metadata = getattr(event, "metadata_", None) or {}
        changes = extract_changes(metadata, getattr(event, "action", None))
        change_summary = format_changes(changes)
        action_label = humanize_action(event.action)
        entity_label = humanize_entity(event.entity_type, event.entity_id)
        event_views.append(
            {
                "occurred_at": event.occurred_at,
                "occurred_at_display": format_audit_datetime(
                    event.occurred_at, "%b %d, %Y %H:%M"
                ),
                "actor_name": actor_name,
                "actor_id": event.actor_id,
                "action": event.action,
                "action_label": action_label,
                "action_detail": change_summary,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "entity_label": entity_label,
                "is_success": event.is_success,
                "status_code": event.status_code,
            }
        )

    all_events = audit_service.audit_events.list(
        db=db,
        actor_id=actor_id_value,
        actor_type=None,
        action=action if action else None,
        entity_type=entity_type if entity_type else None,
        entity_id=None,
        request_id=None,
        is_success=None,
        status_code=None,
        is_active=None,
        order_by="occurred_at",
        order_dir="desc",
        limit=10000,
        offset=0,
    )
    total = len(all_events)
    total_pages = (total + per_page - 1) // per_page

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "admin/system/_audit_table.html",
            {
                "request": request,
                "events": event_views,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
            },
        )

    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/system/audit.html",
        {
            "request": request,
            "events": event_views,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "actor_id": actor_id,
            "action": action,
            "entity_type": entity_type,
            "active_page": "audit",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get("/scheduler", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:read"))])
def scheduler_overview(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    db: Session = Depends(get_db),
):
    """View scheduled tasks."""
    offset = (page - 1) * per_page

    tasks = scheduler_service.scheduled_tasks.list(
        db=db,
        enabled=None,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )

    all_tasks = scheduler_service.scheduled_tasks.list(
        db=db,
        enabled=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
    )
    total = len(all_tasks)
    total_pages = (total + per_page - 1) // per_page

    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/system/scheduler.html",
        {
            "request": request,
            "tasks": tasks,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "active_page": "scheduler",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.get("/scheduler/{task_id}", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:read"))])
def scheduler_task_detail(request: Request, task_id: str, db: Session = Depends(get_db)):
    """View scheduled task details."""
    from app.web.admin import get_sidebar_stats, get_current_user

    task = scheduler_service.scheduled_tasks.get(db, task_id)
    if not task:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "Scheduled task not found"},
            status_code=404,
        )

    # Calculate next run time
    next_run = None
    if task.enabled and task.last_run_at:
        from datetime import timedelta
        next_run = task.last_run_at + timedelta(seconds=task.interval_seconds)

    return templates.TemplateResponse(
        "admin/system/scheduler_detail.html",
        {
            "request": request,
            "task": task,
            "next_run": next_run,
            "runs": [],  # Task run history would come from a task_runs table
            "active_page": "scheduler",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
        },
    )


@router.post("/scheduler/{task_id}/enable", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
def scheduler_task_enable(request: Request, task_id: str, db: Session = Depends(get_db)):
    """Enable a scheduled task."""
    from app.schemas.scheduler import ScheduledTaskUpdate
    scheduler_service.scheduled_tasks.update(db, task_id, ScheduledTaskUpdate(enabled=True))
    return RedirectResponse(url=f"/admin/system/scheduler/{task_id}", status_code=303)


@router.post("/scheduler/{task_id}/disable", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
def scheduler_task_disable(request: Request, task_id: str, db: Session = Depends(get_db)):
    """Disable a scheduled task."""
    from app.schemas.scheduler import ScheduledTaskUpdate
    scheduler_service.scheduled_tasks.update(db, task_id, ScheduledTaskUpdate(enabled=False))
    return RedirectResponse(url=f"/admin/system/scheduler/{task_id}", status_code=303)


@router.post("/scheduler/{task_id}/run", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
def scheduler_task_run(request: Request, task_id: str, db: Session = Depends(get_db)):
    """Manually trigger a scheduled task."""
    task = scheduler_service.scheduled_tasks.get(db, task_id)
    scheduler_service.enqueue_task(task.task_name, task.args_json, task.kwargs_json)
    task.last_run_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(url=f"/admin/system/scheduler/{task_id}", status_code=303)


@router.post("/scheduler/{task_id}/delete", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
def scheduler_task_delete(request: Request, task_id: str, db: Session = Depends(get_db)):
    """Delete a scheduled task."""
    scheduler_service.scheduled_tasks.delete(db, task_id)
    return RedirectResponse(url="/admin/system/scheduler", status_code=303)


@router.get("/workflow", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:read"))])
def workflow_overview(request: Request, db: Session = Depends(get_db)):
    """Workflow and SLA configuration overview."""
    context = _workflow_context(request, db)
    return templates.TemplateResponse("admin/system/workflow.html", context)


@router.post("/workflow/policies", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
async def workflow_policy_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        entity_type_raw = _form_str(form.get("entity_type")).strip()
        if not entity_type_raw:
            raise ValueError("Entity type is required.")
        payload = SlaPolicyCreate(
            name=_form_str(form.get("name")).strip(),
            entity_type=WorkflowEntityType(entity_type_raw),
            description=_form_str_opt(form.get("description")),
            is_active=_form_bool(form.get("is_active")),
        )
        workflow_service.sla_policies.create(db=db, payload=payload)
        return RedirectResponse(url="/admin/system/workflow", status_code=303)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _workflow_context(request, db, error or "Unable to create policy.")
        return templates.TemplateResponse("admin/system/workflow.html", context, status_code=400)


@router.post("/workflow/targets", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
async def workflow_target_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        target_minutes_raw = _form_str(form.get("target_minutes")).strip() or "0"
        target_minutes = int(target_minutes_raw)
        warning_raw = _form_str(form.get("warning_minutes")).strip()
        warning_minutes = int(warning_raw) if warning_raw else None
        policy_id_raw = _form_str(form.get("policy_id")).strip()
        if not policy_id_raw:
            raise ValueError("Policy is required.")
        payload = SlaTargetCreate(
            policy_id=coerce_uuid(policy_id_raw),
            priority=_form_str_opt(form.get("priority")),
            target_minutes=target_minutes,
            warning_minutes=warning_minutes,
            is_active=_form_bool(form.get("is_active")),
        )
        workflow_service.sla_targets.create(db=db, payload=payload)
        return RedirectResponse(url="/admin/system/workflow", status_code=303)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _workflow_context(request, db, error or "Unable to create SLA target.")
        return templates.TemplateResponse("admin/system/workflow.html", context, status_code=400)


@router.post("/workflow/transitions/ticket", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
async def workflow_ticket_transition_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        payload = TicketStatusTransitionCreate(
            from_status=_form_str(form.get("from_status")).strip(),
            to_status=_form_str(form.get("to_status")).strip(),
            requires_note=_form_bool(form.get("requires_note")),
            is_active=_form_bool(form.get("is_active")),
        )
        workflow_service.ticket_transitions.create(db=db, payload=payload)
        return RedirectResponse(url="/admin/system/workflow", status_code=303)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _workflow_context(request, db, error or "Unable to create ticket transition.")
        return templates.TemplateResponse("admin/system/workflow.html", context, status_code=400)


@router.post("/workflow/transitions/work-order", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
async def workflow_work_order_transition_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        payload = WorkOrderStatusTransitionCreate(
            from_status=_form_str(form.get("from_status")).strip(),
            to_status=_form_str(form.get("to_status")).strip(),
            requires_note=_form_bool(form.get("requires_note")),
            is_active=_form_bool(form.get("is_active")),
        )
        workflow_service.work_order_transitions.create(db=db, payload=payload)
        return RedirectResponse(url="/admin/system/workflow", status_code=303)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _workflow_context(request, db, error or "Unable to create work order transition.")
        return templates.TemplateResponse("admin/system/workflow.html", context, status_code=400)


@router.post("/workflow/transitions/project-task", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
async def workflow_project_task_transition_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        payload = ProjectTaskStatusTransitionCreate(
            from_status=_form_str(form.get("from_status")).strip(),
            to_status=_form_str(form.get("to_status")).strip(),
            requires_note=_form_bool(form.get("requires_note")),
            is_active=_form_bool(form.get("is_active")),
        )
        workflow_service.project_task_transitions.create(db=db, payload=payload)
        return RedirectResponse(url="/admin/system/workflow", status_code=303)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)
        context = _workflow_context(request, db, error or "Unable to create project task transition.")
        return templates.TemplateResponse("admin/system/workflow.html", context, status_code=400)


@router.get("/settings", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:read"))])
def settings_overview(
    request: Request,
    domain: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """System settings management."""
    from app.csrf import get_csrf_token
    from app.web.admin import get_sidebar_stats, get_current_user
    settings_context = _build_settings_context(db, domain)
    base_url = str(request.base_url).rstrip("/")
    crm_meta_callback_url = base_url + "/webhooks/crm/meta"
    crm_meta_oauth_redirect_url = base_url + "/admin/crm/meta/callback"
    saved = _form_bool(request.query_params.get("saved"))
    return templates.TemplateResponse(
        "admin/system/settings.html",
        {
            "request": request,
            **settings_context,
            "crm_meta_callback_url": crm_meta_callback_url,
            "crm_meta_oauth_redirect_url": crm_meta_oauth_redirect_url,
            "active_page": "settings",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "saved": saved,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.get(
    "/settings/domain-numbering",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:settings:read"))],
)
def settings_numbering(request: Request, db: Session = Depends(get_db)):
    from app.csrf import get_csrf_token
    from app.web.admin import get_sidebar_stats, get_current_user

    settings_context = _build_settings_context(db, "numbering")
    base_url = str(request.base_url).rstrip("/")
    crm_meta_callback_url = base_url + "/webhooks/crm/meta"
    crm_meta_oauth_redirect_url = base_url + "/admin/crm/meta/callback"
    saved = _form_bool(request.query_params.get("saved"))
    return templates.TemplateResponse(
        "admin/system/settings.html",
        {
            "request": request,
            **settings_context,
            "crm_meta_callback_url": crm_meta_callback_url,
            "crm_meta_oauth_redirect_url": crm_meta_oauth_redirect_url,
            "active_page": "settings",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "saved": saved,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post("/settings", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
async def settings_update(
    request: Request,
    domain: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Update system settings for a domain."""
    form = await request.form()
    domain_value = domain or _form_str_opt(form.get("domain"))
    errors: list[str] = []
    SettingValue = dict[str, object] | list[object] | bool | int | str | None
    if domain_value == ENFORCEMENT_DOMAIN:
        specs = _enforcement_specs()
        for spec in specs:
            service = settings_spec.DOMAIN_SETTINGS_SERVICE.get(spec.domain)
            if not service:
                errors.append(f"{spec.key}: Settings service not configured.")
                continue
            raw = form.get(spec.key)
            raw_value = _form_str_opt(raw)
            if spec.is_secret and not raw_value:
                continue
            value: SettingValue
            if spec.value_type == settings_spec.SettingValueType.boolean:
                value = _form_bool(raw_value)
            elif spec.value_type == settings_spec.SettingValueType.integer:
                if not raw_value:
                    value = cast(SettingValue, spec.default)
                else:
                    try:
                        value = int(raw_value)
                    except ValueError:
                        errors.append(f"{spec.key}: Value must be an integer.")
                        continue
            else:
                if not raw_value:
                    if spec.value_type == settings_spec.SettingValueType.string:
                        value = cast(SettingValue, spec.default) if spec.default is not None else ""
                    elif spec.value_type == settings_spec.SettingValueType.json:
                        value = cast(SettingValue, spec.default) if spec.default is not None else {}
                    else:
                        value = cast(SettingValue, spec.default)
                else:
                    if spec.value_type == settings_spec.SettingValueType.json:
                        try:
                            value = json.loads(raw_value)
                        except json.JSONDecodeError:
                            errors.append(f"{spec.key}: Value must be valid JSON.")
                            continue
                    else:
                        value = raw_value
            if spec.allowed and value is not None and value not in spec.allowed:
                errors.append(f"{spec.key}: Value must be one of {', '.join(sorted(spec.allowed))}.")
                continue
            if isinstance(value, int):
                if spec.min_value is not None and value < spec.min_value:
                    errors.append(f"{spec.key}: Minimum value is {spec.min_value}.")
                    continue
                if spec.max_value is not None and value > spec.max_value:
                    errors.append(f"{spec.key}: Maximum value is {spec.max_value}.")
                    continue
            if value is None:
                value_text, value_json = None, None
            else:
                value_text, value_json_raw = settings_spec.normalize_for_db(spec, value)
                value_json = cast(SettingValue, value_json_raw)
            payload = DomainSettingUpdate(
                value_type=spec.value_type,
                value_text=value_text,
                value_json=value_json,
                is_secret=spec.is_secret,
                is_active=True,
            )
            service.upsert_by_key(db, spec.key, payload)
        settings_context = _build_settings_context(db, ENFORCEMENT_DOMAIN)
    else:
        selected_domain = _resolve_settings_domain(domain_value)
        if selected_domain == SettingDomain.campaigns:
            settings_context = _build_settings_context(db, selected_domain.value)
            base_url = str(request.base_url).rstrip("/")
            crm_meta_callback_url = base_url + "/webhooks/crm/meta"
            crm_meta_oauth_redirect_url = base_url + "/admin/crm/meta/callback"
            from app.csrf import get_csrf_token
            from app.web.admin import get_sidebar_stats, get_current_user
            return templates.TemplateResponse(
                "admin/system/settings.html",
                {
                    "request": request,
                    **settings_context,
                    "crm_meta_callback_url": crm_meta_callback_url,
                    "crm_meta_oauth_redirect_url": crm_meta_oauth_redirect_url,
                    "active_page": "settings",
                    "active_menu": "system",
                    "current_user": get_current_user(request),
                    "sidebar_stats": get_sidebar_stats(db),
                    "errors": ["Campaign sender settings are managed below."],
                    "saved": False,
                    "csrf_token": get_csrf_token(request),
                },
            )
        specs = settings_spec.list_specs(selected_domain)
        service = settings_spec.DOMAIN_SETTINGS_SERVICE.get(selected_domain)
        if not service:
            errors.append("Settings service not configured for this domain.")
        else:
            overrides: dict[str, SettingValue] = {}
            if selected_domain == SettingDomain.comms:
                overrides["ticket_types"] = cast(
                    SettingValue, _extract_ticket_types_from_form(form)
                )
            if selected_domain == SettingDomain.projects:
                overrides["region_pm_assignments"] = cast(
                    SettingValue, _extract_region_pm_assignments_from_form(form)
                )
            for spec in specs:
                if spec.key in overrides:
                    raw_value = None
                    value_setting = overrides[spec.key]
                else:
                    raw = form.get(spec.key)
                    raw_value = _form_str_opt(raw)
                if spec.is_secret and not raw_value:
                    continue
                if spec.key not in overrides:
                    if spec.value_type == settings_spec.SettingValueType.boolean:
                        value_setting = _form_bool(raw_value)
                    elif spec.value_type == settings_spec.SettingValueType.integer:
                        if not raw_value:
                            value_setting = cast(SettingValue, spec.default)
                        else:
                            try:
                                value_setting = int(raw_value)
                            except ValueError:
                                errors.append(f"{spec.key}: Value must be an integer.")
                                continue
                    else:
                        if not raw_value:
                            if spec.value_type == settings_spec.SettingValueType.string:
                                value_setting = (
                                    cast(SettingValue, spec.default) if spec.default is not None else ""
                                )
                            elif spec.value_type == settings_spec.SettingValueType.json:
                                value_setting = (
                                    cast(SettingValue, spec.default) if spec.default is not None else {}
                                )
                            else:
                                value_setting = cast(SettingValue, spec.default)
                        else:
                            if spec.value_type == settings_spec.SettingValueType.json:
                                try:
                                    value_setting = json.loads(raw_value)
                                except json.JSONDecodeError:
                                    errors.append(f"{spec.key}: Value must be valid JSON.")
                                    continue
                            else:
                                value_setting = raw_value
                if spec.allowed and value_setting is not None and value_setting not in spec.allowed:
                    errors.append(f"{spec.key}: Value must be one of {', '.join(sorted(spec.allowed))}.")
                    continue
                if isinstance(value_setting, int):
                    if spec.min_value is not None and value_setting < spec.min_value:
                        errors.append(f"{spec.key}: Minimum value is {spec.min_value}.")
                        continue
                    if spec.max_value is not None and value_setting > spec.max_value:
                        errors.append(f"{spec.key}: Maximum value is {spec.max_value}.")
                        continue
                if value_setting is None:
                    value_text, value_json = None, None
                else:
                    value_text, value_json_raw = settings_spec.normalize_for_db(
                        spec, value_setting
                    )
                    value_json = cast(SettingValue, value_json_raw)
                payload = DomainSettingUpdate(
                    value_type=spec.value_type,
                    value_text=value_text,
                    value_json=value_json,
                    is_secret=spec.is_secret,
                    is_active=True,
                )
                service.upsert_by_key(db, spec.key, payload)

        settings_context = _build_settings_context(db, selected_domain.value)
        if not errors and selected_domain == SettingDomain.numbering:
            from app.services.numbering import backfill_number_prefixes
            backfill_number_prefixes(db)
    base_url = str(request.base_url).rstrip("/")
    crm_meta_callback_url = base_url + "/webhooks/crm/meta"
    crm_meta_oauth_redirect_url = base_url + "/admin/crm/meta/callback"
    from app.csrf import get_csrf_token
    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/system/settings.html",
        {
            "request": request,
            **settings_context,
            "crm_meta_callback_url": crm_meta_callback_url,
            "crm_meta_oauth_redirect_url": crm_meta_oauth_redirect_url,
            "active_page": "settings",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "errors": errors,
            "saved": not errors,
            "csrf_token": get_csrf_token(request),
        },
    )


def _render_campaign_settings(
    request: Request,
    db: Session,
    errors: list[str] | None = None,
    saved: bool = False,
) -> HTMLResponse:
    from app.csrf import get_csrf_token
    settings_context = _build_settings_context(db, SettingDomain.campaigns.value)
    base_url = str(request.base_url).rstrip("/")
    crm_meta_callback_url = base_url + "/webhooks/crm/meta"
    crm_meta_oauth_redirect_url = base_url + "/admin/crm/meta/callback"
    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/system/settings.html",
        {
            "request": request,
            **settings_context,
            "crm_meta_callback_url": crm_meta_callback_url,
            "crm_meta_oauth_redirect_url": crm_meta_oauth_redirect_url,
            "active_page": "settings",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "errors": errors or [],
            "saved": saved,
            "csrf_token": get_csrf_token(request),
        },
    )


@router.post(
    "/settings/campaign-senders",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:settings:write"))],
)
async def campaign_sender_create(
    request: Request,
    name: str = Form(""),
    from_name: str = Form(""),
    from_email: str = Form(""),
    reply_to: str = Form(""),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    errors: list[str] = []
    try:
        payload = CampaignSenderCreate(
            name=name.strip(),
            from_name=from_name.strip() or None,
            from_email=from_email.strip(),
            reply_to=reply_to.strip() or None,
            is_active=str(is_active).lower() in {"1", "true", "yes", "on"},
        )
    except ValidationError as exc:
        errors.extend([err.get("msg", "Invalid value") for err in exc.errors()])
        return _render_campaign_settings(request, db, errors=errors, saved=False)

    try:
        campaign_senders.create(db, payload)
    except HTTPException as exc:
        return _render_campaign_settings(request, db, errors=[str(exc.detail)], saved=False)

    return RedirectResponse(url="/admin/system/settings?domain=campaigns&saved=1", status_code=303)


@router.post(
    "/settings/campaign-senders/{sender_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:settings:write"))],
)
async def campaign_sender_update(
    request: Request,
    sender_id: str,
    name: str = Form(""),
    from_name: str = Form(""),
    from_email: str = Form(""),
    reply_to: str = Form(""),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    errors: list[str] = []
    try:
        payload = CampaignSenderUpdate(
            name=name.strip(),
            from_name=from_name.strip() or None,
            from_email=from_email.strip(),
            reply_to=reply_to.strip() or None,
            is_active=str(is_active).lower() in {"1", "true", "yes", "on"},
        )
    except ValidationError as exc:
        errors.extend([err.get("msg", "Invalid value") for err in exc.errors()])
        return _render_campaign_settings(request, db, errors=errors, saved=False)

    try:
        campaign_senders.update(db, sender_id, payload)
    except HTTPException as exc:
        return _render_campaign_settings(request, db, errors=[str(exc.detail)], saved=False)

    return RedirectResponse(url="/admin/system/settings?domain=campaigns&saved=1", status_code=303)


@router.post(
    "/settings/campaign-senders/{sender_id}/delete",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:settings:write"))],
)
async def campaign_sender_delete(
    request: Request,
    sender_id: str,
    db: Session = Depends(get_db),
):
    try:
        campaign_senders.deactivate(db, sender_id)
    except HTTPException as exc:
        return _render_campaign_settings(request, db, errors=[str(exc.detail)], saved=False)
    return RedirectResponse(url="/admin/system/settings?domain=campaigns", status_code=303)


@router.post(
    "/settings/campaign-smtp",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:settings:write"))],
)
async def campaign_smtp_create(
    request: Request,
    name: str = Form(""),
    host: str = Form(""),
    port: int = Form(587),
    username: str = Form(""),
    password: str = Form(""),
    use_tls: str | None = Form(None),
    use_ssl: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    errors: list[str] = []
    try:
        payload = CampaignSmtpCreate(
            name=name.strip(),
            host=host.strip(),
            port=port,
            username=username.strip() or None,
            password=password.strip() or None,
            use_tls=str(use_tls).lower() in {"1", "true", "yes", "on"},
            use_ssl=str(use_ssl).lower() in {"1", "true", "yes", "on"},
            is_active=str(is_active).lower() in {"1", "true", "yes", "on"},
        )
    except ValidationError as exc:
        errors.extend([err.get("msg", "Invalid value") for err in exc.errors()])
        return _render_campaign_settings(request, db, errors=errors, saved=False)

    try:
        ok, error = email_service.test_smtp_connection(payload.model_dump(), db=db)
        if not ok:
            return _render_campaign_settings(
                request,
                db,
                errors=[error or "SMTP test failed"],
                saved=False,
            )
        campaign_smtp_configs.create(db, payload)
    except HTTPException as exc:
        return _render_campaign_settings(request, db, errors=[str(exc.detail)], saved=False)

    return RedirectResponse(url="/admin/system/settings?domain=campaigns", status_code=303)


@router.get(
    "/settings/campaign-smtp",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:settings:read"))],
)
async def campaign_smtp_get_redirect() -> RedirectResponse:
    return RedirectResponse(url="/admin/system/settings?domain=campaigns", status_code=302)


@router.post(
    "/settings/campaign-smtp/{smtp_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:settings:write"))],
)
async def campaign_smtp_update(
    request: Request,
    smtp_id: str,
    name: str = Form(""),
    host: str = Form(""),
    port: int = Form(587),
    username: str = Form(""),
    password: str = Form(""),
    use_tls: str | None = Form(None),
    use_ssl: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    errors: list[str] = []
    try:
        password_value = password.strip() if password else ""
        password_provided = bool(password_value)
        username_value = username.strip() if username else ""
        update_data: dict[str, Any] = {
            "name": name.strip(),
            "host": host.strip(),
            "port": port,
            "username": username_value or None,
            "use_tls": str(use_tls).lower() in {"1", "true", "yes", "on"},
            "use_ssl": str(use_ssl).lower() in {"1", "true", "yes", "on"},
            "is_active": str(is_active).lower() in {"1", "true", "yes", "on"},
        }
        if password_provided:
            update_data["password"] = password_value
        payload = CampaignSmtpUpdate(**update_data)
    except ValidationError as exc:
        errors.extend([err.get("msg", "Invalid value") for err in exc.errors()])
        return _render_campaign_settings(request, db, errors=errors, saved=False)

    try:
        smtp = campaign_smtp_configs.get(db, smtp_id)
        effective_username = payload.username if payload.username is not None else smtp.username
        if effective_username is None:
            effective_password = None
        else:
            effective_password = (
                payload.password if payload.password is not None else smtp.password
            )
        smtp_test_config = {
            "host": payload.host if payload.host is not None else smtp.host,
            "port": payload.port if payload.port is not None else smtp.port,
            "use_tls": payload.use_tls if payload.use_tls is not None else smtp.use_tls,
            "use_ssl": payload.use_ssl if payload.use_ssl is not None else smtp.use_ssl,
            "username": effective_username,
            "password": effective_password,
        }
        ok, error = email_service.test_smtp_connection(smtp_test_config, db=db)
        if not ok:
            return _render_campaign_settings(
                request,
                db,
                errors=[error or "SMTP test failed"],
                saved=False,
            )
        campaign_smtp_configs.update(db, smtp_id, payload)
    except HTTPException as exc:
        return _render_campaign_settings(request, db, errors=[str(exc.detail)], saved=False)

    return RedirectResponse(url="/admin/system/settings?domain=campaigns", status_code=303)


@router.post(
    "/settings/campaign-smtp/{smtp_id}/delete",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("system:settings:write"))],
)
async def campaign_smtp_delete(
    request: Request,
    smtp_id: str,
    db: Session = Depends(get_db),
):
    try:
        campaign_smtp_configs.deactivate(db, smtp_id)
    except HTTPException as exc:
        return _render_campaign_settings(request, db, errors=[str(exc.detail)], saved=False)
    return RedirectResponse(url="/admin/system/settings?domain=campaigns", status_code=303)


@router.post("/settings/branding", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:write"))])
async def settings_branding_update(
    request: Request,
    logo: UploadFile | None = File(None),
    favicon: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    """Upload branding assets (logo/favicon) and store their URLs in settings."""
    errors: list[str] = []
    if logo and not logo.filename:
        logo = None
    if favicon and not favicon.filename:
        favicon = None
    service = settings_spec.DOMAIN_SETTINGS_SERVICE.get(SettingDomain.comms)
    if not service:
        errors.append("Settings service not configured for branding.")
    if not logo and not favicon:
        errors.append("Please upload a logo and/or favicon.")

    try:
        if service and logo:
            previous_logo = settings_spec.resolve_value(db, SettingDomain.comms, "brand_logo_url")
            logo_url = await branding_assets.save_branding_asset(logo, "logo", previous_url=cast(str | None, previous_logo))
            payload = DomainSettingUpdate(
                value_type=settings_spec.SettingValueType.string,
                value_text=logo_url,
                value_json=None,
                is_secret=False,
                is_active=True,
            )
            service.upsert_by_key(db, "brand_logo_url", payload)
        if service and favicon:
            previous_favicon = settings_spec.resolve_value(db, SettingDomain.comms, "brand_favicon_url")
            favicon_url = await branding_assets.save_branding_asset(favicon, "favicon", previous_url=cast(str | None, previous_favicon))
            payload = DomainSettingUpdate(
                value_type=settings_spec.SettingValueType.string,
                value_text=favicon_url,
                value_json=None,
                is_secret=False,
                is_active=True,
            )
            service.upsert_by_key(db, "brand_favicon_url", payload)
    except HTTPException as exc:
        errors.append(str(exc.detail))
    except Exception as exc:
        errors.append(str(exc) or "Failed to upload branding assets.")

    settings_context = _build_settings_context(db, SettingDomain.comms.value)
    base_url = str(request.base_url).rstrip("/")
    crm_meta_callback_url = base_url + "/webhooks/crm/meta"
    crm_meta_oauth_redirect_url = base_url + "/admin/crm/meta/callback"
    from app.web.admin import get_sidebar_stats, get_current_user
    return templates.TemplateResponse(
        "admin/system/settings.html",
        {
            "request": request,
            **settings_context,
            "crm_meta_callback_url": crm_meta_callback_url,
            "crm_meta_oauth_redirect_url": crm_meta_oauth_redirect_url,
            "active_page": "settings",
            "active_menu": "system",
            "current_user": get_current_user(request),
            "sidebar_stats": get_sidebar_stats(db),
            "errors": errors,
            "saved": not errors,
        },
    )


@router.post("/settings/test-smtp", response_class=HTMLResponse, dependencies=[Depends(require_permission("system:settings:read"))])
def test_smtp_connection(request: Request, db: Session = Depends(get_db)):
    """Test SMTP connection (HTMX endpoint)."""
    from app.services.email import test_smtp_connection as smtp_test, _get_smtp_config

    config = _get_smtp_config(db)
    success, error = smtp_test(config, db=db)

    if success:
        return HTMLResponse(
            '<div class="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700 dark:border-green-800 dark:bg-green-900/30 dark:text-green-400">'
            '<div class="flex items-center gap-2">'
            '<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>'
            '<span>SMTP connection successful!</span>'
            '</div>'
            '</div>',
            status_code=200,
        )
    else:
        error_msg = error or "Unknown error"
        return HTMLResponse(
            f'<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            f'<div class="flex items-center gap-2">'
            f'<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>'
            f'<span>SMTP test failed: {error_msg}</span>'
            f'</div>'
            f'</div>',
            status_code=200,
        )


@router.post("/users/{user_id}/resend-invite", response_class=HTMLResponse, dependencies=[Depends(require_permission("rbac:assign"))])
def resend_user_invitation(request: Request, user_id: str, db: Session = Depends(get_db)):
    """Resend invitation email to a pending user (HTMX endpoint)."""
    person = person_service.people.get(db, user_id)
    if not person:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            'User not found.'
            '</div>',
            status_code=404,
        )

    # Get user's credential
    credential = (
        db.query(UserCredential)
        .filter(UserCredential.person_id == person.id)
        .filter(UserCredential.is_active.is_(True))
        .first()
    )

    if not credential:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            'No active credentials found for this user.'
            '</div>',
            status_code=400,
        )

    if not credential.must_change_password:
        return HTMLResponse(
            '<div class="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-400">'
            'This user has already set their password.'
            '</div>',
            status_code=400,
        )

    if not person.email:
        return HTMLResponse(
            '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            'User has no email address configured.'
            '</div>',
            status_code=400,
        )

    # Generate a new password reset token and send invitation email
    try:
        reset_payload = auth_flow_service.request_password_reset(db, person.email)
        if not reset_payload:
            return HTMLResponse(
                '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
                'Failed to generate invitation token.'
                '</div>',
                status_code=500,
            )

        reset_token = reset_payload.get("token")
        if not reset_token:
            return HTMLResponse(
                '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
                'Failed to generate invitation token.'
                '</div>',
                status_code=500,
            )

        person_name = person.display_name or f"{person.first_name or ''} {person.last_name or ''}".strip() or None
        success = email_service.send_user_invite_email(db, person.email, reset_token, person_name)

        if success:
            return HTMLResponse(
                '<div class="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700 dark:border-green-800 dark:bg-green-900/30 dark:text-green-400">'
                '<div class="flex items-center gap-2">'
                '<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>'
                f'<span>Invitation sent to {person.email}</span>'
                '</div>'
                '</div>',
                status_code=200,
            )
        else:
            return HTMLResponse(
                '<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
                'Failed to send invitation email. Check SMTP settings.'
                '</div>',
                status_code=500,
            )
    except Exception as exc:
        return HTMLResponse(
            f'<div class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-900/30 dark:text-red-400">'
            f'Error: {str(exc)}'
            f'</div>',
            status_code=500,
        )

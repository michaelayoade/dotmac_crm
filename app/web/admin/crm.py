"""CRM web routes - Omni-channel Inbox."""

import uuid
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.csrf import get_csrf_token
from app.db import SessionLocal
from app.logging import get_logger
from app.models.projects import ProjectType
from app.models.subscriber import Organization, Subscriber
from app.services import person as person_service
from app.services.common import coerce_uuid
from app.services.crm.inbox.page_context import build_inbox_page_context
from app.services.subscriber import subscriber as subscriber_service
from app.web.admin.crm_contacts import router as crm_contacts_router
from app.web.admin.crm_inbox_actions_core import router as crm_inbox_actions_core_router
from app.web.admin.crm_inbox_catalog import router as crm_inbox_catalog_router
from app.web.admin.crm_inbox_comment_reply import router as crm_inbox_comment_reply_router
from app.web.admin.crm_inbox_comments import router as crm_inbox_comments_router
from app.web.admin.crm_inbox_connectors_actions import router as crm_inbox_connectors_actions_router
from app.web.admin.crm_inbox_conversations import router as crm_inbox_conversations_router
from app.web.admin.crm_inbox_message import router as crm_inbox_message_router
from app.web.admin.crm_inbox_private_notes import router as crm_inbox_private_notes_router
from app.web.admin.crm_inbox_settings import router as crm_inbox_settings_router
from app.web.admin.crm_inbox_start import router as crm_inbox_start_router
from app.web.admin.crm_inbox_status import router as crm_inbox_status_router
from app.web.admin.crm_leads import router as crm_leads_router
from app.web.admin.crm_presence import router as crm_presence_router
from app.web.admin.crm_quotes import router as crm_quotes_router
from app.web.admin.crm_sales import router as crm_sales_router
from app.web.admin.crm_widget import router as crm_widget_router
from app.web.templates import Jinja2Templates

_DEFAULT_PIPELINE_STAGES = [
    {"name": "Lead Identified", "probability": 10},
    {"name": "Qualification Call Completed", "probability": 20},
    {"name": "Needs Assessment / Demo", "probability": 35},
    {"name": "Proposal Sent", "probability": 50},
    {"name": "Commercial Negotiation", "probability": 70},
    {"name": "Decision Pending", "probability": 85},
    {"name": "Closed Won", "probability": 100},
    {"name": "Closed Lost", "probability": 0},
]

templates = Jinja2Templates(directory="templates")
logger = get_logger(__name__)


def _select_subscriber_by_id(db: Session, subscriber_id: str) -> Subscriber | None:
    try:
        return subscriber_service.get(db, coerce_uuid(subscriber_id))
    except Exception:
        return None


def _infer_project_type_from_quote_items(items: list) -> str | None:
    if not items:
        return None
    for item in items:
        desc = (getattr(item, "description", "") or "").lower()
        if "air fiber installation" in desc:
            return ProjectType.air_fiber_installation.value
    for item in items:
        desc = (getattr(item, "description", "") or "").lower()
        if "fiber optics installation" in desc:
            return ProjectType.fiber_optics_installation.value
    return None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


router = APIRouter(prefix="/crm", tags=["web-admin-crm"])


def _as_bool(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value.strip()) if isinstance(value, str) else int(value)
    except ValueError:
        return default


def _as_str(value: object | None) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _form_str(form, key: str, default: str = "") -> str:
    value = form.get(key)
    return value.strip() if isinstance(value, str) else default


def _form_str_opt(form, key: str) -> str | None:
    value = form.get(key)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _coerce_bubble_position(
    value: str | None,
) -> Literal["bottom-right", "bottom-left"]:
    if value == "bottom-left":
        return "bottom-left"
    return "bottom-right"


def _coerce_uuid_optional(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    return coerce_uuid(value)


def _as_list(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_decimal(value: str | None, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except Exception as exc:
        raise ValueError(f"Invalid {field}") from exc


def _collect_quote_item_inputs(
    descriptions: list[str] | None,
    quantities: list[str] | None,
    unit_prices: list[str] | None,
    inventory_item_ids: list[str] | None,
) -> list[dict]:
    descriptions = descriptions or []
    quantities = quantities or []
    unit_prices = unit_prices or []
    inventory_item_ids = inventory_item_ids or []
    max_len = max(
        len(descriptions),
        len(quantities),
        len(unit_prices),
        len(inventory_item_ids),
        0,
    )
    items: list[dict] = []
    for idx in range(max_len):
        desc = (descriptions[idx] if idx < len(descriptions) else "").strip()
        qty = (quantities[idx] if idx < len(quantities) else "").strip()
        price = (unit_prices[idx] if idx < len(unit_prices) else "").strip()
        inventory_item_id = (inventory_item_ids[idx] if idx < len(inventory_item_ids) else "").strip()
        if not (desc or qty or price or inventory_item_id):
            continue
        items.append(
            {
                "description": desc,
                "quantity": qty,
                "unit_price": price,
                "inventory_item_id": inventory_item_id,
            }
        )
    return items


def _parse_quote_line_items(items: list[dict]) -> list[dict]:
    parsed: list[dict] = []
    for item in items:
        desc = (item.get("description") or "").strip()
        if not desc:
            raise ValueError("Line item description is required")
        inventory_item_id = (item.get("inventory_item_id") or "").strip() or None
        qty = _parse_decimal(item.get("quantity"), "line item quantity") or Decimal("1.000")
        price = _parse_decimal(item.get("unit_price"), "line item unit price") or Decimal("0.00")
        if qty <= 0:
            raise ValueError("Line item quantity must be greater than 0")
        if price < 0:
            raise ValueError("Line item unit price must be 0 or greater")
        parsed.append(
            {
                "description": desc,
                "quantity": qty,
                "unit_price": price,
                "inventory_item_id": inventory_item_id,
            }
        )
    return parsed


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _load_contact_people_orgs(db: Session) -> dict:
    people = person_service.people.list(
        db=db,
        email=None,
        status=None,
        party_status=None,
        organization_id=None,
        is_active=True,
        order_by="last_name",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    organizations = db.query(Organization).order_by(Organization.name.asc()).limit(500).all()
    return {"people": people, "organizations": organizations}


router.include_router(crm_presence_router)
router.include_router(crm_inbox_settings_router)
router.include_router(crm_inbox_catalog_router)
router.include_router(crm_inbox_comment_reply_router)
router.include_router(crm_inbox_comments_router)
router.include_router(crm_inbox_connectors_actions_router)
router.include_router(crm_inbox_conversations_router)
router.include_router(crm_inbox_actions_core_router)
router.include_router(crm_inbox_message_router)
router.include_router(crm_inbox_private_notes_router)
router.include_router(crm_inbox_start_router)
router.include_router(crm_inbox_status_router)
router.include_router(crm_contacts_router)
router.include_router(crm_leads_router)
router.include_router(crm_quotes_router)
router.include_router(crm_sales_router)
router.include_router(crm_widget_router)


def _parse_inbox_date(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    """Parse a YYYY-MM-DD string into a timezone-aware datetime."""
    if not value:
        return None
    try:
        d = datetime.strptime(value.strip(), "%Y-%m-%d")
        t = time.max if end_of_day else time.min
        return datetime.combine(d.date(), t, tzinfo=UTC)
    except ValueError:
        return None


@router.get("/inbox", response_class=HTMLResponse)
async def inbox(
    request: Request,
    db: Session = Depends(get_db),
    channel: str | None = None,
    status: str | None = None,
    outbox_status: str | None = None,
    search: str | None = None,
    assignment: str | None = None,
    target_id: str | None = None,
    conversation_id: str | None = None,
    comment_id: str | None = None,
    agent_id: str | None = None,
    assigned_from: str | None = None,
    assigned_to: str | None = None,
    saved_filter_id: str | None = None,
    offset: int | None = None,
    limit: int | None = None,
    page: int | None = None,
):
    """Omni-channel inbox view."""
    from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats

    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)
    query_params_map = {k: str(v) for k, v in request.query_params.items()}
    person_id_raw = (current_user.get("person_id") or "").strip() if current_user else ""
    saved_filters: list[dict] = []
    if person_id_raw:
        from app.services.crm.inbox import saved_filters as saved_filters_service

        person_uuid = coerce_uuid(person_id_raw)
        saved_filters = saved_filters_service.list_saved_filters(db, person_uuid)
        if saved_filter_id and not saved_filters_service.has_managed_params(query_params_map):
            saved = saved_filters_service.get_saved_filter(db, person_uuid, saved_filter_id)
            if saved and isinstance(saved.get("params"), dict):
                merged = saved_filters_service.merge_query_with_saved_filter(
                    query_params_map,
                    saved["params"],
                )
                merged["saved_filter_id"] = str(saved_filter_id)
                return RedirectResponse(url=f"/admin/crm/inbox?{urlencode(merged)}", status_code=303)

    safe_limit = max(int(limit or 150), 1)
    safe_page = max(int(page or 1), 1)
    safe_offset = max(int(offset or ((safe_page - 1) * safe_limit)), 0)
    assigned_from_dt = _parse_inbox_date(assigned_from)
    assigned_to_dt = _parse_inbox_date(assigned_to, end_of_day=True)
    context = await build_inbox_page_context(
        db,
        current_user=current_user,
        sidebar_stats=sidebar_stats,
        csrf_token=get_csrf_token(request),
        query_params=request.query_params,
        channel=channel,
        status=status,
        outbox_status=outbox_status,
        search=search,
        assignment=assignment,
        target_id=target_id,
        conversation_id=conversation_id,
        comment_id=comment_id,
        filter_agent_id=agent_id,
        assigned_from=assigned_from_dt,
        assigned_to=assigned_to_dt,
        offset=safe_offset,
        limit=safe_limit,
        page=safe_page,
    )
    return templates.TemplateResponse(
        "admin/crm/inbox.html",
        {
            "request": request,
            "saved_filters": saved_filters,
            "current_saved_filter_id": saved_filter_id or "",
            **context,
        },
    )

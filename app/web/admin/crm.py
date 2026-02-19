"""CRM web routes - Omni-channel Inbox."""

import base64
import json
import mimetypes
import os
import re
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html import escape as html_escape
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import settings
from app.csrf import get_csrf_token
from app.db import SessionLocal
from app.logging import get_logger
from app.models.crm.conversation import Message
from app.models.crm.enums import (
    ChannelType,
    LeadStatus,
    MessageStatus,
    QuoteStatus,
)
from app.models.crm.sales import Lead, Pipeline, PipelineStage, Quote
from app.models.domain_settings import SettingDomain
from app.models.person import ChannelType as PersonChannelType
from app.models.person import Person
from app.models.projects import ProjectType
from app.models.subscriber import Organization, Subscriber
from app.schemas.crm.conversation import (
    ConversationCreate,
    MessageAttachmentCreate,
)
from app.schemas.crm.inbox import InboxSendRequest
from app.schemas.crm.sales import (
    LeadCreate,
    LeadUpdate,
    PipelineCreate,
    PipelineStageCreate,
    PipelineStageUpdate,
    PipelineUpdate,
    QuoteCreate,
    QuoteLineItemCreate,
    QuoteLineItemUpdate,
    QuoteUpdate,
)
from app.services import crm as crm_service
from app.services import person as person_service
from app.services.audit_helpers import (
    build_changes_metadata,
    log_audit_event,
)
from app.services.auth_dependencies import require_permission
from app.services.common import coerce_uuid
from app.services.crm import contact as contact_service
from app.services.crm import conversation as conversation_service
from app.services.crm import inbox as inbox_service
from app.services.crm.conversations.service import MessageAttachments as MessageAttachmentsService
from app.services.crm.inbox.page_context import build_inbox_page_context
from app.services.settings_spec import resolve_value
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
from app.web.admin.crm_presence import router as crm_presence_router


# Simple tax rate stub for quotes (billing service was removed)
class _TaxRate:
    """Simple tax rate object for quote calculations."""

    def __init__(self, id: str, name: str, rate: float):
        self.id = id
        self.name = name
        self.rate = rate  # Rate as decimal (0.075 = 7.5%)


# Default tax rates available for quotes
_DEFAULT_TAX_RATES = [
    _TaxRate("vat-0", "No Tax (0%)", 0.0),
    _TaxRate("vat-5", "VAT 5%", 0.05),
    _TaxRate("vat-7.5", "VAT 7.5%", 0.075),
    _TaxRate("vat-10", "VAT 10%", 0.10),
    _TaxRate("vat-15", "VAT 15%", 0.15),
]
_TAX_RATES_BY_ID = {r.id: r for r in _DEFAULT_TAX_RATES}

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


class _StubTaxRates:
    @staticmethod
    def list(*args, **kwargs):
        return _DEFAULT_TAX_RATES

    @staticmethod
    def get(db, tax_rate_id: str):
        return _TAX_RATES_BY_ID.get(tax_rate_id)


class _StubBillingService:
    tax_rates = _StubTaxRates()


billing_service = _StubBillingService()


def _ensure_pydyf_compat() -> None:
    """Patch pydyf.PDF initializer for older API variants."""
    try:
        import pydyf  # type: ignore[import-untyped]
    except Exception:
        return
    try:
        init_args = pydyf.PDF.__init__.__code__.co_argcount
    except Exception:
        return
    if init_args == 1:
        original_init = pydyf.PDF.__init__

        def _compat_init(self, *args, **kwargs):
            original_init(self)
            version = args[0] if len(args) > 0 else kwargs.get("version")
            identifier = args[1] if len(args) > 1 else kwargs.get("identifier")
            if version is not None:
                self.version = version if isinstance(version, bytes | bytearray) else str(version).encode()
            if identifier is not None:
                self.identifier = identifier
            if not hasattr(self, "version"):
                self.version = b"1.7"
            if not hasattr(self, "identifier"):
                self.identifier = None
            return None

        pydyf.PDF.__init__ = _compat_init
    if not hasattr(pydyf.Stream, "transform"):

        def _compat_transform(self, a=1, b=0, c=0, d=1, e=0, f=0):
            return self.set_matrix(a, b, c, d, e, f)

        pydyf.Stream.transform = _compat_transform
    if not hasattr(pydyf.Stream, "text_matrix"):

        def _compat_text_matrix(self, a=1, b=0, c=0, d=1, e=0, f=0):
            return self.set_matrix(a, b, c, d, e, f)

        pydyf.Stream.text_matrix = _compat_text_matrix


templates = Jinja2Templates(directory="templates")
REGION_OPTIONS = ["Gudu", "Garki", "Gwarimpa", "Jabi", "Lagos"]
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


def _crm_base_context(request: Request, db: Session, active_page: str) -> dict:
    from app.web.admin import get_current_user, get_sidebar_stats

    return {
        "request": request,
        "current_user": get_current_user(request),
        "sidebar_stats": get_sidebar_stats(db),
        "active_page": active_page,
        "active_menu": "crm",
        "csrf_token": get_csrf_token(request),
    }


def _load_crm_sales_options(db: Session) -> dict:
    contacts = crm_service.contacts.list(
        db=db,
        person_id=None,
        organization_id=None,
        party_status=None,
        is_active=True,
        search=None,
        order_by="display_name",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    # Load people for the unified party model
    from app.services.person import people as person_svc

    people = person_svc.list(
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
    pipelines = crm_service.pipelines.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    stages = crm_service.pipeline_stages.list(
        db=db,
        pipeline_id=None,
        is_active=True,
        order_by="order_index",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    agents = crm_service.agents.list(
        db=db,
        person_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    # Lead "Owner" dropdown uses CrmAgent IDs. In addition to active agents, include
    # any agents tied to active members of the Sales service team so those contacts
    # are assignable as lead owners.
    #
    # NOTE: This does not create missing CrmAgent rows; if a service team member has
    # no CrmAgent record yet, they won't appear until one is created.
    try:
        from app.models.crm.team import CrmAgent
        from app.models.service_team import ServiceTeamMember

        sales_team_id = coerce_uuid("7ba88183-1f51-438c-b81c-02f90cbd5287")
        member_person_ids = {
            person_id
            for (person_id,) in (
                db.query(ServiceTeamMember.person_id)
                .filter(ServiceTeamMember.team_id == sales_team_id)
                .filter(ServiceTeamMember.is_active.is_(True))
                .all()
            )
            if person_id
        }
        if member_person_ids:
            team_agents = (
                db.query(CrmAgent)
                .filter(CrmAgent.person_id.in_(member_person_ids))
                .order_by(CrmAgent.created_at.desc())
                .limit(500)
                .all()
            )
            by_id = {str(agent.id): agent for agent in (agents or [])}
            for agent in team_agents:
                by_id[str(agent.id)] = agent
            agents = list(by_id.values())
    except Exception:
        logger.debug("Failed to include Sales service team members in lead owner agent options.", exc_info=True)
    # Use service function for efficient bulk agent label fetch
    agent_labels = crm_service.get_agent_labels(db, agents)
    return {
        "contacts": contacts,
        "people": people,
        "pipelines": pipelines,
        "stages": stages,
        "agents": agents,
        "agent_labels": agent_labels,
    }


def _load_pipeline_stages_for_pipeline(db: Session, pipeline_id: str | None) -> list[PipelineStage]:
    if not pipeline_id:
        return []
    try:
        pipeline_uuid = coerce_uuid(pipeline_id)
    except Exception:
        return []
    return (
        db.query(PipelineStage)
        .filter(PipelineStage.pipeline_id == pipeline_uuid)
        .filter(PipelineStage.is_active.is_(True))
        .order_by(PipelineStage.order_index.asc(), PipelineStage.name.asc())
        .all()
    )


def _format_project_summary(
    quote: Quote,
    lead: Lead | None,
    contact: Person | None,
    company_name: str | None,
    support_email: str | None = None,
) -> str:
    contact_name = None
    if contact:
        contact_name = (
            contact.display_name
            or " ".join(part for part in [contact.first_name, contact.last_name] if part).strip()
            or contact.email
            or contact.phone
        )
    quote_label = (
        quote.metadata_.get("quote_name")
        if isinstance(quote.metadata_, dict) and quote.metadata_.get("quote_name")
        else None
    )
    project_type = (
        quote.metadata_.get("project_type")
        if isinstance(quote.metadata_, dict) and quote.metadata_.get("project_type")
        else None
    )
    if not project_type:
        project_type = quote_label or (lead.title if lead and lead.title else None)
    if isinstance(project_type, str):
        project_type = project_type.replace("_", " ").strip()
    if not project_type:
        project_type = f"Project {str(quote.id)[:8].upper()}"

    expiry_label = "Not specified"
    if quote.expires_at:
        expiry_label = quote.expires_at.strftime("%b %d, %Y")

    lines: list[str] = [
        "Subject: Installation Quote",
        "",
        f"Dear {contact_name or 'Customer'},",
        "",
        f"Please find the requested quotation for {project_type} attached for your review.",
        "",
        f"The document contains a breakdown for your order. This quote is valid until {expiry_label}.",
        "",
        f"Should you have any questions or wish to proceed, please contact us on {support_email}."
        if support_email
        else "Should you have any questions or wish to proceed, please do not hesitate to reach out.",
        "",
        "Best regards,",
        "",
        company_name or "Dotmac",
    ]
    return "\n".join(lines)


def _build_quote_pdf_bytes(
    request: Request,
    quote: Quote,
    items: list,
    lead: Lead | None,
    contact: Person | None,
    quote_name: str | None,
    branding: dict | None,
) -> bytes:
    template = templates.get_template("admin/crm/quote_pdf.html")
    branding_payload = dict(branding or {})
    branding_payload["logo_src"] = _resolve_brand_logo_src(branding_payload, request)
    html = template.render(
        {
            "request": request,
            "quote": quote,
            "items": items,
            "lead": lead,
            "contact": contact,
            "quote_name": quote_name or "",
            "branding": branding_payload,
        }
    )
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="WeasyPrint is not installed on the server. Install it to generate PDFs.",
        ) from exc
    _ensure_pydyf_compat()
    html_doc = HTML(string=html, base_url=str(request.base_url))
    return html_doc.write_pdf()


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


def _load_crm_agent_team_options(db: Session) -> dict:
    """Get agents and teams for assignment dropdowns (uses service layer)."""
    return crm_service.get_agent_team_options(db)


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme in {"http", "https", "mailto", "tel"}:
        return True
    return parsed.scheme == ""


def _resolve_brand_logo_src(branding: dict, request: Request) -> str | None:
    logo_url = branding.get("logo_url") if isinstance(branding, dict) else None
    if not logo_url or not isinstance(logo_url, str):
        return None
    if logo_url.startswith("data:"):
        return logo_url

    # Prefer embedding branding assets as data URIs so PDF rendering works
    # even without outbound HTTPS (missing CA certs, no egress, etc.).
    marker = "uploads/branding/"
    idx = logo_url.find(marker)
    if idx >= 0:
        key = logo_url[idx:]
        try:
            from app.services.storage import storage

            data = storage.get(key)
            mime, _ = mimetypes.guess_type(key)
            encoded = base64.b64encode(data).decode("ascii")
            return f"data:{mime or 'image/png'};base64,{encoded}"
        except Exception:
            pass  # Fall through to URL-based return

    if logo_url.startswith("/"):
        return urljoin(str(request.base_url), logo_url.lstrip("/"))
    return logo_url


def _get_current_roles(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        roles = auth.get("roles") or []
        if isinstance(roles, list):
            return [str(role) for role in roles]
    return []


def _get_current_scopes(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        scopes = auth.get("scopes") or []
        if isinstance(scopes, list):
            return [str(scope) for scope in scopes]
    return []


def _is_admin_request(request: Request) -> bool:
    roles = _get_current_roles(request)
    return any(role.strip().lower() == "admin" for role in roles)


def _is_manager_request(request: Request) -> bool:
    roles = _get_current_roles(request)
    return any(role.strip().lower() == "manager" for role in roles)


def _can_view_live_location_map(request: Request) -> bool:
    if _is_admin_request(request) or _is_manager_request(request):
        return True
    scopes = {scope.strip().lower() for scope in _get_current_scopes(request)}
    return "crm:location:read" in scopes


def _require_admin_role(request: Request) -> None:
    if _is_admin_request(request):
        return
    raise HTTPException(status_code=403, detail="Only admin users can delete quotes.")


def _can_write_sales(request: Request) -> bool:
    if _is_admin_request(request):
        return True
    scopes = set(_get_current_scopes(request))
    return bool({"crm:lead:write", "crm:lead", "crm"} & scopes)


def _apply_message_attachments(
    db: Session,
    message: Message,
    attachments: list[dict] | None,
) -> None:
    if not attachments:
        return
    for item in attachments:
        if not isinstance(item, dict):
            continue
        MessageAttachmentsService.create(
            db,
            MessageAttachmentCreate(
                message_id=message.id,
                file_name=item.get("file_name"),
                mime_type=item.get("mime_type"),
                file_size=item.get("file_size"),
                external_url=item.get("url"),
                metadata_={"stored_name": item.get("stored_name")},
            ),
        )


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
    offset: int | None = None,
    limit: int | None = None,
    page: int | None = None,
):
    """Omni-channel inbox view."""
    from app.web.admin import get_current_user, get_sidebar_stats

    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)
    safe_limit = max(int(limit or 150), 1)
    safe_page = max(int(page or 1), 1)
    safe_offset = max(int(offset or ((safe_page - 1) * safe_limit)), 0)
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
        offset=safe_offset,
        limit=safe_limit,
        page=safe_page,
    )
    return templates.TemplateResponse(
        "admin/crm/inbox.html",
        {
            "request": request,
            **context,
        },
    )


@router.get(
    "/leads",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:read"))],
)
def crm_leads_list(
    request: Request,
    status: str | None = None,
    pipeline_id: str | None = None,
    stage_id: str | None = None,
    owner_agent_id: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * per_page
    leads = crm_service.leads.list(
        db=db,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
        owner_agent_id=owner_agent_id,
        status=status,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )
    all_leads = crm_service.leads.list(
        db=db,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
        owner_agent_id=owner_agent_id,
        status=status,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
    )
    total = len(all_leads)
    total_pages = (total + per_page - 1) // per_page if total else 1
    options = _load_crm_sales_options(db)
    lead_person_ids = [lead.person_id for lead in leads if lead.person_id]
    if lead_person_ids:
        lead_contacts = db.query(Person).filter(Person.id.in_(lead_person_ids)).all()
        contacts_map = {str(contact.id): contact for contact in lead_contacts}
    else:
        contacts_map = {}
    pipeline_map = {str(pipeline.id): pipeline for pipeline in options["pipelines"]}
    stage_map = {str(stage.id): stage for stage in options["stages"]}

    # Compute lead stats (unfiltered totals for dashboard cards)
    all_leads_unfiltered = crm_service.leads.list(
        db=db,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
    )
    lead_stats: dict[str, Any] = {"total": len(all_leads_unfiltered)}
    status_counts: dict[str, int] = {}
    total_value = 0.0
    for lead_item in all_leads_unfiltered:
        key = lead_item.status.value if lead_item.status else "new"
        status_counts[key] = status_counts.get(key, 0) + 1
        if lead_item.estimated_value:
            total_value += float(lead_item.estimated_value)
    lead_stats["by_status"] = status_counts
    lead_stats["total_value"] = total_value

    context = _crm_base_context(request, db, "leads")
    context.update(
        {
            "leads": leads,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "status": status or "",
            "pipeline_id": pipeline_id or "",
            "stage_id": stage_id or "",
            "owner_agent_id": owner_agent_id or "",
            "lead_statuses": [item.value for item in LeadStatus],
            "contacts": options["contacts"],
            "pipelines": options["pipelines"],
            "stages": options["stages"],
            "agents": options["agents"],
            "agent_labels": options["agent_labels"],
            "contacts_map": contacts_map,
            "pipeline_map": pipeline_map,
            "stage_map": stage_map,
            "lead_stats": lead_stats,
            "can_write_leads": _can_write_sales(request),
        }
    )
    return templates.TemplateResponse("admin/crm/leads.html", context)


@router.get(
    "/leads/new",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:write"))],
)
def crm_lead_new(request: Request, db: Session = Depends(get_db)):
    from app.models.crm.team import CrmAgent
    from app.web.admin import get_current_user

    person_id = request.query_params.get("person_id", "").strip()
    contact_id = request.query_params.get("contact_id", "").strip()  # Legacy support
    pipeline_id = request.query_params.get("pipeline_id", "").strip()
    if not person_id and contact_id:
        person_id = contact_id
    options = _load_crm_sales_options(db)
    if not pipeline_id and options["pipelines"]:
        pipeline_id = str(options["pipelines"][0].id)
    stages_for_pipeline = _load_pipeline_stages_for_pipeline(db, pipeline_id)
    if person_id:
        from app.services.person import people as person_svc

        if not any(str(person.id) == person_id for person in options["people"]):
            try:
                person = person_svc.get(db, person_id)
                options["people"] = [person] + options["people"]
            except Exception:
                logger.debug("Failed to pre-load selected person for CRM lead form.", exc_info=True)
    lead = {
        "id": "",
        "person_id": person_id,
        "contact_id": contact_id,
        "pipeline_id": pipeline_id,
        "stage_id": str(stages_for_pipeline[0].id) if stages_for_pipeline else "",
        "owner_agent_id": "",
        "title": "",
        "status": LeadStatus.new.value,
        "estimated_value": "",
        "currency": "",
        "probability": None,
        "expected_close_date": "",
        "lost_reason": "",
        "region": "",
        "address": "",
        "notes": "",
        "is_active": True,
    }
    current_user = get_current_user(request)
    current_person_id = current_user.get("person_id") if current_user else None
    if current_person_id:
        agent = (
            db.query(CrmAgent)
            .filter(
                CrmAgent.person_id == coerce_uuid(current_person_id),
                CrmAgent.is_active.is_(True),
            )
            .first()
        )
        if agent:
            lead["owner_agent_id"] = str(agent.id)
    context = _crm_base_context(request, db, "leads")
    context.update(
        {
            "lead": lead,
            "lead_statuses": [item.value for item in LeadStatus],
            "people": options["people"],
            "contacts": options["contacts"],
            "pipelines": options["pipelines"],
            "stages": options["stages"],
            "agents": options["agents"],
            "agent_labels": options["agent_labels"],
            "form_title": "New Lead",
            "submit_label": "Create Lead",
            "action_url": "/admin/crm/leads",
            "region_options": REGION_OPTIONS,
        }
    )
    return templates.TemplateResponse("admin/crm/lead_form.html", context)


@router.get(
    "/leads/{lead_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:read"))],
)
def crm_lead_detail(request: Request, lead_id: str, db: Session = Depends(get_db)):
    lead = crm_service.leads.get(db=db, lead_id=lead_id)
    options = _load_crm_sales_options(db)
    pipeline_map = {str(pipeline.id): pipeline for pipeline in options["pipelines"]}
    stage_map = {str(stage.id): stage for stage in options["stages"]}

    contact = None
    contact_id_value = lead.person_id or lead.contact_id
    if contact_id_value:
        try:
            contact = contact_service.Contacts.get(db=db, contact_id=str(contact_id_value))
        except Exception:
            contact = db.get(Person, coerce_uuid(contact_id_value))

    pipeline = pipeline_map.get(str(lead.pipeline_id)) if lead.pipeline_id else None
    stage = stage_map.get(str(lead.stage_id)) if lead.stage_id else None
    owner_label = options["agent_labels"].get(str(lead.owner_agent_id)) if lead.owner_agent_id else "—"
    status_val = lead.status.value if lead.status else LeadStatus.new.value

    context = _crm_base_context(request, db, "leads")
    context.update(
        {
            "lead": lead,
            "contact": contact,
            "pipeline": pipeline,
            "stage": stage,
            "owner_label": owner_label,
            "status_val": status_val,
            "can_write_leads": _can_write_sales(request),
        }
    )
    return templates.TemplateResponse("admin/crm/lead_detail.html", context)


@router.post(
    "/leads/{lead_id}/status",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:write"))],
)
async def crm_lead_status_update(
    request: Request,
    lead_id: str,
    db: Session = Depends(get_db),
):
    """Quick inline status update for a lead."""
    form = await request.form()
    status_raw = form.get("status")
    status_value = status_raw.strip() if isinstance(status_raw, str) else ""
    try:
        crm_service.leads.get(db=db, lead_id=lead_id)
        from app.schemas.crm.sales import LeadUpdate

        payload = LeadUpdate.model_validate({"status": status_value})
        crm_service.leads.update(db=db, lead_id=lead_id, payload=payload)
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                content="",
                headers={"HX-Redirect": f"/admin/crm/leads/{lead_id}"},
            )
        return RedirectResponse(f"/admin/crm/leads/{lead_id}", status_code=303)
    except Exception as exc:
        error = html_escape(exc.detail if hasattr(exc, "detail") else str(exc))
        if request.headers.get("HX-Request"):
            return HTMLResponse(content=f'<p class="text-red-600 text-sm">{error}</p>', status_code=422)
        return RedirectResponse(f"/admin/crm/leads/{lead_id}", status_code=303)


@router.post(
    "/leads",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:write"))],
)
def crm_lead_create(
    request: Request,
    person_id: str | None = Form(None),
    contact_id: str | None = Form(None),  # Legacy support
    pipeline_id: str | None = Form(None),
    stage_id: str | None = Form(None),
    owner_agent_id: str | None = Form(None),
    title: str | None = Form(None),
    status: str | None = Form(None),
    estimated_value: str | None = Form(None),
    currency: str | None = Form(None),
    probability: str | None = Form(None),
    expected_close_date: str | None = Form(None),
    lost_reason: str | None = Form(None),
    region: str | None = Form(None),
    address: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from datetime import date as date_type

    from app.models.crm.team import CrmAgent
    from app.web.admin import get_current_user

    error = None
    lead: dict[str, str | bool] = {
        "person_id": (person_id or "").strip(),
        "contact_id": (contact_id or "").strip(),
        "pipeline_id": (pipeline_id or "").strip(),
        "stage_id": (stage_id or "").strip(),
        "owner_agent_id": (owner_agent_id or "").strip(),
        "title": (title or "").strip(),
        "status": (status or "").strip(),
        "estimated_value": (estimated_value or "").strip(),
        "currency": (currency or "").strip(),
        "probability": (probability or "").strip(),
        "expected_close_date": (expected_close_date or "").strip(),
        "lost_reason": (lost_reason or "").strip(),
        "region": (region or "").strip(),
        "address": (address or "").strip(),
        "notes": (notes or "").strip(),
        "is_active": is_active == "true",
    }
    try:
        person_id_value = lead["person_id"] if isinstance(lead["person_id"], str) else ""
        contact_id_value = lead["contact_id"] if isinstance(lead["contact_id"], str) else ""
        pipeline_id_value = lead["pipeline_id"] if isinstance(lead["pipeline_id"], str) else ""
        stage_id_value = lead["stage_id"] if isinstance(lead["stage_id"], str) else ""
        owner_agent_id_value = lead["owner_agent_id"] if isinstance(lead["owner_agent_id"], str) else ""
        title_value = lead["title"] if isinstance(lead["title"], str) else ""
        status_value = lead["status"] if isinstance(lead["status"], str) else ""
        estimated_value_value = lead["estimated_value"] if isinstance(lead["estimated_value"], str) else ""
        currency_value = lead["currency"] if isinstance(lead["currency"], str) else ""
        probability_value = lead["probability"] if isinstance(lead["probability"], str) else ""
        expected_close_date_value = lead["expected_close_date"] if isinstance(lead["expected_close_date"], str) else ""
        lost_reason_value = lead["lost_reason"] if isinstance(lead["lost_reason"], str) else ""
        region_value = lead["region"] if isinstance(lead["region"], str) else ""
        address_value = lead["address"] if isinstance(lead["address"], str) else ""
        notes_value = lead["notes"] if isinstance(lead["notes"], str) else ""
        if not owner_agent_id_value:
            current_user = get_current_user(request)
            current_person_id = current_user.get("person_id") if current_user else None
            if current_person_id:
                agent = (
                    db.query(CrmAgent)
                    .filter(
                        CrmAgent.person_id == coerce_uuid(current_person_id),
                        CrmAgent.is_active.is_(True),
                    )
                    .first()
                )
                if agent:
                    owner_agent_id_value = str(agent.id)
        if pipeline_id_value and not stage_id_value:
            pipeline_stages = _load_pipeline_stages_for_pipeline(db, pipeline_id_value)
            if pipeline_stages:
                stage_id_value = str(pipeline_stages[0].id)
        value = _parse_decimal(estimated_value_value, "estimated_value")
        prob_value = int(probability_value) if probability_value else None
        close_date = None
        if expected_close_date_value:
            close_date = date_type.fromisoformat(expected_close_date_value)
        resolved_person_id = person_id_value or contact_id_value or None
        try:
            status_enum = LeadStatus(status_value) if status_value else LeadStatus.new
        except ValueError:
            status_enum = LeadStatus.new
        person_uuid = _coerce_uuid_optional(resolved_person_id)
        if not person_uuid:
            raise ValueError("Person is required.")
        payload = LeadCreate(
            person_id=person_uuid,
            pipeline_id=_coerce_uuid_optional(pipeline_id_value),
            stage_id=_coerce_uuid_optional(stage_id_value),
            owner_agent_id=_coerce_uuid_optional(owner_agent_id_value),
            title=title_value or None,
            status=status_enum,
            estimated_value=value,
            currency=currency_value or None,
            probability=prob_value,
            expected_close_date=close_date,
            lost_reason=lost_reason_value or None,
            region=region_value or None,
            address=address_value or None,
            notes=notes_value or None,
            is_active=bool(lead["is_active"]),
        )
        crm_service.leads.create(db=db, payload=payload)
        return RedirectResponse(url="/admin/crm/leads", status_code=303)
    except (ValidationError, ValueError) as exc:
        error = str(exc)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    options = _load_crm_sales_options(db)
    context = _crm_base_context(request, db, "leads")
    context.update(
        {
            "lead": lead,
            "lead_statuses": [item.value for item in LeadStatus],
            "people": options["people"],
            "contacts": options["contacts"],
            "pipelines": options["pipelines"],
            "stages": options["stages"],
            "agents": options["agents"],
            "agent_labels": options["agent_labels"],
            "form_title": "New Lead",
            "submit_label": "Create Lead",
            "action_url": "/admin/crm/leads",
            "error": error,
            "region_options": REGION_OPTIONS,
        }
    )
    return templates.TemplateResponse("admin/crm/lead_form.html", context, status_code=400)


@router.get(
    "/leads/{lead_id}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:write"))],
)
def crm_lead_edit(request: Request, lead_id: str, db: Session = Depends(get_db)):
    lead_obj = crm_service.leads.get(db=db, lead_id=lead_id)
    lead = {
        "id": str(lead_obj.id),
        "person_id": str(lead_obj.person_id) if lead_obj.person_id else "",
        "contact_id": str(lead_obj.contact_id) if lead_obj.contact_id else "",
        "pipeline_id": str(lead_obj.pipeline_id) if lead_obj.pipeline_id else "",
        "stage_id": str(lead_obj.stage_id) if lead_obj.stage_id else "",
        "owner_agent_id": str(lead_obj.owner_agent_id) if lead_obj.owner_agent_id else "",
        "title": lead_obj.title or "",
        "status": lead_obj.status.value if lead_obj.status else "",
        "estimated_value": lead_obj.estimated_value or "",
        "currency": lead_obj.currency or "",
        "probability": lead_obj.probability,
        "expected_close_date": lead_obj.expected_close_date.isoformat() if lead_obj.expected_close_date else "",
        "lost_reason": lead_obj.lost_reason or "",
        "region": lead_obj.region or "",
        "address": lead_obj.address or "",
        "notes": lead_obj.notes or "",
        "is_active": lead_obj.is_active,
    }
    options = _load_crm_sales_options(db)
    if not lead["pipeline_id"] and options["pipelines"]:
        lead["pipeline_id"] = str(options["pipelines"][0].id)
    if not lead["stage_id"] and lead["pipeline_id"]:
        stages_for_pipeline = _load_pipeline_stages_for_pipeline(db, lead["pipeline_id"])
        if stages_for_pipeline:
            lead["stage_id"] = str(stages_for_pipeline[0].id)
    if lead_obj.person_id and not any(str(person.id) == str(lead_obj.person_id) for person in options["people"]):
        from app.services.person import people as person_svc

        try:
            person = person_svc.get(db, str(lead_obj.person_id))
            options["people"] = [person] + options["people"]
        except Exception:
            logger.debug("Failed to pre-load person for CRM lead edit form.", exc_info=True)
    context = _crm_base_context(request, db, "leads")
    context.update(
        {
            "lead": lead,
            "lead_statuses": [item.value for item in LeadStatus],
            "people": options["people"],
            "contacts": options["contacts"],
            "pipelines": options["pipelines"],
            "stages": options["stages"],
            "agents": options["agents"],
            "agent_labels": options["agent_labels"],
            "form_title": "Edit Lead",
            "submit_label": "Save Lead",
            "action_url": f"/admin/crm/leads/{lead_id}/edit",
            "region_options": REGION_OPTIONS,
        }
    )
    return templates.TemplateResponse("admin/crm/lead_form.html", context)


@router.post(
    "/leads/{lead_id}/edit",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:write"))],
)
def crm_lead_update(
    request: Request,
    lead_id: str,
    person_id: str | None = Form(None),
    contact_id: str | None = Form(None),  # Legacy
    pipeline_id: str | None = Form(None),
    stage_id: str | None = Form(None),
    owner_agent_id: str | None = Form(None),
    title: str | None = Form(None),
    status: str | None = Form(None),
    estimated_value: str | None = Form(None),
    currency: str | None = Form(None),
    probability: str | None = Form(None),
    expected_close_date: str | None = Form(None),
    lost_reason: str | None = Form(None),
    region: str | None = Form(None),
    address: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from datetime import date as date_type

    error = None
    lead: dict[str, str | bool] = {
        "id": lead_id,
        "person_id": (person_id or "").strip(),
        "contact_id": (contact_id or "").strip(),
        "pipeline_id": (pipeline_id or "").strip(),
        "stage_id": (stage_id or "").strip(),
        "owner_agent_id": (owner_agent_id or "").strip(),
        "title": (title or "").strip(),
        "status": (status or "").strip(),
        "estimated_value": (estimated_value or "").strip(),
        "currency": (currency or "").strip(),
        "probability": (probability or "").strip(),
        "expected_close_date": (expected_close_date or "").strip(),
        "lost_reason": (lost_reason or "").strip(),
        "region": (region or "").strip(),
        "address": (address or "").strip(),
        "notes": (notes or "").strip(),
        "is_active": is_active == "true",
    }
    try:
        person_id_value = lead["person_id"] if isinstance(lead["person_id"], str) else ""
        contact_id_value = lead["contact_id"] if isinstance(lead["contact_id"], str) else ""
        pipeline_id_value = lead["pipeline_id"] if isinstance(lead["pipeline_id"], str) else ""
        stage_id_value = lead["stage_id"] if isinstance(lead["stage_id"], str) else ""
        owner_agent_id_value = lead["owner_agent_id"] if isinstance(lead["owner_agent_id"], str) else ""
        title_value = lead["title"] if isinstance(lead["title"], str) else ""
        status_value = lead["status"] if isinstance(lead["status"], str) else ""
        estimated_value_value = lead["estimated_value"] if isinstance(lead["estimated_value"], str) else ""
        currency_value = lead["currency"] if isinstance(lead["currency"], str) else ""
        probability_value = lead["probability"] if isinstance(lead["probability"], str) else ""
        expected_close_date_value = lead["expected_close_date"] if isinstance(lead["expected_close_date"], str) else ""
        lost_reason_value = lead["lost_reason"] if isinstance(lead["lost_reason"], str) else ""
        region_value = lead["region"] if isinstance(lead["region"], str) else ""
        address_value = lead["address"] if isinstance(lead["address"], str) else ""
        notes_value = lead["notes"] if isinstance(lead["notes"], str) else ""
        if pipeline_id_value and not stage_id_value:
            pipeline_stages = _load_pipeline_stages_for_pipeline(db, pipeline_id_value)
            if pipeline_stages:
                stage_id_value = str(pipeline_stages[0].id)
        value = _parse_decimal(estimated_value_value, "estimated_value")
        prob_value = int(probability_value) if probability_value else None
        close_date = None
        if expected_close_date_value:
            close_date = date_type.fromisoformat(expected_close_date_value)
        resolved_person_id = person_id_value or contact_id_value or None
        status_enum = None
        if status_value:
            try:
                status_enum = LeadStatus(status_value)
            except ValueError:
                status_enum = None
        payload = LeadUpdate(
            person_id=_coerce_uuid_optional(resolved_person_id),
            pipeline_id=_coerce_uuid_optional(pipeline_id_value),
            stage_id=_coerce_uuid_optional(stage_id_value),
            owner_agent_id=_coerce_uuid_optional(owner_agent_id_value),
            title=title_value or None,
            status=status_enum,
            estimated_value=value,
            currency=currency_value or None,
            probability=prob_value,
            expected_close_date=close_date,
            lost_reason=lost_reason_value or None,
            region=region_value or None,
            address=address_value or None,
            notes=notes_value or None,
            is_active=bool(lead["is_active"]),
        )
        crm_service.leads.update(db=db, lead_id=lead_id, payload=payload)
        return RedirectResponse(url="/admin/crm/leads", status_code=303)
    except (ValidationError, ValueError) as exc:
        error = str(exc)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    options = _load_crm_sales_options(db)
    context = _crm_base_context(request, db, "leads")
    context.update(
        {
            "lead": lead,
            "lead_statuses": [item.value for item in LeadStatus],
            "people": options["people"],
            "contacts": options["contacts"],
            "pipelines": options["pipelines"],
            "stages": options["stages"],
            "agents": options["agents"],
            "agent_labels": options["agent_labels"],
            "form_title": "Edit Lead",
            "submit_label": "Save Lead",
            "action_url": f"/admin/crm/leads/{lead_id}/edit",
            "error": error,
            "region_options": REGION_OPTIONS,
        }
    )
    return templates.TemplateResponse("admin/crm/lead_form.html", context, status_code=400)


@router.post(
    "/leads/{lead_id}/delete",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission("crm:lead:write"))],
)
def crm_lead_delete(request: Request, lead_id: str, db: Session = Depends(get_db)):
    _ = request
    crm_service.leads.delete(db=db, lead_id=lead_id)
    return RedirectResponse(url="/admin/crm/leads", status_code=303)


@router.get("/quotes", response_class=HTMLResponse)
def crm_quotes_list(
    request: Request,
    status: str | None = None,
    lead_id: str | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=200),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * per_page
    quotes = crm_service.quotes.list(
        db=db,
        lead_id=lead_id,
        status=status,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
        search=search,
    )
    all_quotes = crm_service.quotes.list(
        db=db,
        lead_id=lead_id,
        status=status,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
        search=search,
    )
    total = len(all_quotes)
    total_pages = (total + per_page - 1) // per_page if total else 1
    options = _load_crm_sales_options(db)
    leads = crm_service.leads.list(
        db=db,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )
    lead_map = {str(item.id): item for item in leads}
    contacts_map = {str(contact.id): contact for contact in options["contacts"]}
    stats = crm_service.quotes.count_by_status(db)

    context = _crm_base_context(request, db, "quotes")
    context.update(
        {
            "quotes": quotes,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "status": status or "",
            "lead_id": lead_id or "",
            "search": search or "",
            "quote_statuses": [item.value for item in QuoteStatus],
            "leads": leads,
            "lead_map": lead_map,
            "contacts_map": contacts_map,
            "stats": stats,
            "today": datetime.now(UTC),
        }
    )
    # HTMX partial response for table
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("admin/crm/_quotes_table.html", context)
    return templates.TemplateResponse("admin/crm/quotes.html", context)


@router.get("/quotes/new", response_class=HTMLResponse)
def crm_quote_new(
    request: Request,
    lead_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    options = _load_crm_sales_options(db)
    tax_rates = billing_service.tax_rates.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    leads = crm_service.leads.list(
        db=db,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )
    inventory_items: list = []
    project_types = [item.value for item in ProjectType]
    lead_id_value = (lead_id or "").strip()
    contact_id_value = ""
    if lead_id_value:
        try:
            lead_obj = crm_service.leads.get(db=db, lead_id=lead_id_value)
            if lead_obj.person_id:
                contact_id_value = str(lead_obj.person_id)
        except Exception:
            lead_id_value = ""

    contacts = list(options["contacts"])
    if contact_id_value and not any(str(contact.id) == contact_id_value for contact in contacts):
        contact_person = db.get(Person, coerce_uuid(contact_id_value))
        if contact_person:
            contacts.append(contact_person)

    quote = {
        "id": "",
        "lead_id": lead_id_value,
        "contact_id": contact_id_value,
        "tax_rate_id": "",
        "status": QuoteStatus.draft.value,
        "project_type": "",
        "currency": "NGN",
        "subtotal": "0.00",
        "tax_total": "0.00",
        "total": "0.00",
        "expires_at": "",
        "notes": "",
        "is_active": True,
    }
    context = _crm_base_context(request, db, "quotes")
    context.update(
        {
            "quote": quote,
            "quote_items": [],
            "tax_rates": tax_rates,
            "quote_statuses": [item.value for item in QuoteStatus],
            "project_types": project_types,
            "leads": leads,
            "contacts": contacts,
            "inventory_items": inventory_items,
            "form_title": "New Quote",
            "submit_label": "Create Quote",
            "action_url": "/admin/crm/quotes",
        }
    )
    return templates.TemplateResponse("admin/crm/quote_form.html", context)


@router.get("/quotes/{quote_id}", response_class=HTMLResponse)
def crm_quote_detail(request: Request, quote_id: str, db: Session = Depends(get_db)):
    quote = crm_service.quotes.get(db=db, quote_id=quote_id)
    items = crm_service.quote_line_items.list(
        db=db,
        quote_id=quote_id,
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    lead = None
    if quote.lead_id:
        try:
            lead = crm_service.leads.get(db=db, lead_id=str(quote.lead_id))
        except Exception:
            lead = None
    contact = None
    if quote.person_id:
        contact = contact_service.get_person_with_relationships(db, str(quote.person_id))

    has_email = False
    has_whatsapp = False
    if contact and contact.channels:
        for channel in contact.channels:
            if not channel.address:
                continue
            if channel.channel_type == PersonChannelType.email:
                has_email = True
            if channel.channel_type == PersonChannelType.whatsapp:
                has_whatsapp = True

    company_name_raw = resolve_value(db, SettingDomain.comms, "company_name")
    company_name = (
        company_name_raw.strip() if isinstance(company_name_raw, str) and company_name_raw.strip() else "Dotmac"
    )
    support_email_raw = resolve_value(db, SettingDomain.comms, "support_email")
    support_email = (
        support_email_raw.strip() if isinstance(support_email_raw, str) and support_email_raw.strip() else None
    )
    summary_text = _format_project_summary(quote, lead, contact, company_name, support_email=support_email)

    context = _crm_base_context(request, db, "quotes")
    context.update(
        {
            "quote": quote,
            "items": items,
            "lead": lead,
            "contact": contact,
            "summary_text": summary_text,
            "has_email": has_email,
            "has_whatsapp": has_whatsapp,
            "today": datetime.now(UTC),
        }
    )
    return templates.TemplateResponse("admin/crm/quote_detail.html", context)


@router.post("/quotes/{quote_id}/status", response_class=HTMLResponse)
async def crm_quote_status_update(
    request: Request,
    quote_id: str,
    db: Session = Depends(get_db),
):
    """Quick inline status update for a quote."""
    form = await request.form()
    status_raw = form.get("status")
    status_value = status_raw.strip() if isinstance(status_raw, str) else ""
    try:
        crm_service.quotes.get(db=db, quote_id=quote_id)
        from app.schemas.crm.sales import QuoteUpdate

        payload = QuoteUpdate.model_validate({"status": status_value})
        crm_service.quotes.update(db=db, quote_id=quote_id, payload=payload)
        if request.headers.get("HX-Request"):
            return HTMLResponse(
                content="",
                headers={"HX-Redirect": f"/admin/crm/quotes/{quote_id}"},
            )
        return RedirectResponse(f"/admin/crm/quotes/{quote_id}", status_code=303)
    except Exception as exc:
        error = html_escape(exc.detail if hasattr(exc, "detail") else str(exc))
        if request.headers.get("HX-Request"):
            return HTMLResponse(content=f'<p class="text-red-600 text-sm">{error}</p>', status_code=422)
        return RedirectResponse(f"/admin/crm/quotes/{quote_id}", status_code=303)


@router.post("/quotes/{quote_id}/send-summary", response_class=HTMLResponse)
def crm_quote_send_summary(
    request: Request,
    quote_id: str,
    channel_type: str = Form(...),
    message: str = Form(...),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_current_user

    quote = crm_service.quotes.get(db=db, quote_id=quote_id)
    items = crm_service.quote_line_items.list(
        db=db,
        quote_id=quote_id,
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    lead = None
    if quote.lead_id:
        try:
            lead = crm_service.leads.get(db=db, lead_id=str(quote.lead_id))
        except Exception:
            lead = None

    if not quote.person_id:
        return RedirectResponse(
            url=f"/admin/crm/quotes/{quote_id}?send_error=1",
            status_code=303,
        )

    contact = contact_service.get_person_with_relationships(db, str(quote.person_id))
    if not contact:
        return RedirectResponse(
            url=f"/admin/crm/quotes/{quote_id}?send_error=1",
            status_code=303,
        )

    try:
        channel_enum = ChannelType(channel_type)
    except ValueError:
        return RedirectResponse(
            url=f"/admin/crm/quotes/{quote_id}?send_error=1",
            status_code=303,
        )

    if channel_enum not in (ChannelType.email, ChannelType.whatsapp):
        return RedirectResponse(
            url=f"/admin/crm/quotes/{quote_id}?send_error=1",
            status_code=303,
        )

    person_channel = conversation_service.resolve_person_channel(db, str(contact.id), channel_enum)
    if not person_channel:
        return RedirectResponse(
            url=f"/admin/crm/quotes/{quote_id}?send_error=1",
            status_code=303,
        )

    company_name_raw = resolve_value(db, SettingDomain.comms, "company_name")
    company_name = (
        company_name_raw.strip() if isinstance(company_name_raw, str) and company_name_raw.strip() else "Dotmac"
    )
    support_email_raw = resolve_value(db, SettingDomain.comms, "support_email")
    _support_email = (
        support_email_raw.strip() if isinstance(support_email_raw, str) and support_email_raw.strip() else None
    )
    body = (message or "").strip()
    if not body:
        body = _format_project_summary(quote, lead, contact, company_name, support_email=_support_email)

    quote_label = None
    if isinstance(quote.metadata_, dict):
        quote_label = quote.metadata_.get("quote_name")
    subject = None
    attachments_payload: list[dict] | None = None
    if channel_enum == ChannelType.email:
        subject = "Installation Quote"
        stored_name = None
        try:
            branding_payload = dict(getattr(request.state, "branding", None) or {})
            if "quote_banking_details" not in branding_payload:
                branding_payload["quote_banking_details"] = resolve_value(
                    db, SettingDomain.comms, "quote_banking_details"
                )
            pdf_bytes = _build_quote_pdf_bytes(
                request=request,
                quote=quote,
                items=items,
                lead=lead,
                contact=contact,
                quote_name=quote_label,
                branding=branding_payload,
            )
            max_size = settings.message_attachment_max_size_bytes
            if max_size and len(pdf_bytes) > max_size:
                return RedirectResponse(
                    url=f"/admin/crm/quotes/{quote_id}?send_error=1",
                    status_code=303,
                )
            stored_name = f"{uuid.uuid4().hex}.pdf"
            from app.services.crm.conversations import message_attachments as message_attachment_service

            saved = message_attachment_service.save(
                [
                    {
                        "stored_name": stored_name,
                        "file_name": f"quote_{quote.id}.pdf",
                        "file_size": len(pdf_bytes),
                        "mime_type": "application/pdf",
                        "content": pdf_bytes,
                    }
                ]
            )
            attachments_payload = saved or None
        except Exception:
            return RedirectResponse(
                url=f"/admin/crm/quotes/{quote_id}?send_error=1",
                status_code=303,
            )

    conversation = conversation_service.resolve_open_conversation_for_channel(db, str(contact.id), channel_enum)
    if not conversation:
        conversation = conversation_service.Conversations.create(
            db,
            ConversationCreate(
                person_id=contact.id,
                subject=subject if channel_enum == ChannelType.email else None,
            ),
        )

    current_user = get_current_user(request)
    author_id = current_user.get("person_id") if current_user else None

    try:
        result_msg = inbox_service.send_message(
            db,
            InboxSendRequest(
                conversation_id=conversation.id,
                channel_type=channel_enum,
                subject=subject,
                body=body,
                attachments=attachments_payload,
            ),
            author_id=author_id,
        )
        if result_msg and result_msg.status == MessageStatus.failed:
            return RedirectResponse(
                url=f"/admin/crm/quotes/{quote_id}?send_error=1",
                status_code=303,
            )
    except Exception:
        return RedirectResponse(
            url=f"/admin/crm/quotes/{quote_id}?send_error=1",
            status_code=303,
        )

    if attachments_payload and result_msg:
        _apply_message_attachments(db, result_msg, attachments_payload)

    return RedirectResponse(
        url=f"/admin/crm/quotes/{quote_id}?send=1",
        status_code=303,
    )


@router.get("/quotes/{quote_id}/pdf")
def crm_quote_pdf(request: Request, quote_id: str, db: Session = Depends(get_db)):
    quote = crm_service.quotes.get(db=db, quote_id=quote_id)
    items = crm_service.quote_line_items.list(
        db=db,
        quote_id=quote_id,
        order_by="created_at",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    lead = None
    if quote.lead_id:
        try:
            lead = crm_service.leads.get(db=db, lead_id=str(quote.lead_id))
        except Exception:
            lead = None
    contact = None
    if quote.person_id:
        contact = db.get(Person, quote.person_id)

    stored_name = None
    if isinstance(quote.metadata_, dict):
        stored_name = quote.metadata_.get("quote_name")
    quote_name = stored_name or (contact.display_name if contact and contact.display_name else None)
    if not quote_name and contact:
        quote_name = contact.email

    template = templates.get_template("admin/crm/quote_pdf.html")
    template_path = getattr(template, "filename", None)
    if template_path:
        template_path = os.path.abspath(template_path)
    branding_payload = dict(getattr(request.state, "branding", None) or {})
    if "quote_banking_details" not in branding_payload:
        branding_payload["quote_banking_details"] = resolve_value(db, SettingDomain.comms, "quote_banking_details")
    branding_payload["logo_src"] = _resolve_brand_logo_src(branding_payload, request)
    html = template.render(
        {
            "request": request,
            "quote": quote,
            "items": items,
            "lead": lead,
            "contact": contact,
            "quote_name": quote_name or "",
            "branding": branding_payload,
        }
    )
    if request.query_params.get("smoke") == "1":
        html = f"""
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>PDF Smoke Test</title></head>
<body style="font-family: Arial, sans-serif; font-size: 16px; line-height: 1.6;">
  <p>PDF smoke test</p>
  <p>Quote ID: {quote.id}</p>
  <p>Line items: {len(items) if items else 0}</p>
  <p>Subtotal: {quote.subtotal or 0}</p>
  <p>Tax: {quote.tax_total or 0}</p>
  <p>Total: {quote.total or 0}</p>
  <p>End of test.</p>
</body>
</html>
"""
    if request.query_params.get("plain") == "1":
        currency = quote.currency or ""
        plain_rows = []
        if items:
            for item in items:
                desc = html_escape(str(getattr(item, "description", "") or ""))
                qty = getattr(item, "quantity", 0) or 0
                unit_price = getattr(item, "unit_price", 0) or 0
                amount = getattr(item, "amount", 0) or 0
                plain_rows.append(
                    f"<tr><td>{desc}</td><td style='text-align:right'>{qty}</td>"
                    f"<td style='text-align:right'>{unit_price}</td>"
                    f"<td style='text-align:right'>{amount}</td></tr>"
                )
        else:
            plain_rows.append("<tr><td colspan='4'>No line items found.</td></tr>")
        plain_html = f"""
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Quote {quote.id}</title></head>
<body>
  <h1>{html_escape(quote_name or f"Quote {str(quote.id)[:8]}")}</h1>
  <div>Quote ID: {quote.id}</div>
  <div>Status: {(quote.status.value if quote.status else "draft")}</div>
  <div>Currency: {html_escape(currency)}</div>
  <table border="1" cellpadding="6" cellspacing="0" width="100%%">
    <thead>
      <tr><th>Description</th><th>Qty</th><th>Unit Price</th><th>Amount</th></tr>
    </thead>
    <tbody>
      {"".join(plain_rows)}
    </tbody>
  </table>
  <div>Subtotal: {quote.subtotal or 0}</div>
  <div>Tax: {quote.tax_total or 0}</div>
  <div>Total: {quote.total or 0}</div>
</body>
</html>
"""
        html = plain_html
    logger.info(
        "quote_pdf_template=%s quote_pdf_html_len=%s items=%s totals=subtotal:%s tax:%s total:%s currency=%s",
        template_path or "unknown",
        len(html),
        len(items) if items else 0,
        quote.subtotal,
        quote.tax_total,
        quote.total,
        quote.currency,
    )
    if request.query_params.get("debug") == "1":
        return HTMLResponse(content=html)
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="WeasyPrint is not installed on the server. Install it to generate PDFs.",
        ) from exc
    _ensure_pydyf_compat()
    if request.query_params.get("nocss") == "1":
        html = re.sub(r"<style[\\s\\S]*?</style>", "", html, flags=re.IGNORECASE)
    html_doc = HTML(string=html, base_url=str(request.base_url))
    pdf_bytes = html_doc.write_pdf()
    logger.info(
        "quote_pdf_len=%s",
        len(pdf_bytes),
    )
    if request.query_params.get("save") == "1":
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                delete=False,
                suffix=".html",
                prefix=f"quote_{quote.id}_",
            ) as html_handle:
                html_handle.write(html)
                tmp_html = html_handle.name
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                suffix=".pdf",
                prefix=f"quote_{quote.id}_",
            ) as pdf_handle:
                pdf_handle.write(pdf_bytes)
                tmp_pdf = pdf_handle.name
            logger.info("quote_pdf_saved html=%s pdf=%s", tmp_html, tmp_pdf)
        except Exception:
            logger.exception("quote_pdf_save_failed")
    filename = f"quote_{quote.id}.pdf"
    disposition = "inline" if request.query_params.get("inline") == "1" else "attachment"
    return Response(
        pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


@router.get("/quotes/{quote_id}/preview", response_class=HTMLResponse)
def crm_quote_preview(request: Request, quote_id: str):
    extra = "&plain=1" if request.query_params.get("plain") == "1" else ""
    cache_bust = int(datetime.now(UTC).timestamp())
    pdf_url = f"/admin/crm/quotes/{quote_id}/pdf?inline=1{extra}&ts={cache_bust}"
    html = f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Quote PDF Preview</title>
  <style>
    html, body {{ height: 100%; margin: 0; }}
    .frame {{ width: 100%; height: 100vh; border: none; }}
  </style>
</head>
<body>
  <iframe class="frame" src="{pdf_url}" title="Quote PDF Preview"></iframe>
</body>
</html>
"""
    return HTMLResponse(content=html)


@router.post("/quotes", response_class=HTMLResponse)
def crm_quote_create(
    request: Request,
    lead_id: str | None = Form(None),
    contact_id: str | None = Form(None),
    tax_rate_id: str | None = Form(None),
    status: str | None = Form(None),
    project_type: str | None = Form(None),
    currency: str | None = Form(None),
    subtotal: str | None = Form(None),
    tax_total: str | None = Form(None),
    total: str | None = Form(None),
    expires_at: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: str | None = Form(None),
    item_description: list[str] = Form([]),
    item_quantity: list[str] = Form([]),
    item_unit_price: list[str] = Form([]),
    item_inventory_item_id: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    error = None
    quote: dict[str, str | bool] = {
        "lead_id": (lead_id or "").strip(),
        "contact_id": (contact_id or "").strip(),
        "tax_rate_id": (tax_rate_id or "").strip(),
        "status": (status or "").strip(),
        "project_type": (project_type or "").strip(),
        "currency": (currency or "").strip(),
        "subtotal": (subtotal or "").strip(),
        "tax_total": (tax_total or "").strip(),
        "total": (total or "").strip(),
        "expires_at": (expires_at or "").strip(),
        "notes": (notes or "").strip(),
        "is_active": is_active == "true",
    }
    quote_items = _collect_quote_item_inputs(
        item_description,
        item_quantity,
        item_unit_price,
        item_inventory_item_id,
    )
    try:
        lead_id_value = quote["lead_id"] if isinstance(quote["lead_id"], str) else ""
        contact_id_value = quote["contact_id"] if isinstance(quote["contact_id"], str) else ""
        tax_rate_id_value = quote["tax_rate_id"] if isinstance(quote["tax_rate_id"], str) else ""
        status_value = quote["status"] if isinstance(quote["status"], str) else ""
        project_type_value = quote["project_type"] if isinstance(quote["project_type"], str) else ""
        currency_value = quote["currency"] if isinstance(quote["currency"], str) else ""
        subtotal_value = quote["subtotal"] if isinstance(quote["subtotal"], str) else ""
        tax_total_value = quote["tax_total"] if isinstance(quote["tax_total"], str) else ""
        total_value = quote["total"] if isinstance(quote["total"], str) else ""
        expires_at_value = quote["expires_at"] if isinstance(quote["expires_at"], str) else ""
        notes_value = quote["notes"] if isinstance(quote["notes"], str) else ""
        parsed_items = _parse_quote_line_items(quote_items)
        subtotal_val = _parse_decimal(subtotal_value, "subtotal") or Decimal("0.00")
        tax_val = _parse_decimal(tax_total_value, "tax_total") or Decimal("0.00")
        if tax_rate_id_value:
            try:
                rate = billing_service.tax_rates.get(db, tax_rate_id_value)
                rate_value = Decimal(rate.rate or 0)
                if rate_value > 1:
                    rate_value = rate_value / Decimal("100")
                tax_val = subtotal_val * rate_value
            except Exception:
                raise ValueError("Invalid tax rate")
        total_val = _parse_decimal(total_value, "total") or Decimal("0.00")
        if tax_rate_id_value:
            total_val = subtotal_val + tax_val
        resolved_person_id = contact_id_value or None
        if not resolved_person_id and lead_id_value:
            try:
                lead_obj = crm_service.leads.get(db=db, lead_id=lead_id_value)
                resolved_person_id = str(lead_obj.person_id) if lead_obj.person_id else None
            except Exception:
                resolved_person_id = None
        if not resolved_person_id:
            raise ValueError("Select a contact or lead to create a quote.")
        try:
            status_enum = QuoteStatus(status_value) if status_value else QuoteStatus.draft
        except ValueError:
            status_enum = QuoteStatus.draft
        payload = QuoteCreate(
            lead_id=_coerce_uuid_optional(lead_id_value),
            person_id=coerce_uuid(resolved_person_id),
            status=status_enum,
            currency=currency_value or "NGN",
            subtotal=subtotal_val,
            tax_total=tax_val,
            total=total_val,
            expires_at=_parse_optional_datetime(expires_at_value),
            notes=notes_value or None,
            metadata_={"project_type": project_type_value} if project_type_value else None,
            is_active=bool(quote["is_active"]),
        )
        quote_obj = crm_service.quotes.create(db=db, payload=payload)
        for item in parsed_items:
            item_payload = QuoteLineItemCreate(
                quote_id=quote_obj.id,
                description=item["description"],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
                inventory_item_id=item["inventory_item_id"],
            )
            crm_service.quote_line_items.create(db=db, payload=item_payload)
        return RedirectResponse(url="/admin/crm/quotes", status_code=303)
    except (ValidationError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            error = exc.errors()[0]["msg"]
        else:
            error = str(exc)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    options = _load_crm_sales_options(db)
    tax_rates = billing_service.tax_rates.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    leads = crm_service.leads.list(
        db=db,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )
    inventory_items: list = []
    project_types = [item.value for item in ProjectType]
    context = _crm_base_context(request, db, "quotes")
    context.update(
        {
            "quote": quote,
            "quote_items": quote_items,
            "tax_rates": tax_rates,
            "quote_statuses": [item.value for item in QuoteStatus],
            "project_types": project_types,
            "leads": leads,
            "contacts": options["contacts"],
            "inventory_items": inventory_items,
            "form_title": "New Quote",
            "submit_label": "Create Quote",
            "action_url": "/admin/crm/quotes",
            "error": error,
        }
    )
    return templates.TemplateResponse("admin/crm/quote_form.html", context, status_code=400)


@router.get("/quotes/{quote_id}/edit", response_class=HTMLResponse)
def crm_quote_edit(request: Request, quote_id: str, db: Session = Depends(get_db)):
    quote_obj = crm_service.quotes.get(db=db, quote_id=quote_id)
    items = crm_service.quote_line_items.list(
        db=db,
        quote_id=quote_id,
        order_by="created_at",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    quote_items = [
        {
            "description": item.description or "",
            "quantity": str(item.quantity or Decimal("1.000")),
            "unit_price": str(item.unit_price or Decimal("0.00")),
            "inventory_item_id": str(item.inventory_item_id) if item.inventory_item_id else "",
        }
        for item in items
    ]
    metadata = quote_obj.metadata_ if isinstance(quote_obj.metadata_, dict) else {}
    quote = {
        "id": str(quote_obj.id),
        "lead_id": str(quote_obj.lead_id) if quote_obj.lead_id else "",
        "contact_id": str(quote_obj.contact_id) if quote_obj.contact_id else "",
        "status": quote_obj.status.value if quote_obj.status else "",
        "project_type": metadata.get("project_type", "") if metadata else "",
        "currency": quote_obj.currency or "",
        "subtotal": quote_obj.subtotal or Decimal("0.00"),
        "tax_total": quote_obj.tax_total or Decimal("0.00"),
        "total": quote_obj.total or Decimal("0.00"),
        "expires_at": quote_obj.expires_at.strftime("%Y-%m-%dT%H:%M") if quote_obj.expires_at else "",
        "notes": quote_obj.notes or "",
        "is_active": quote_obj.is_active,
    }
    options = _load_crm_sales_options(db)
    leads = crm_service.leads.list(
        db=db,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )
    inventory_items = []
    project_types = [item.value for item in ProjectType]
    context = _crm_base_context(request, db, "quotes")
    context.update(
        {
            "quote": quote,
            "quote_items": quote_items,
            "quote_statuses": [item.value for item in QuoteStatus],
            "project_types": project_types,
            "leads": leads,
            "contacts": options["contacts"],
            "inventory_items": inventory_items,
            "form_title": "Edit Quote",
            "submit_label": "Save Quote",
            "action_url": f"/admin/crm/quotes/{quote_id}/edit",
        }
    )
    return templates.TemplateResponse("admin/crm/quote_form.html", context)


@router.post("/quotes/{quote_id}/edit", response_class=HTMLResponse)
def crm_quote_update(
    request: Request,
    quote_id: str,
    lead_id: str | None = Form(None),
    contact_id: str | None = Form(None),
    status: str | None = Form(None),
    project_type: str | None = Form(None),
    currency: str | None = Form(None),
    subtotal: str | None = Form(None),
    tax_total: str | None = Form(None),
    total: str | None = Form(None),
    expires_at: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: str | None = Form(None),
    item_description: list[str] = Form([]),
    item_quantity: list[str] = Form([]),
    item_unit_price: list[str] = Form([]),
    item_inventory_item_id: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    error = None
    quote: dict[str, str | bool] = {
        "id": quote_id,
        "lead_id": (lead_id or "").strip(),
        "contact_id": (contact_id or "").strip(),
        "status": (status or "").strip(),
        "project_type": (project_type or "").strip(),
        "currency": (currency or "").strip(),
        "subtotal": (subtotal or "").strip(),
        "tax_total": (tax_total or "").strip(),
        "total": (total or "").strip(),
        "expires_at": (expires_at or "").strip(),
        "notes": (notes or "").strip(),
        "is_active": is_active == "true",
    }
    quote_items = _collect_quote_item_inputs(
        item_description,
        item_quantity,
        item_unit_price,
        item_inventory_item_id,
    )
    try:
        lead_id_value = quote["lead_id"] if isinstance(quote["lead_id"], str) else ""
        contact_id_value = quote["contact_id"] if isinstance(quote["contact_id"], str) else ""
        status_value = quote["status"] if isinstance(quote["status"], str) else ""
        project_type_value = quote["project_type"] if isinstance(quote["project_type"], str) else ""
        currency_value = quote["currency"] if isinstance(quote["currency"], str) else ""
        tax_total_value = quote["tax_total"] if isinstance(quote["tax_total"], str) else ""
        expires_at_value = quote["expires_at"] if isinstance(quote["expires_at"], str) else ""
        notes_value = quote["notes"] if isinstance(quote["notes"], str) else ""
        parsed_items = _parse_quote_line_items(quote_items)
        subtotal_from_items = sum((item["quantity"] * item["unit_price"] for item in parsed_items), Decimal("0.00"))
        tax_value = _parse_decimal(tax_total_value, "tax_total") or Decimal("0.00")
        total_from_items = subtotal_from_items + tax_value
        quote_obj = crm_service.quotes.get(db=db, quote_id=quote_id)
        resolved_person_id = contact_id_value or None
        if not resolved_person_id and lead_id_value:
            try:
                lead_obj = crm_service.leads.get(db=db, lead_id=lead_id_value)
                resolved_person_id = str(lead_obj.person_id) if lead_obj.person_id else None
            except Exception:
                resolved_person_id = None
        metadata = quote_obj.metadata_ if isinstance(quote_obj.metadata_, dict) else {}
        if project_type_value:
            metadata["project_type"] = project_type_value
        try:
            status_enum = QuoteStatus(status_value) if status_value else None
        except ValueError:
            status_enum = None
        person_id_value = coerce_uuid(resolved_person_id) if resolved_person_id else quote_obj.person_id
        if not person_id_value:
            raise ValueError("Quote must be linked to a person.")
        payload = QuoteUpdate(
            person_id=person_id_value,
            status=status_enum,
            currency=currency_value or None,
            subtotal=subtotal_from_items,
            tax_total=tax_value,
            total=total_from_items,
            expires_at=_parse_optional_datetime(expires_at_value),
            notes=notes_value or None,
            metadata_=metadata if metadata else None,
            is_active=bool(quote["is_active"]),
        )
        before = quote_obj
        updated = crm_service.quotes.update(db=db, quote_id=quote_id, payload=payload)
        existing_items = crm_service.quote_line_items.list(
            db=db,
            quote_id=quote_id,
            order_by="created_at",
            order_dir="asc",
            limit=500,
            offset=0,
        )
        for index, item in enumerate(parsed_items):
            if index < len(existing_items):
                crm_service.quote_line_items.update(
                    db=db,
                    item_id=str(existing_items[index].id),
                    payload=QuoteLineItemUpdate(
                        description=item["description"],
                        quantity=item["quantity"],
                        unit_price=item["unit_price"],
                        inventory_item_id=item["inventory_item_id"],
                    ),
                )
            else:
                crm_service.quote_line_items.create(
                    db=db,
                    payload=QuoteLineItemCreate(
                        quote_id=updated.id,
                        description=item["description"],
                        quantity=item["quantity"],
                        unit_price=item["unit_price"],
                        inventory_item_id=item["inventory_item_id"],
                    ),
                )
        for stale_item in existing_items[len(parsed_items) :]:
            db.delete(stale_item)
        db.commit()
        metadata_payload = build_changes_metadata(before, updated)
        from app.web.admin import get_current_user

        current_user = get_current_user(request)
        log_audit_event(
            db=db,
            request=request,
            action="update",
            entity_type="quote",
            entity_id=str(quote_id),
            actor_id=str(current_user.get("person_id")) if current_user else None,
            metadata=metadata_payload,
        )
        return RedirectResponse(url="/admin/crm/quotes", status_code=303)
    except (ValidationError, ValueError) as exc:
        db.rollback()
        error = str(exc)
    except Exception as exc:
        db.rollback()
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    options = _load_crm_sales_options(db)
    leads = crm_service.leads.list(
        db=db,
        pipeline_id=None,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )
    inventory_items: list = []
    project_types = [item.value for item in ProjectType]
    context = _crm_base_context(request, db, "quotes")
    context.update(
        {
            "quote": quote,
            "quote_items": quote_items,
            "quote_statuses": [item.value for item in QuoteStatus],
            "project_types": project_types,
            "leads": leads,
            "contacts": options["contacts"],
            "inventory_items": inventory_items,
            "form_title": "Edit Quote",
            "submit_label": "Save Quote",
            "action_url": f"/admin/crm/quotes/{quote_id}/edit",
            "error": error,
        }
    )
    return templates.TemplateResponse("admin/crm/quote_form.html", context, status_code=400)


@router.post("/quotes/{quote_id}/delete", response_class=HTMLResponse)
def crm_quote_delete(request: Request, quote_id: str, db: Session = Depends(get_db)):
    _require_admin_role(request)
    crm_service.quotes.delete(db=db, quote_id=quote_id)
    return RedirectResponse(url="/admin/crm/quotes", status_code=303)


@router.post("/quotes/bulk/status")
async def crm_quotes_bulk_status(
    request: Request,
    db: Session = Depends(get_db),
):
    """Bulk update quote status."""

    _require_admin_role(request)
    try:
        raw = await request.body()
        body = json.loads(raw)
    except Exception:
        logger.debug("Failed to parse bulk quote status body.", exc_info=True)
        body = {}
    quote_ids = body.get("quote_ids", [])
    new_status = body.get("status", "")
    if not quote_ids or not new_status:
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "Missing quote_ids or status"}, status_code=400)
    from app.schemas.crm import QuoteUpdate

    for quote_id in quote_ids:
        try:
            crm_service.quotes.update(db, quote_id, QuoteUpdate(status=new_status))
        except Exception:
            logger.debug("Failed to update quote status in bulk.", exc_info=True)
    from fastapi.responses import JSONResponse

    return JSONResponse({"success": True, "updated": len(quote_ids)})


@router.post("/quotes/bulk/delete")
async def crm_quotes_bulk_delete(
    request: Request,
    db: Session = Depends(get_db),
):
    """Bulk delete quotes."""

    _require_admin_role(request)
    try:
        raw = await request.body()
        body = json.loads(raw)
    except Exception:
        logger.debug("Failed to parse bulk quote delete body.", exc_info=True)
        body = {}
    quote_ids = body.get("quote_ids", [])
    if not quote_ids:
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "Missing quote_ids"}, status_code=400)
    deleted = 0
    for quote_id in quote_ids:
        try:
            crm_service.quotes.delete(db, quote_id)
            deleted += 1
        except Exception:
            logger.debug("Failed to delete quote in bulk.", exc_info=True)
    from fastapi.responses import JSONResponse

    return JSONResponse({"success": True, "deleted": deleted})


# ---------------------------------------------------------------------------
# Sales Dashboard and Pipeline Board Routes
# ---------------------------------------------------------------------------


@router.get("/sales", response_class=HTMLResponse)
def crm_sales_dashboard(
    request: Request,
    pipeline_id: str | None = None,
    period_days: int = Query(30, ge=7, le=365),
    db: Session = Depends(get_db),
):
    """Sales dashboard with metrics, charts, and leaderboard."""
    from app.services.crm import reports as reports_service

    # Get pipelines for filter dropdown
    pipelines = crm_service.pipelines.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=100,
        offset=0,
    )

    start_at = datetime.now(UTC) - timedelta(days=period_days)
    end_at = datetime.now(UTC)

    # Get pipeline metrics
    metrics = reports_service.sales_pipeline_metrics(
        db,
        pipeline_id=pipeline_id,
        start_at=start_at,
        end_at=end_at,
        owner_agent_id=None,
    )

    # Get forecast data
    forecast = reports_service.sales_forecast(
        db,
        pipeline_id=pipeline_id,
        months_ahead=6,
    )

    # Get agent leaderboard
    agent_performance = reports_service.agent_sales_performance(
        db,
        start_at=start_at,
        end_at=end_at,
        pipeline_id=pipeline_id,
    )

    # Get recent leads
    recent_leads = crm_service.leads.list(
        db=db,
        pipeline_id=pipeline_id,
        stage_id=None,
        owner_agent_id=None,
        status=None,
        is_active=True,
        order_by="updated_at",
        order_dir="desc",
        limit=10,
        offset=0,
    )

    # Build person map for recent leads
    person_ids = [lead.person_id for lead in recent_leads if lead.person_id]
    persons = db.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
    person_map = {str(p.id): p for p in persons}

    context = _crm_base_context(request, db, "sales")
    context.update(
        {
            "pipelines": pipelines,
            "selected_pipeline_id": pipeline_id or "",
            "selected_period_days": period_days,
            "metrics": metrics,
            "forecast": forecast,
            "agent_performance": agent_performance[:10],  # Top 10
            "recent_leads": recent_leads,
            "person_map": person_map,
        }
    )
    return templates.TemplateResponse("admin/crm/sales_dashboard.html", context)


@router.get("/sales/pipeline", response_class=HTMLResponse)
def crm_sales_pipeline(
    request: Request,
    pipeline_id: str | None = None,
    db: Session = Depends(get_db),
):
    """Sales pipeline kanban board."""
    # Get pipelines for filter dropdown
    pipelines = crm_service.pipelines.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=100,
        offset=0,
    )

    # Select first pipeline if none specified
    if not pipeline_id and pipelines:
        pipeline_id = str(pipelines[0].id)

    context = _crm_base_context(request, db, "sales")
    context.update(
        {
            "pipelines": pipelines,
            "selected_pipeline_id": pipeline_id or "",
        }
    )
    return templates.TemplateResponse("admin/crm/sales_pipeline.html", context)


# ---------------------------------------------------------------------------
# Pipeline Settings
# ---------------------------------------------------------------------------


@router.get("/settings/pipelines/new", response_class=HTMLResponse)
def crm_pipeline_new(
    request: Request,
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    pipeline = {
        "name": "",
        "is_active": True,
        "create_default_stages": True,
    }
    context = _crm_base_context(request, db, "sales")
    context.update(
        {
            "pipeline": pipeline,
            "form_title": "New Pipeline",
            "submit_label": "Create Pipeline",
            "action_url": "/admin/crm/settings/pipelines",
            "error": None,
        }
    )
    return templates.TemplateResponse("admin/crm/pipeline_form.html", context)


@router.post("/settings/pipelines", response_class=HTMLResponse)
def crm_pipeline_create(
    request: Request,
    name: str | None = Form(None),
    is_active: str | None = Form(None),
    create_default_stages: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    error = None
    pipeline_data: dict[str, str | bool] = {
        "name": (name or "").strip(),
        "is_active": _as_bool(is_active) if is_active is not None else True,
        "create_default_stages": _as_bool(create_default_stages),
    }
    try:
        if not pipeline_data["name"]:
            raise ValueError("Pipeline name is required.")
        payload = PipelineCreate(
            name=str(pipeline_data["name"]),
            is_active=bool(pipeline_data["is_active"]),
        )
        pipeline = crm_service.pipelines.create(db=db, payload=payload)
        if pipeline_data["create_default_stages"]:
            for index, stage in enumerate(_DEFAULT_PIPELINE_STAGES):
                probability_value = stage.get("probability")
                default_probability = int(probability_value) if isinstance(probability_value, int | str) else 0
                stage_payload = PipelineStageCreate(
                    pipeline_id=pipeline.id,
                    name=str(stage["name"]),
                    order_index=index,
                    default_probability=default_probability,
                    is_active=True,
                )
                crm_service.pipeline_stages.create(db=db, payload=stage_payload)
        return RedirectResponse(
            url=f"/admin/crm/sales/pipeline?pipeline_id={pipeline.id}",
            status_code=303,
        )
    except (ValidationError, ValueError) as exc:
        error = str(exc)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    context = _crm_base_context(request, db, "sales")
    context.update(
        {
            "pipeline": pipeline_data,
            "form_title": "New Pipeline",
            "submit_label": "Create Pipeline",
            "action_url": "/admin/crm/settings/pipelines",
            "error": error,
        }
    )
    return templates.TemplateResponse("admin/crm/pipeline_form.html", context)


@router.get("/settings/pipelines", response_class=HTMLResponse)
def crm_pipeline_settings(
    request: Request,
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    pipelines = db.query(Pipeline).order_by(Pipeline.is_active.desc(), Pipeline.created_at.desc()).limit(200).all()
    stages = (
        db.query(PipelineStage)
        .order_by(PipelineStage.pipeline_id.asc(), PipelineStage.order_index.asc(), PipelineStage.created_at.asc())
        .limit(1000)
        .all()
    )
    stage_map: dict[str, list[PipelineStage]] = {}
    for stage in stages:
        stage_map.setdefault(str(stage.pipeline_id), []).append(stage)

    bulk_result = request.query_params.get("bulk_result", "").strip()
    bulk_count = request.query_params.get("bulk_count", "").strip()

    context = _crm_base_context(request, db, "sales")
    context.update(
        {
            "pipelines": pipelines,
            "stage_map": stage_map,
            "bulk_result": bulk_result,
            "bulk_count": bulk_count,
            "default_pipeline_stages": _DEFAULT_PIPELINE_STAGES,
        }
    )
    return templates.TemplateResponse("admin/crm/pipeline_settings.html", context)


@router.get("/settings/pipelines/{pipeline_id}/edit", response_class=HTMLResponse)
def crm_pipeline_edit(
    request: Request,
    pipeline_id: str,
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    pipeline = crm_service.pipelines.get(db, pipeline_id)
    context = _crm_base_context(request, db, "sales")
    context.update(
        {
            "pipeline": pipeline,
            "form_title": "Edit Pipeline",
            "submit_label": "Update Pipeline",
            "action_url": f"/admin/crm/settings/pipelines/{pipeline_id}",
            "error": None,
        }
    )
    return templates.TemplateResponse("admin/crm/pipeline_form.html", context)


@router.post("/settings/pipelines/{pipeline_id}", response_class=HTMLResponse)
def crm_pipeline_update(
    request: Request,
    pipeline_id: str,
    name: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    try:
        payload = PipelineUpdate(
            name=(name or "").strip() or None,
            is_active=_as_bool(is_active) if is_active is not None else None,
        )
        crm_service.pipelines.update(db=db, pipeline_id=pipeline_id, payload=payload)
        return RedirectResponse(url="/admin/crm/settings/pipelines", status_code=303)
    except (ValidationError, ValueError) as exc:
        error = str(exc)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    context = _crm_base_context(request, db, "sales")
    context.update(
        {
            "pipeline": {
                "id": pipeline_id,
                "name": (name or "").strip(),
                "is_active": _as_bool(is_active) if is_active is not None else True,
                "create_default_stages": False,
            },
            "form_title": "Edit Pipeline",
            "submit_label": "Update Pipeline",
            "action_url": f"/admin/crm/settings/pipelines/{pipeline_id}",
            "error": error,
        }
    )
    return templates.TemplateResponse("admin/crm/pipeline_form.html", context, status_code=400)


@router.post("/settings/pipelines/{pipeline_id}/delete")
def crm_pipeline_delete(
    request: Request,
    pipeline_id: str,
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    crm_service.pipelines.delete(db, pipeline_id)
    return RedirectResponse(url="/admin/crm/settings/pipelines", status_code=303)


@router.post("/settings/pipelines/{pipeline_id}/stages")
def crm_pipeline_stage_create(
    request: Request,
    pipeline_id: str,
    name: str = Form(...),
    order_index: int = Form(0),
    default_probability: int = Form(50),
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    payload = PipelineStageCreate(
        pipeline_id=coerce_uuid(pipeline_id),
        name=name.strip(),
        order_index=order_index,
        default_probability=default_probability,
        is_active=True,
    )
    crm_service.pipeline_stages.create(db=db, payload=payload)
    return RedirectResponse(url="/admin/crm/settings/pipelines", status_code=303)


@router.post("/settings/pipelines/stages/{stage_id}")
def crm_pipeline_stage_update(
    request: Request,
    stage_id: str,
    name: str = Form(...),
    order_index: int = Form(0),
    default_probability: int = Form(50),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    payload = PipelineStageUpdate(
        name=name.strip(),
        order_index=order_index,
        default_probability=default_probability,
        is_active=_as_bool(is_active) if is_active is not None else False,
    )
    crm_service.pipeline_stages.update(db=db, stage_id=stage_id, payload=payload)
    return RedirectResponse(url="/admin/crm/settings/pipelines", status_code=303)


@router.post("/settings/pipelines/stages/{stage_id}/delete")
def crm_pipeline_stage_delete(
    request: Request,
    stage_id: str,
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    payload = PipelineStageUpdate(is_active=False)
    crm_service.pipeline_stages.update(db=db, stage_id=stage_id, payload=payload)
    return RedirectResponse(url="/admin/crm/settings/pipelines", status_code=303)


@router.post("/settings/pipelines/{pipeline_id}/bulk-assign-leads")
def crm_pipeline_bulk_assign_leads(
    request: Request,
    pipeline_id: str,
    stage_id: str | None = Form(None),
    scope: str = Form("unassigned"),
    db: Session = Depends(get_db),
):
    if not _can_write_sales(request):
        return HTMLResponse("<div class='p-6 text-center text-slate-500'>Forbidden</div>", status_code=403)
    count = crm_service.leads.bulk_assign_pipeline(
        db,
        pipeline_id=pipeline_id,
        stage_id=(stage_id or "").strip() or None,
        scope=scope,
    )
    return RedirectResponse(
        url=f"/admin/crm/settings/pipelines?bulk_result=ok&bulk_count={count}",
        status_code=303,
    )


# --------------------------------------------------------------------------
# Chat Widget Management
# --------------------------------------------------------------------------


@router.get("/widget", response_class=HTMLResponse)
def crm_widget_list(
    request: Request,
    db: Session = Depends(get_db),
):
    """List all chat widget configurations."""
    from app.models.crm.chat_widget import ChatWidgetConfig

    widgets = db.query(ChatWidgetConfig).order_by(ChatWidgetConfig.created_at.desc()).all()

    context = _crm_base_context(request, db, "widget")
    context.update(
        {
            "widgets": widgets,
            "success_message": request.query_params.get("success"),
            "error_message": request.query_params.get("error"),
        }
    )
    return templates.TemplateResponse("admin/crm/widget_list.html", context)


@router.get("/widget/new", response_class=HTMLResponse)
def crm_widget_new(
    request: Request,
    db: Session = Depends(get_db),
):
    """Show widget creation form."""
    context = _crm_base_context(request, db, "widget")
    context.update(
        {
            "widget": None,
        }
    )
    return templates.TemplateResponse("admin/crm/widget_detail.html", context)


@router.post("/widget", response_class=HTMLResponse)
async def crm_widget_create(
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a new widget configuration."""
    from app.schemas.crm.chat_widget import ChatWidgetConfigCreate
    from app.services.crm.chat_widget import widget_configs

    form = await request.form()

    try:
        prechat_fields_raw = _form_str(form, "prechat_fields_json")
        prechat_fields = None
        if prechat_fields_raw.strip():
            try:
                import json

                prechat_fields = json.loads(prechat_fields_raw)
            except Exception as exc:
                raise ValueError("Invalid pre-chat field configuration") from exc
        # Parse allowed domains
        allowed_domains_str = _form_str(form, "allowed_domains")
        allowed_domains = (
            [d.strip() for d in allowed_domains_str.split(",") if d.strip()] if allowed_domains_str else []
        )

        payload = ChatWidgetConfigCreate(
            name=_form_str(form, "name"),
            allowed_domains=allowed_domains,
            primary_color=_form_str(form, "primary_color", "#3B82F6"),
            bubble_position=_coerce_bubble_position(_form_str_opt(form, "bubble_position")),
            widget_title=_form_str(form, "widget_title", "Chat with us"),
            welcome_message=_form_str_opt(form, "welcome_message"),
            placeholder_text=_form_str(form, "placeholder_text", "Type a message..."),
            rate_limit_messages_per_minute=_as_int(_form_str_opt(form, "rate_limit_messages_per_minute"), 10) or 10,
            rate_limit_sessions_per_ip=_as_int(_form_str_opt(form, "rate_limit_sessions_per_ip"), 5) or 5,
            prechat_form_enabled="prechat_form_enabled" in form,
            prechat_fields=prechat_fields,
        )

        widget = widget_configs.create(db, payload)
        return RedirectResponse(
            url=f"/admin/crm/widget/{widget.id}?success=Widget created successfully",
            status_code=303,
        )
    except Exception as e:
        context = _crm_base_context(request, db, "widget")
        context.update(
            {
                "widget": None,
                "error_message": str(e),
            }
        )
        return templates.TemplateResponse("admin/crm/widget_detail.html", context)


@router.get("/widget/{widget_id}", response_class=HTMLResponse)
def crm_widget_detail(
    widget_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Widget detail with settings and embed code."""
    from app.models.crm.chat_widget import WidgetVisitorSession
    from app.services.crm.chat_widget import widget_configs

    widget = widget_configs.get(db, widget_id)
    if not widget:
        return RedirectResponse(
            url="/admin/crm/widget?error=Widget not found",
            status_code=303,
        )

    # Get base URL for embed code
    host = request.headers.get("host", "localhost:8000")
    scheme = request.headers.get("x-forwarded-proto", "http")
    base_url = f"{scheme}://{host}"

    embed_code = widget_configs.generate_embed_code(widget, base_url)

    # Get stats
    session_count = db.query(WidgetVisitorSession).filter(WidgetVisitorSession.widget_config_id == widget.id).count()
    conversation_count = (
        db.query(WidgetVisitorSession)
        .filter(WidgetVisitorSession.widget_config_id == widget.id)
        .filter(WidgetVisitorSession.conversation_id.isnot(None))
        .count()
    )

    context = _crm_base_context(request, db, "widget")
    context.update(
        {
            "widget": widget,
            "embed_code": embed_code,
            "session_count": session_count,
            "conversation_count": conversation_count,
            "success_message": request.query_params.get("success"),
            "error_message": request.query_params.get("error"),
        }
    )
    return templates.TemplateResponse("admin/crm/widget_detail.html", context)


@router.post("/widget/{widget_id}", response_class=HTMLResponse)
async def crm_widget_update(
    widget_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Update widget configuration."""
    from app.schemas.crm.chat_widget import ChatWidgetConfigUpdate
    from app.services.crm.chat_widget import widget_configs

    form = await request.form()

    try:
        prechat_fields_raw = _form_str(form, "prechat_fields_json")
        prechat_fields = None
        if prechat_fields_raw.strip():
            try:
                import json

                prechat_fields = json.loads(prechat_fields_raw)
            except Exception as exc:
                raise ValueError("Invalid pre-chat field configuration") from exc
        # Parse allowed domains
        allowed_domains_str = _form_str(form, "allowed_domains")
        allowed_domains = (
            [d.strip() for d in allowed_domains_str.split(",") if d.strip()] if allowed_domains_str else []
        )

        payload = ChatWidgetConfigUpdate(
            name=_form_str_opt(form, "name"),
            allowed_domains=allowed_domains,
            primary_color=_form_str_opt(form, "primary_color"),
            bubble_position=(
                _coerce_bubble_position(bubble_position_value)
                if (bubble_position_value := _form_str_opt(form, "bubble_position"))
                else None
            ),
            widget_title=_form_str_opt(form, "widget_title"),
            welcome_message=_form_str_opt(form, "welcome_message"),
            placeholder_text=_form_str_opt(form, "placeholder_text"),
            rate_limit_messages_per_minute=_as_int(_form_str_opt(form, "rate_limit_messages_per_minute"), 10) or 10,
            rate_limit_sessions_per_ip=_as_int(_form_str_opt(form, "rate_limit_sessions_per_ip"), 5) or 5,
            is_active="is_active" in form,
            prechat_form_enabled="prechat_form_enabled" in form,
            prechat_fields=prechat_fields,
        )

        widget = widget_configs.update(db, widget_id, payload)
        if not widget:
            return RedirectResponse(
                url="/admin/crm/widget?error=Widget not found",
                status_code=303,
            )

        return RedirectResponse(
            url=f"/admin/crm/widget/{widget_id}?success=Widget updated successfully",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/admin/crm/widget/{widget_id}?error={e!s}",
            status_code=303,
        )


@router.post("/widget/{widget_id}/delete", response_class=HTMLResponse)
def crm_widget_delete(
    widget_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Delete a widget configuration."""
    from app.services.crm.chat_widget import widget_configs

    if widget_configs.delete(db, widget_id):
        return RedirectResponse(
            url="/admin/crm/widget?success=Widget deleted successfully",
            status_code=303,
        )
    else:
        return RedirectResponse(
            url="/admin/crm/widget?error=Widget not found",
            status_code=303,
        )

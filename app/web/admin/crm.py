"""CRM web routes - Omni-channel Inbox."""

import base64
import mimetypes
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urljoin, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.csrf import get_csrf_token
from app.db import SessionLocal
from app.logging import get_logger
from app.models.crm.conversation import Message
from app.models.crm.sales import Lead, PipelineStage, Quote
from app.models.person import Person
from app.models.projects import ProjectType
from app.models.subscriber import Organization, Subscriber
from app.schemas.crm.conversation import (
    MessageAttachmentCreate,
)
from app.services import crm as crm_service
from app.services import person as person_service
from app.services.common import coerce_uuid
from app.services.crm.conversations.service import MessageAttachments as MessageAttachmentsService
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
router.include_router(crm_leads_router)
router.include_router(crm_quotes_router)
router.include_router(crm_sales_router)


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

"""CRM web routes - Omni-channel Inbox."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import html
import json
import uuid
from html.parser import HTMLParser
from html import escape as html_escape
import re
from urllib.parse import quote, urlparse, urlencode
import os
from uuid import UUID

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, selectinload, aliased

from app.db import SessionLocal
from app.models.connector import ConnectorConfig, ConnectorType
from app.models.domain_settings import SettingDomain
from app.models.crm.sales import Lead, Quote
from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.team import CrmAgent, CrmAgentTeam
from app.models.crm.enums import (
    AgentPresenceStatus,
    ChannelType,
    LeadStatus,
    MessageDirection,
    MessageStatus,
    QuoteStatus,
)
from app.models.integration import (
    IntegrationRun,
    IntegrationRunStatus,
    IntegrationTarget,
    IntegrationTargetType,
)
from app.models.person import Person, PersonChannel, ChannelType as PersonChannelType, PartyStatus
from app.models.projects import Project, ProjectStatus, ProjectTask, ProjectType
from app.models.subscriber import Organization, Subscriber, SubscriberStatus


from app.models.tickets import Ticket
from app.schemas.connector import ConnectorConfigUpdate
from app.schemas.crm.contact import ContactCreate, ContactUpdate
from app.schemas.person import PartyStatusEnum
from app.schemas.crm.conversation import ConversationAssignmentCreate
from app.schemas.crm.conversation import (
    ConversationCreate,
    MessageAttachmentCreate,
    MessageCreate,
)
from app.schemas.crm.inbox import InboxSendRequest
from app.schemas.crm.sales import (
    LeadCreate,
    LeadUpdate,
    PipelineCreate,
    PipelineStageCreate,
    QuoteCreate,
    QuoteLineItemCreate,
    QuoteUpdate,
)
from app.schemas.integration import IntegrationTargetUpdate
from app.services.person import InvalidTransitionError, People
from app.services.subscriber import subscriber as subscriber_service


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
    {"name": "New", "probability": 10},
    {"name": "Qualified", "probability": 25},
    {"name": "Proposal", "probability": 50},
    {"name": "Negotiation", "probability": 75},
    {"name": "Closed Won", "probability": 100},
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


class PrivateNoteCreate(BaseModel):
    """Payload for creating a private note in an inbox conversation."""

    body: str
    requested_visibility: Literal["author", "team", "admins"] | None = None


class PrivateNoteRequest(BaseModel):
    """Payload for creating a private note via JSON."""

    body: str
    visibility: Literal["author", "team", "admins"] | None = None
    attachments: list[dict] | None = None


from app.services import connector as connector_service
from app.services import crm as crm_service
from app.services import integration as integration_service
from app.services import person as person_service
from app.services import inventory as inventory_service
from app.config import settings
from app.services.audit_helpers import (
    build_changes_metadata,
    log_audit_event,
    recent_activity_for_paths,
)
from app.services.crm.inbox.settings_admin import (
    create_agent,
    create_agent_team,
    create_team,
    update_notification_settings,
)
from app.services.crm.inbox.settings_view import build_inbox_settings_context
from app.services.settings_spec import resolve_value

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
                self.version = version if isinstance(version, (bytes, bytearray)) else str(version).encode()
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


from app.services.common import coerce_uuid
from app.services.crm import contact as contact_service
from app.services.crm import conversation as conversation_service
from app.services.crm.conversations.service import MessageAttachments as MessageAttachmentsService
from app.services.crm import inbox as inbox_service
from app.services import meta_oauth
from app.csrf import get_csrf_token
from app.logging import get_logger

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


def _get_initials(name: str | None) -> str:
    """Generate initials from a name."""
    if not name:
        return "?"
    parts = name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[0:2].upper() if len(name) >= 2 else name[0].upper()


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
        inventory_item_id = (
            inventory_item_ids[idx] if idx < len(inventory_item_ids) else ""
        ).strip()
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
        return parsed.replace(tzinfo=timezone.utc)
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


def _format_project_summary(
    quote: Quote,
    lead: Lead | None,
    contact: Person | None,
    company_name: str | None,
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
        "Should you have any questions or wish to proceed, please contact us on sales@dotmac.ng.",
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
    html = template.render(
        {
            "request": request,
            "quote": quote,
            "items": items,
            "lead": lead,
            "contact": contact,
            "quote_name": quote_name or "",
            "branding": branding or {},
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
    organizations = (
        db.query(Organization)
        .order_by(Organization.name.asc())
        .limit(500)
        .all()
    )
    return {"people": people, "organizations": organizations}


def _load_crm_agent_team_options(db: Session) -> dict:
    """Get agents and teams for assignment dropdowns (uses service layer)."""
    return crm_service.get_agent_team_options(db)


class _MessageHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._allowed_tags = {
            "p",
            "br",
            "strong",
            "b",
            "em",
            "i",
            "u",
            "ul",
            "ol",
            "li",
            "table",
            "thead",
            "tbody",
            "tr",
            "td",
            "th",
            "span",
            "div",
            "a",
        }
        self._self_closing = {"br"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self._allowed_tags:
            return
        safe_attrs: list[str] = []
        if tag == "a":
            href = None
            target = None
            for key, value in attrs:
                if key == "href" and value:
                    href = value
                if key == "target" and value:
                    target = value
            if href and _is_safe_url(href):
                safe_attrs.append(f'href="{html.escape(href, quote=True)}"')
            if target:
                safe_attrs.append(f'target="{html.escape(target, quote=True)}"')
            safe_attrs.append('rel="noreferrer noopener"')
        elif tag in {"td", "th"}:
            for key, value in attrs:
                if key in {"colspan", "rowspan"} and value:
                    safe_attrs.append(f'{key}="{html.escape(value, quote=True)}"')
        attr_text = f" {' '.join(safe_attrs)}" if safe_attrs else ""
        if tag in self._self_closing:
            self._parts.append(f"<{tag}{attr_text} />")
        else:
            self._parts.append(f"<{tag}{attr_text}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._allowed_tags and tag not in self._self_closing:
            self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not data:
            return
        escaped = html.escape(data)
        if "\n" in escaped:
            escaped = "<br />".join(escaped.splitlines())
        self._parts.append(escaped)

    def handle_entityref(self, name: str) -> None:
        self._parts.append(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self._parts.append(html.unescape(f"&#{name};"))

    def get_html(self) -> str:
        return "".join(self._parts).strip()


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme in {"http", "https", "mailto", "tel"}:
        return True
    return parsed.scheme == ""


def _sanitize_message_html(value: str) -> str:
    if not value:
        return ""
    sanitizer = _MessageHTMLSanitizer()
    sanitizer.feed(value)
    sanitizer.close()
    return sanitizer.get_html()


def _normalize_cid(value: str | None) -> str | None:
    if not value:
        return None
    cid = value.strip()
    if cid.startswith("cid:"):
        cid = cid[4:]
    if cid.startswith("<") and cid.endswith(">"):
        cid = cid[1:-1]
    return cid.strip().lower() or None


def _replace_cid_images(html_body: str, attachments: list[dict]) -> str:
    if not html_body:
        return html_body
    cid_map: dict[str, str] = {}
    for att in attachments:
        cid = _normalize_cid(att.get("content_id"))
        url = att.get("url")
        if not cid or not url:
            continue
        mime_type = att.get("mime_type") or ""
        if not str(mime_type).startswith("image/"):
            continue
        cid_map[cid] = url
    if not cid_map:
        return html_body

    def _swap(match: re.Match) -> str:
        raw = match.group(1) or ""
        key = _normalize_cid(raw)
        if not key or key not in cid_map:
            return match.group(0)
        return cid_map[key]

    return re.sub(r"cid:([^\"'\\s>]+)", _swap, html_body, flags=re.IGNORECASE)


def _coerce_metadata(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _safe_log_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=True, default=str, sort_keys=True)


def _format_conversation_for_template(
    conv: Conversation,
    db: Session,
    latest_message: dict | Message | None = None,
    unread_count: int | None = None,
    include_inbox_label: bool = False,
) -> dict:
    """Transform a Conversation model into template-friendly dict."""
    contact = conv.contact

    if latest_message is None:
        from app.services.crm.conversation import get_latest_message
        latest_message = get_latest_message(db, str(conv.id))

    if unread_count is None:
        from app.services.crm.conversation import get_unread_count
        unread_count = get_unread_count(db, str(conv.id))

    channel = "email"
    channel_target_id = None
    if latest_message:
        channel_type = (
            latest_message.get("channel_type")
            if isinstance(latest_message, dict)
            else latest_message.channel_type
        )
        if channel_type:
            channel = channel_type.value
        if isinstance(latest_message, dict):
            channel_target_id = latest_message.get("channel_target_id")
        else:
            channel_target_id = getattr(latest_message, "channel_target_id", None)
    elif contact and contact.channels:
        channel = contact.channels[0].channel_type.value

    assigned_to = None
    assigned_team = None
    assigned_agent_id = None
    assigned_agent_name = None
    if conv.assignments:
        active_assignment = next((a for a in conv.assignments if a.is_active), None)
        if active_assignment:
            if active_assignment.agent_id:
                assigned_agent_id = str(active_assignment.agent_id)
                assigned_agent_name = "another agent"
            if active_assignment.agent:
                agent = active_assignment.agent
                if agent.person_id:
                    person = db.get(Person, agent.person_id)
                    if person:
                        full_name = person.display_name or " ".join(
                            part for part in [person.first_name, person.last_name] if part
                        ).strip()
                        assigned_to = {
                            "name": full_name or "Agent",
                            "initials": _get_initials(full_name or "Agent"),
                        }
                        assigned_agent_id = str(agent.id)
                        assigned_agent_name = full_name or "Agent"
            if not assigned_to and active_assignment.team:
                team = active_assignment.team
                team_name = team.name or "Team"
                assigned_team = {
                    "name": team_name,
                    "initials": _get_initials(team_name),
                }

    company = None
    if contact and contact.organization_id:
        org = db.get(Organization, contact.organization_id)
        if org:
            company = org.name

    preview = "No messages yet"
    if latest_message:
        if isinstance(latest_message, dict):
            body = latest_message.get("body")
            metadata = latest_message.get("metadata")
            message_type = latest_message.get("message_type")
            has_attachments = bool(latest_message.get("has_attachments"))
        else:
            body = latest_message.body
            metadata = latest_message.metadata_ if isinstance(latest_message.metadata_, dict) else None
            message_type = metadata.get("type") if metadata else None
            has_attachments = bool(getattr(latest_message, "attachments", None))

        body_text = body.strip() if isinstance(body, str) else ""
        if body_text in {"[reaction message]", "[location message]"}:
            body_text = ""
        message_type_value = message_type.lower() if isinstance(message_type, str) else None

        if message_type_value == "location":
            location_label = None
            if isinstance(metadata, dict):
                for key in ("address", "name", "label"):
                    value = metadata.get(key)
                    if isinstance(value, str) and value.strip():
                        location_label = value.strip()
                        break
                if not location_label:
                    loc = metadata.get("location")
                    if not loc:
                        raw = metadata.get("raw")
                        if isinstance(raw, dict):
                            raw_messages = raw.get("messages")
                            if isinstance(raw_messages, list) and raw_messages:
                                first_msg = raw_messages[0]
                                if isinstance(first_msg, dict):
                                    loc = first_msg.get("location")
                    if isinstance(loc, dict):
                        for key in ("address", "name", "label"):
                            value = loc.get(key)
                            if isinstance(value, str) and value.strip():
                                location_label = value.strip()
                                break
                if not location_label:
                    lat = metadata.get("latitude") or metadata.get("lat")
                    lng = metadata.get("longitude") or metadata.get("lng") or metadata.get("lon")
                    if lat is not None and lng is not None:
                        location_label = f"({lat}, {lng})"
                if not location_label and isinstance(loc, dict):
                    lat = loc.get("latitude") or loc.get("lat")
                    lng = loc.get("longitude") or loc.get("lng") or loc.get("lon")
                    if lat is not None and lng is not None:
                        location_label = f"({lat}, {lng})"
            if location_label:
                preview = f"📍 Location: {location_label}"
            else:
                preview = "📍 Location shared"
            if len(preview) > 100:
                preview = preview[:97] + "..."
        elif message_type_value == "reaction":
            reaction_emoji = None
            if isinstance(metadata, dict):
                reaction_emoji = metadata.get("emoji")
                if not reaction_emoji:
                    raw = metadata.get("raw")
                    if isinstance(raw, dict):
                        raw_messages = raw.get("messages")
                        if isinstance(raw_messages, list) and raw_messages:
                            first_msg = raw_messages[0]
                            if isinstance(first_msg, dict):
                                reaction = first_msg.get("reaction")
                                if isinstance(reaction, dict):
                                    reaction_emoji = reaction.get("emoji")
            if isinstance(reaction_emoji, str) and reaction_emoji.strip():
                preview = f"Reaction {reaction_emoji.strip()}"
            else:
                preview = "Reaction received"
        elif has_attachments:
            preview = "Attachment (Image/File)"
        elif body_text:
            preview = body_text[:100] + "..." if len(body_text) > 100 else body_text
        else:
            preview = "New message received"
    latest_message_at = None
    if isinstance(latest_message, dict):
        latest_message_at = latest_message.get("last_message_at")
    elif latest_message:
        latest_message_at = (
            latest_message.received_at
            or latest_message.sent_at
            or latest_message.created_at
        )

    inbox_label = None
    if include_inbox_label and channel_target_id:
        if isinstance(latest_message, dict):
            inbox_label = latest_message.get("channel_target_name")
        if not inbox_label:
            try:
                target = db.get(IntegrationTarget, coerce_uuid(channel_target_id))
            except Exception:
                target = None
            if target and target.connector_config:
                inbox_label = target.name or target.connector_config.name
    if include_inbox_label and not inbox_label:
        channel_labels = {
            "email": "Email Inbox",
            "whatsapp": "WhatsApp Inbox",
            "facebook_messenger": "Facebook Inbox",
            "instagram_dm": "Instagram Inbox",
            "sms": "SMS Inbox",
            "telegram": "Telegram Inbox",
            "webchat": "Webchat Inbox",
            "phone": "Phone Inbox",
        }
        inbox_label = channel_labels.get(channel, "Inbox")

    # For WhatsApp/phone channels, prefer phone number over email as fallback
    if contact:
        phone_value = contact.phone
        if phone_value and channel in ("whatsapp", "sms", "phone") and not phone_value.startswith("+"):
            phone_value = f"+{phone_value}"
        if channel in ("whatsapp", "sms", "phone"):
            contact_name = contact.display_name or phone_value or contact.email or "Unknown"
        else:
            contact_name = contact.display_name or contact.email or phone_value or "Unknown"
        contact_initials = _get_initials(contact_name)
    else:
        contact_name = "Unknown"
        contact_initials = "?"

    # Get splynx_id from contact metadata if available
    splynx_id = None
    if contact and contact.metadata_:
        splynx_id = contact.metadata_.get("splynx_id")

    return {
        "id": str(conv.id),
        "contact": {
            "id": str(contact.id) if contact else "",
            "name": contact_name,
            "email": contact.email if contact else "",
            "phone": phone_value if contact else "",
            "avatar_initials": contact_initials,
            "company": company,
            "splynx_id": splynx_id,
        },
        "channel": channel,
        "status": conv.status.value if conv.status else "open",
        "subject": conv.subject,
        "preview": preview,
        "unread_count": unread_count or 0,
        "last_message_at": conv.last_message_at or latest_message_at or conv.updated_at,
        "assigned_to": assigned_to,
        "assigned_team": assigned_team,
        "assigned_agent_id": assigned_agent_id,
        "assigned_agent_name": assigned_agent_name,
        "inbox": {
            "id": str(channel_target_id) if channel_target_id else None,
            "label": inbox_label,
        },
    }


def _format_message_for_template(msg: Message, db: Session) -> dict:
    """Transform a Message model into template-friendly dict."""
    sender_name = "Unknown"
    sender_initials = "?"

    if msg.direction == MessageDirection.internal:
        if msg.author_id:
            person = db.get(Person, msg.author_id)
            if person:
                full_name = person.display_name or " ".join(
                    part for part in [person.first_name, person.last_name] if part
                ).strip()
                sender_name = full_name or "Internal Note"
                sender_initials = _get_initials(sender_name)
            else:
                sender_name = "Internal Note"
                sender_initials = "IN"
        else:
            sender_name = "Internal Note"
            sender_initials = "IN"
    elif msg.direction == MessageDirection.outbound:
        if msg.author_id:
            person = db.get(Person, msg.author_id)
            if person:
                full_name = person.display_name or " ".join(
                    part for part in [person.first_name, person.last_name] if part
                ).strip()
                sender_name = full_name or "Agent"
                sender_initials = _get_initials(sender_name)
        else:
            sender_name = "Agent"
            sender_initials = "AG"
    else:
        conv = msg.conversation
        if conv and conv.contact:
            sender_name = conv.contact.display_name or conv.contact.email or "Contact"
            sender_initials = _get_initials(sender_name)

    attachments = []
    for attachment in msg.attachments or []:
        metadata = attachment.metadata_ or {}
        content_base64 = metadata.get("content_base64")
        content_id = metadata.get("content_id")
        url = attachment.external_url
        if not url and content_base64 and attachment.mime_type:
            url = f"data:{attachment.mime_type};base64,{content_base64}"
        attachments.append(
            {
                "id": str(attachment.id),
                "file_name": attachment.file_name or "attachment",
                "mime_type": attachment.mime_type or "",
                "file_size": attachment.file_size,
                "url": url,
                "content_id": content_id,
                "is_image": bool(
                    attachment.mime_type and attachment.mime_type.startswith("image/")
                ),
            }
        )

    metadata = msg.metadata_ or {}
    meta_attachments = metadata.get("attachments") if isinstance(metadata, dict) else None
    attachment_caption = None
    if isinstance(meta_attachments, list):
        for idx, meta_attachment in enumerate(meta_attachments):
            if not isinstance(meta_attachment, dict):
                continue
            payload_value = meta_attachment.get("payload")
            payload: dict = payload_value if isinstance(payload_value, dict) else {}
            if attachment_caption is None:
                caption = payload.get("caption")
                if isinstance(caption, str) and caption.strip():
                    attachment_caption = caption.strip()
            attachment_id = (
                payload.get("attachment_id")
                or payload.get("id")
                or meta_attachment.get("id")
            )
            url = payload.get("url") or meta_attachment.get("url")
            attachment_type = (
                meta_attachment.get("type")
                or payload.get("content_type")
                or payload.get("mime_type")
                or ""
            )
            is_image = attachment_type == "image" or str(attachment_type).startswith("image/")
            if not url and attachment_id:
                url = f"/admin/crm/inbox/attachment/{msg.id}/{idx}"
            file_name = meta_attachment.get("file_name") or f"attachment-{idx + 1}"
            attachments.append(
                {
                    "id": f"meta-{idx + 1}",
                    "file_name": file_name,
                    "mime_type": attachment_type,
                    "file_size": None,
                    "url": url,
                    "content_id": None,
                    "attachment_id": attachment_id,
                    "is_image": is_image,
                }
            )

    content = msg.body or ""
    if content.startswith("[Attachment:") and (attachments or meta_attachments):
        content = ""
    if (
        msg.channel_type == ChannelType.whatsapp
        and (attachments or meta_attachments)
        and content.strip().startswith("[")
        and content.strip().endswith("message]")
    ):
        content = attachment_caption or ""
    if content.strip() in {"[reaction message]", "[location message]"}:
        content = ""

    if not content.strip() and isinstance(metadata, dict):
        meta_type = metadata.get("type")
        if isinstance(meta_type, str):
            meta_type_value = meta_type.lower()
        else:
            meta_type_value = None

        if meta_type_value == "reaction":
            emoji = metadata.get("emoji")
            if isinstance(emoji, str) and emoji.strip():
                content = f"Reaction {emoji.strip()}"
            else:
                content = "Reaction received"
        elif meta_type_value == "location":
            loc_label = (
                metadata.get("label")
                or metadata.get("name")
                or metadata.get("address")
            )
            if not loc_label:
                loc = metadata.get("location")
                if isinstance(loc, dict):
                    loc_label = (
                        loc.get("label")
                        or loc.get("name")
                        or loc.get("address")
                    )
            if loc_label:
                content = f"📍 {loc_label}"
            else:
                lat = metadata.get("latitude")
                lng = metadata.get("longitude")
                if lat is not None and lng is not None:
                    content = f"📍 https://maps.google.com/?q={lat},{lng}"
                else:
                    content = "📍 Location shared"

    if msg.channel_type == ChannelType.instagram_dm:
        meta_count = len(meta_attachments) if isinstance(meta_attachments, list) else 0
        first_url = attachments[0].get("url") if attachments else None
        logger.info(
            "crm_inbox_ig_attachments message_id=%s meta_count=%s rendered_count=%s first_url=%s",
            msg.id,
            meta_count,
            len(attachments),
            first_url,
        )

    visibility = metadata.get("visibility") if isinstance(metadata, dict) else None
    note_type = metadata.get("type") if isinstance(metadata, dict) else None
    is_private_note = (
        msg.direction == MessageDirection.internal
        or msg.channel_type == ChannelType.note
        or note_type == "private_note"
    )

    html_body = metadata.get("html_body") if isinstance(metadata, dict) else None
    if html_body:
        html_body = _replace_cid_images(html_body, attachments)
    html_source = html_body or content
    if not html_body and "&lt;" in content and "&gt;" in content:
        html_source = html.unescape(content)

    return {
        "id": str(msg.id),
        "conversation_id": str(msg.conversation_id),
        "direction": msg.direction.value,
        "content": content,
        "content_html": _sanitize_message_html(html_source),
        "html_body": html_body,
        "timestamp": msg.received_at or msg.sent_at or msg.created_at,
        "status": msg.status.value if msg.status else "received",
        "attachments": attachments,
        "sender": {
            "name": sender_name,
            "initials": sender_initials,
        },
        "channel_type": msg.channel_type.value if msg.channel_type else "email",
        "visibility": visibility,
        "is_private_note": is_private_note,
        "author_id": str(msg.author_id) if msg.author_id else None,
        "reply_to": metadata.get("reply_to") if isinstance(metadata, dict) else None,
        "reply_to_message_id": str(msg.reply_to_message_id) if msg.reply_to_message_id else None,
    }


def _extract_meta_attachment(meta_attachment: dict) -> tuple[str | None, str | None]:
    payload_value = meta_attachment.get("payload")
    payload: dict = payload_value if isinstance(payload_value, dict) else {}
    attachment_id = payload.get("attachment_id") or payload.get("id") or meta_attachment.get("id")
    url = payload.get("url") or meta_attachment.get("url")
    return attachment_id, url


def _get_current_roles(request: Request) -> list[str]:
    auth = getattr(request.state, "auth", None)
    if isinstance(auth, dict):
        roles = auth.get("roles") or []
        if isinstance(roles, list):
            return [str(role) for role in roles]
    return []


def _get_current_agent_id(db: Session, current_user: dict | None) -> str | None:
    person_id = (current_user or {}).get("person_id")
    if not person_id:
        return None
    try:
        person_uuid = coerce_uuid(person_id)
    except Exception:
        return None
    agent = (
        db.query(CrmAgent)
        .filter(CrmAgent.person_id == person_uuid, CrmAgent.is_active.is_(True))
        .first()
    )
    return str(agent.id) if agent else None


@router.post("/agents/presence", response_class=JSONResponse)
async def update_current_agent_presence(
    request: Request,
    db: Session = Depends(get_db),
):
    """Update presence for the current CRM agent (derived from logged-in user)."""
    from app.web.admin import get_current_user

    current_user = get_current_user(request)
    agent_id = _get_current_agent_id(db, current_user)
    if not agent_id:
        return Response(status_code=204)

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    status = payload.get("status") if isinstance(payload, dict) else None
    if status is not None and status not in {s.value for s in AgentPresenceStatus}:
        raise HTTPException(status_code=400, detail="Invalid status")

    crm_service.agent_presence.upsert(db, agent_id, status=status)
    return JSONResponse({"ok": True})


def _filter_messages_for_user(
    messages: list[dict],
    current_user_id: str | None,
    current_roles: list[str] | None,
) -> list[dict]:
    if not messages:
        return []
    user_id = current_user_id or ""
    roles = set(current_roles or [])
    filtered: list[dict] = []
    for msg in messages:
        if not msg.get("is_private_note"):
            filtered.append(msg)
            continue
        visibility = msg.get("visibility") or "team"
        if visibility == "author" and msg.get("author_id") != user_id:
            continue
        if visibility == "admins" and "admin" not in roles:
            continue
        filtered.append(msg)
    return filtered


def _format_contact_for_template(contact: Person, db: Session) -> dict:
    """Transform a Contact model into detailed template-friendly dict."""
    channels = []
    for ch in contact.channels or []:
        channels.append({
            "type": ch.channel_type.value,
            "address": ch.address,
            "verified": ch.is_verified,
        })

    company = None
    if contact.organization:
        company = contact.organization.name

    resolved_person_id = str(contact.id)

    # Get tags efficiently using service method (single query instead of N+1)
    tags = contact_service.get_contact_tags(db, resolved_person_id)

    # Use service layer methods for data retrieval
    recent_tickets = contact_service.get_contact_recent_tickets(
        db, resolved_person_id, subscriber_ids=None, limit=3
    )
    recent_projects = contact_service.get_contact_recent_projects(
        db, resolved_person_id, subscriber_ids=None, limit=3
    )
    recent_tasks = contact_service.get_contact_recent_tasks(
        db, resolved_person_id, subscriber_ids=None, limit=3
    )
    conversations_summary = contact_service.get_contact_conversations_summary(
        db, resolved_person_id, limit=5
    )

    # Build recent conversations list using service method
    recent_conversations = []
    recent_convs = contact_service.get_contact_recent_conversations(db, resolved_person_id, limit=5)
    for conv in recent_convs:
        conv_payload = _format_conversation_for_template(conv, db)
        last_message_at = conv_payload.get("last_message_at")
        if isinstance(last_message_at, str):
            try:
                last_message_at = datetime.fromisoformat(last_message_at)
            except ValueError:
                last_message_at = None
        recent_conversations.append({
            "id": str(conv.id),
            "subject": conv_payload.get("subject") or f"Conversation {str(conv.id)[:8]}",
            "status": conv_payload.get("status") or (conv.status.value if conv.status else "open"),
            "updated_at": last_message_at.strftime("%Y-%m-%d %H:%M") if last_message_at else "N/A",
            "preview": conv_payload.get("preview") or "No messages yet",
            "channel": conv_payload.get("channel"),
            "sort_at": last_message_at or conv.updated_at,
            "href": f"/admin/crm/inbox?conversation_id={conv.id}",
        })

    from app.services.crm.inbox.comments_summary import (
        merge_recent_conversations_with_comments,
    )
    recent_conversations = merge_recent_conversations_with_comments(
        db,
        resolved_person_id,
        recent_conversations,
        limit=5,
    )

    # Get resolved conversations via service method
    resolved_data = contact_service.get_contact_resolved_conversations(db, resolved_person_id)
    resolved_conversations = []
    for conv_data in resolved_data:
        last_message_at = conv_data.get("last_message_at") or conv_data.get("updated_at")
        resolved_conversations.append({
            "id": conv_data["id"],
            "subject": conv_data["subject"],
            "status": conv_data["status"],
            "updated_at": last_message_at.strftime("%Y-%m-%d %H:%M") if last_message_at else "N/A",
            "preview": "No messages yet",
            "channel": None,
            "sort_at": last_message_at,
            "href": f"/admin/crm/inbox?conversation_id={conv_data['id']}",
        })

    total_conversations = len(contact.conversations) if contact.conversations else 0

    # Get splynx_id from contact metadata
    splynx_id = None
    if contact.metadata_:
        splynx_id = contact.metadata_.get("splynx_id")

    address_parts = [
        contact.address_line1,
        contact.address_line2,
        contact.city,
        contact.region,
        contact.postal_code,
        contact.country_code,
    ]
    address_text = ", ".join([part for part in address_parts if part])
    phone_display = contact.phone or ""
    if phone_display and not phone_display.startswith("+"):
        phone_display = f"+{phone_display}"

    return {
        "id": str(contact.id),
        "name": contact.display_name or phone_display or contact.email or "Unknown",
        "email": contact.email,
        "phone": phone_display,
        "company": company,
        "is_active": contact.is_active,
        "avatar_initials": _get_initials(contact.display_name or contact.email),
        "channels": channels,
        "tags": list(tags)[:5],
        "subscriber": None,  # Subscriber info removed
        "splynx_id": splynx_id,
        "recent_tickets": recent_tickets,
        "recent_projects": recent_projects,
        "recent_tasks": recent_tasks,
        "notes": contact.notes,
        "address": {
            "line1": contact.address_line1,
            "line2": contact.address_line2,
            "city": contact.city,
            "region": contact.region,
            "postal_code": contact.postal_code,
            "country_code": contact.country_code,
            "text": address_text,
        },
        "total_conversations": total_conversations,
        "conversations": conversations_summary,
        "recent_conversations": recent_conversations,
        "resolved_conversations": resolved_conversations,
        "avg_response_time": "N/A",
    }


@router.get("/inbox", response_class=HTMLResponse)
async def inbox(
    request: Request,
    db: Session = Depends(get_db),
    channel: str | None = None,
    status: str | None = None,
    search: str | None = None,
    assignment: str | None = None,
    target_id: str | None = None,
    conversation_id: str | None = None,
    comment_id: str | None = None,
):
    """Omni-channel inbox view."""
    from app.web.admin import get_current_user, get_sidebar_stats
    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)
    assigned_person_id = current_user.get("person_id")
    current_agent_id = _get_current_agent_id(db, current_user)

    comments_mode = channel == "comments"
    comments: list[dict] = []
    selected_comment = None
    comment_replies: list[dict] = []
    conversations: list[dict] = []
    selected_conversation = None
    messages: list[dict] = []
    contact_details = None

    if comments_mode:
        from app.services.crm.inbox.comments_context import load_comments_context
        context = await load_comments_context(
            db,
            search=search,
            comment_id=comment_id,
            fetch=False,
            target_id=target_id,
        )
        comments = context.grouped_comments
        selected_comment = context.selected_comment
        comment_replies = context.comment_replies
        if conversation_id:
            try:
                conv = conversation_service.Conversations.get(db, conversation_id)
                selected_conversation = _format_conversation_for_template(
                    conv, db, include_inbox_label=True
                )
                if conv.contact:
                    contact_details = _format_contact_for_template(conv.contact, db)
            except Exception:
                pass

    # Use service layer methods for inbox queries
    if not comments_mode:
        from app.services.crm.inbox.listing import load_inbox_list
        listing = await load_inbox_list(
            db,
            channel=channel,
            status=status,
            search=search,
            assignment=assignment,
            assigned_person_id=assigned_person_id,
            target_id=target_id,
            include_thread=False,
            fetch_comments=False,
        )
        conversations = [
            _format_conversation_for_template(
                conv,
                db,
                latest_message=latest_message,
                unread_count=unread_count,
                include_inbox_label=True,
            )
            for conv, latest_message, unread_count in listing.conversations_raw
        ]
        if listing.comment_items:
            conversations = conversations + listing.comment_items
            conversations.sort(
                key=lambda item: item.get("last_message_at")
                or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            conversations = conversations[:50]

        target_conv_id = conversation_id
        if not target_conv_id and conversations:
            first_conv = next(
                (entry for entry in conversations if entry.get("kind") != "comment"),
                None,
            )
            if first_conv:
                target_conv_id = first_conv["id"]

        if target_conv_id:
            try:
                conv = conversation_service.Conversations.get(db, target_conv_id)
                selected_conversation = _format_conversation_for_template(
                    conv, db, include_inbox_label=True
                )
            except Exception:
                pass

    from app.services.crm.inbox.dashboard import load_inbox_stats
    stats, channel_stats = load_inbox_stats(db)

    from app.services.crm.inbox.inboxes import get_email_channel_state, list_channel_targets
    email_channel = get_email_channel_state(db)
    email_inboxes = list_channel_targets(db, ConnectorType.email)
    whatsapp_inboxes = list_channel_targets(db, ConnectorType.whatsapp)
    facebook_inboxes = list_channel_targets(db, ConnectorType.facebook)
    instagram_inboxes = list_channel_targets(db, ConnectorType.instagram)
    from app.services.crm.inbox.comments_context import list_comment_inboxes
    facebook_comment_inboxes, instagram_comment_inboxes = list_comment_inboxes(db)
    email_setup = request.query_params.get("email_setup")
    email_error = request.query_params.get("email_error")
    email_error_detail = request.query_params.get("email_error_detail")
    new_error = request.query_params.get("new_error")
    new_error_detail = request.query_params.get("new_error_detail")
    reply_error = request.query_params.get("reply_error")
    reply_error_detail = request.query_params.get("reply_error_detail")

    assignment_options = _load_crm_agent_team_options(db)
    from app.logic import private_note_logic
    notification_auto_dismiss_seconds = resolve_value(
        db, SettingDomain.notification, "crm_inbox_notification_auto_dismiss_seconds"
    )

    return templates.TemplateResponse(
        "admin/crm/inbox.html",
        {
            "request": request,
            "current_user": current_user,
            "current_agent_id": current_agent_id,
            "sidebar_stats": sidebar_stats,
            "active_page": "inbox",
            "csrf_token": get_csrf_token(request),
            "conversations": conversations,
            "selected_conversation": selected_conversation,
            "messages": messages,
            "contact_details": contact_details,
            "comments": comments,
            "selected_comment": selected_comment,
            "selected_comment_id": str(selected_comment.id) if selected_comment else None,
            "comment_replies": comment_replies,
            "stats": stats,
            "channel_stats": channel_stats,
            "current_channel": channel,
            "current_status": status,
            "current_assignment": assignment,
            "current_target_id": target_id,
            "search": search,
            "email_channel": email_channel,
            "email_inboxes": email_inboxes,
            "whatsapp_inboxes": whatsapp_inboxes,
            "facebook_inboxes": facebook_inboxes,
            "instagram_inboxes": instagram_inboxes,
            "facebook_comment_inboxes": facebook_comment_inboxes,
            "instagram_comment_inboxes": instagram_comment_inboxes,
            "email_setup": email_setup,
            "email_error": email_error,
            "email_error_detail": email_error_detail,
            "new_error": new_error,
            "new_error_detail": new_error_detail,
            "reply_error": reply_error,
            "reply_error_detail": reply_error_detail,
            "agents": assignment_options["agents"],
            "teams": assignment_options["teams"],
            "agent_labels": assignment_options["agent_labels"],
            "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
            "notification_auto_dismiss_seconds": notification_auto_dismiss_seconds,
        },
    )


@router.get("/inbox/comments/list", response_class=HTMLResponse)
async def inbox_comments_list(
    request: Request,
    db: Session = Depends(get_db),
    search: str | None = None,
    comment_id: str | None = None,
    target_id: str | None = None,
):
    from app.services.crm.inbox.comments_context import load_comments_context
    context = await load_comments_context(
        db,
        search=search,
        comment_id=comment_id,
        fetch=True,
        target_id=target_id,
        include_thread=False,
    )
    return templates.TemplateResponse(
        "admin/crm/_comment_list.html",
        {
            "request": request,
            "comments": context.grouped_comments,
            "selected_comment": context.selected_comment,
            "selected_comment_id": (
                str(context.selected_comment.id) if context.selected_comment else None
            ),
            "search": search,
            "current_target_id": target_id,
        },
    )


@router.get("/inbox/comments/thread", response_class=HTMLResponse)
async def inbox_comments_thread(
    request: Request,
    db: Session = Depends(get_db),
    search: str | None = None,
    comment_id: str | None = None,
    target_id: str | None = None,
):
    from app.services.crm.inbox.comments_context import load_comments_context
    context = await load_comments_context(
        db,
        search=search,
        comment_id=comment_id,
        fetch=False,
        target_id=target_id,
    )
    return templates.TemplateResponse(
        "admin/crm/_comment_thread.html",
        {
            "request": request,
            "selected_comment": context.selected_comment,
            "comment_replies": context.comment_replies,
        },
    )


@router.get("/inbox/settings", response_class=HTMLResponse)
async def inbox_settings(
    request: Request,
    db: Session = Depends(get_db),
):
    """Connector settings for CRM inbox channels."""
    from app.web.admin import get_current_user, get_sidebar_stats
    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)

    context = build_inbox_settings_context(
        db,
        query_params=request.query_params,
        headers=request.headers,
        current_user=current_user,
        sidebar_stats=sidebar_stats,
    )
    return templates.TemplateResponse(
        "admin/crm/inbox_settings.html",
        {
            "request": request,
            **context,
        },
    )


@router.post("/inbox/notification-settings", response_class=HTMLResponse)
async def update_inbox_notification_settings(
    request: Request,
    reminder_delay_seconds: str = Form(""),
    reminder_repeat_enabled: str | None = Form(None),
    reminder_repeat_interval_seconds: str = Form(""),
    notification_auto_dismiss_seconds: str = Form(""),
    db: Session = Depends(get_db),
):
    result = update_notification_settings(
        db,
        reminder_delay_seconds=reminder_delay_seconds,
        reminder_repeat_enabled=reminder_repeat_enabled,
        reminder_repeat_interval_seconds=reminder_repeat_interval_seconds,
        notification_auto_dismiss_seconds=notification_auto_dismiss_seconds,
    )
    if not result.ok:
        detail = quote(result.error_detail or "Failed to save notification settings", safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox/settings?notification_error=1&notification_error_detail={detail}",
            status_code=303,
        )
    return RedirectResponse(
        url="/admin/crm/inbox/settings?notification_setup=1",
        status_code=303,
    )


@router.post("/inbox/teams", response_class=HTMLResponse)
async def create_crm_team(
    request: Request,
    name: str = Form(...),
    notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    result = create_team(db, name=name, notes=notes)
    if result.ok:
        return RedirectResponse(
            url="/admin/crm/inbox/settings?team_setup=1", status_code=303
        )
    detail = quote(result.error_detail or "Failed to create team", safe="")
    return RedirectResponse(
        url=f"/admin/crm/inbox/settings?team_error=1&team_error_detail={detail}",
        status_code=303,
    )


@router.post("/inbox/agents", response_class=HTMLResponse)
async def create_crm_agent(
    request: Request,
    person_id: str | None = Form(None),
    title: str | None = Form(None),
    db: Session = Depends(get_db),
):
    result = create_agent(db, person_id=person_id, title=title)
    if result.ok:
        return RedirectResponse(
            url="/admin/crm/inbox/settings?agent_setup=1", status_code=303
        )
    detail = quote(result.error_detail or "Failed to create agent", safe="")
    return RedirectResponse(
        url=f"/admin/crm/inbox/settings?agent_error=1&agent_error_detail={detail}",
        status_code=303,
    )


@router.post("/inbox/agent-teams", response_class=HTMLResponse)
async def create_crm_agent_team(
    request: Request,
    agent_id: str = Form(...),
    team_id: str = Form(...),
    db: Session = Depends(get_db),
):
    result = create_agent_team(db, agent_id=agent_id, team_id=team_id)
    if result.ok:
        return RedirectResponse(
            url="/admin/crm/inbox/settings?assignment_setup=1", status_code=303
        )
    detail = quote(result.error_detail or "Failed to assign agent to team", safe="")
    return RedirectResponse(
        url=f"/admin/crm/inbox/settings?assignment_error=1&assignment_error_detail={detail}",
        status_code=303,
    )


@router.get("/inbox/conversations", response_class=HTMLResponse)
async def inbox_conversations_partial(
    request: Request,
    db: Session = Depends(get_db),
    channel: str | None = None,
    status: str | None = None,
    search: str | None = None,
    assignment: str | None = None,
    target_id: str | None = None,
):
    """Partial template for conversation list (HTMX)."""
    from app.web.admin import get_current_user
    current_user = get_current_user(request)
    assigned_person_id = current_user.get("person_id")
    from app.services.crm.inbox.listing import load_inbox_list
    listing = await load_inbox_list(
        db,
        channel=channel,
        status=status,
        search=search,
        assignment=assignment,
        assigned_person_id=assigned_person_id,
        target_id=target_id,
        include_thread=False,
        fetch_comments=False,
    )
    conversations = [
        _format_conversation_for_template(
            conv,
            db,
            latest_message=latest_message,
            unread_count=unread_count,
            include_inbox_label=True,
        )
        for conv, latest_message, unread_count in listing.conversations_raw
    ]
    if listing.comment_items:
        conversations = conversations + listing.comment_items
        conversations.sort(
            key=lambda item: item.get("last_message_at")
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        conversations = conversations[:50]

    return templates.TemplateResponse(
        "admin/crm/_conversation_list.html",
        {
            "request": request,
            "conversations": conversations,
            "current_channel": channel,
            "current_status": status,
            "current_assignment": assignment,
            "current_target_id": target_id,
            "search": search,
        },
    )


@router.get("/inbox/conversation/{conversation_id}", response_class=HTMLResponse)
async def inbox_conversation_detail(
    request: Request,
    conversation_id: str,
    db: Session = Depends(get_db),
):
    """Partial template for conversation thread (HTMX)."""
    from app.web.admin import get_current_user
    from app.services.crm.inbox.thread import load_conversation_thread
    current_user = get_current_user(request)
    thread = load_conversation_thread(
        db,
        conversation_id,
        actor_person_id=current_user.get("person_id"),
        mark_read=True,
    )
    if thread.kind != "success":
        return HTMLResponse(
            "<div class='p-8 text-center text-slate-500'>Conversation not found</div>"
        )

    conversation = _format_conversation_for_template(
        thread.conversation, db, include_inbox_label=True
    )
    messages = [_format_message_for_template(m, db) for m in (thread.messages or [])]
    current_roles = _get_current_roles(request)
    current_agent_id = _get_current_agent_id(db, current_user)
    messages = _filter_messages_for_user(
        messages,
        current_user.get("person_id"),
        current_roles,
    )
    from app.logic import private_note_logic

    return templates.TemplateResponse(
        "admin/crm/_message_thread.html",
        {
            "request": request,
            "conversation": conversation,
            "messages": messages,
            "current_user": current_user,
            "current_agent_id": current_agent_id,
            "current_roles": current_roles,
            "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
        },
    )


@router.get("/inbox/attachment/{message_id}/{attachment_index}")
def inbox_attachment(
    message_id: str,
    attachment_index: int,
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.attachments import fetch_inbox_attachment

    result = fetch_inbox_attachment(db, message_id, attachment_index)
    if result.kind == "redirect" and result.redirect_url:
        return RedirectResponse(result.redirect_url)
    if result.kind == "content" and result.content is not None:
        return Response(
            content=result.content,
            media_type=result.content_type or "application/octet-stream",
            headers={"Content-Disposition": "inline"},
        )
    return Response(status_code=404)


@router.get("/inbox/contact/{contact_id}", response_class=HTMLResponse)
async def inbox_contact_detail(
    request: Request,
    contact_id: str,
    conversation_id: str | None = None,
    db: Session = Depends(get_db),
):
    """Partial template for contact details sidebar (HTMX)."""
    try:
        contact_service.Contacts.get(db, contact_id)
        contact = contact_service.get_person_with_relationships(db, contact_id)
    except Exception:
        return HTMLResponse(
            "<div class='p-8 text-center text-slate-500'>Contact not found</div>"
        )

    if not contact:
        return HTMLResponse(
            "<div class='p-8 text-center text-slate-500'>Contact not found</div>"
        )

    contact_details = _format_contact_for_template(contact, db)
    private_notes = []
    notes_query = (
        db.query(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Conversation.person_id == coerce_uuid(contact_id))
        .filter(Message.channel_type == ChannelType.note)
        .order_by(
            func.coalesce(
                Message.received_at,
                Message.sent_at,
                Message.created_at,
            ).desc()
        )
        .limit(10)
        .all()
    )
    for note in notes_query:
        payload = _format_message_for_template(note, db)
        if payload.get("is_private_note"):
            private_notes.append(payload)
        if len(private_notes) >= 5:
            break
    assignment_options = _load_crm_agent_team_options(db)
    from app.logic import private_note_logic

    return templates.TemplateResponse(
        "admin/crm/_contact_details.html",
        {
            "request": request,
            "contact": contact_details,
            "conversation_id": conversation_id,
            "agents": assignment_options["agents"],
            "teams": assignment_options["teams"],
            "agent_labels": assignment_options["agent_labels"],
            "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
            "private_notes": private_notes,
        },
    )


@router.post("/inbox/conversation/{conversation_id}/assignment", response_class=HTMLResponse)
def inbox_conversation_assignment(
    request: Request,
    conversation_id: str,
    agent_id: str | None = Form(None),
    team_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_current_user
    from app.services.crm.inbox.conversation_actions import assign_conversation

    conversation_result = assign_conversation(
        db,
        conversation_id=conversation_id,
        agent_id=agent_id,
        team_id=team_id,
        assigned_by_id=(get_current_user(request).get("person_id") or "").strip() or None,
    )
    if conversation_result.kind == "not_found":
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Conversation not found</div>",
            status_code=404,
        )
    if conversation_result.kind == "invalid_input":
        return HTMLResponse(
            "<div class='p-4 text-sm text-red-500'>Invalid agent or team selection.</div>",
            status_code=200,
        )
    if conversation_result.kind == "error":
        logger.exception("Failed to assign conversation.")
        if request.headers.get("HX-Request"):
            conversation = conversation_result.conversation
            contact = (
                contact_service.get_person_with_relationships(db, str(conversation.contact_id))
                if conversation
                else None
            )
            if contact:
                contact_details = _format_contact_for_template(contact, db)
                assignment_options = _load_crm_agent_team_options(db)
                from app.logic import private_note_logic
                return templates.TemplateResponse(
                    "admin/crm/_contact_details.html",
                    {
                        "request": request,
                        "contact": contact_details,
                        "conversation_id": str(conversation.id) if conversation else None,
                        "agents": assignment_options["agents"],
                        "teams": assignment_options["teams"],
                        "agent_labels": assignment_options["agent_labels"],
                        "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
                        "assignment_error_detail": conversation_result.error_detail or "Assignment failed",
                    },
                )
            return HTMLResponse(
                "<div class='p-6 text-center text-slate-500'>Contact not found</div>",
                status_code=200,
            )
        return RedirectResponse(
            url=f"/admin/crm/inbox?conversation_id={conversation_id}",
            status_code=303,
        )

    contact = conversation_result.contact
    if not contact:
        return RedirectResponse(
            url=f"/admin/crm/inbox?conversation_id={conversation_id}",
            status_code=303,
        )

    contact_details = _format_contact_for_template(contact, db)
    assignment_options = _load_crm_agent_team_options(db)
    if request.headers.get("HX-Request"):
        from app.logic import private_note_logic
        return templates.TemplateResponse(
            "admin/crm/_contact_details.html",
            {
                "request": request,
                "contact": contact_details,
                "conversation_id": str(conversation_result.conversation.id),
                "agents": assignment_options["agents"],
                "teams": assignment_options["teams"],
                "agent_labels": assignment_options["agent_labels"],
                "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
            },
        )
    return RedirectResponse(
        url=f"/admin/crm/inbox?conversation_id={conversation_id}",
        status_code=303,
    )


@router.post("/inbox/conversation/{conversation_id}/resolve", response_class=HTMLResponse)
def inbox_conversation_resolve(
    request: Request,
    conversation_id: str,
    person_id: str = Form(...),
    subscriber_id: str | None = Form(None),
    channel_type: str | None = Form(None),
    channel_address: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.web.admin import get_current_user
    from app.services.crm.inbox.conversation_actions import resolve_conversation

    result = resolve_conversation(
        db,
        conversation_id=conversation_id,
        person_id=person_id,
        channel_type=channel_type,
        channel_address=channel_address,
        merged_by_id=(get_current_user(request).get("person_id") if get_current_user(request) else None),
    )
    if result.kind == "not_found":
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Conversation not found</div>",
            status_code=404,
        )
    if result.kind == "invalid_channel":
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Invalid channel type</div>",
            status_code=400,
        )
    if result.kind == "error":
        return HTMLResponse(
            f"<div class='p-6 text-center text-slate-500'>Resolve failed: {result.error_detail}</div>",
            status_code=400,
        )

    contact = result.contact
    if not contact:
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Contact not found</div>",
            status_code=404,
        )

    contact_details = _format_contact_for_template(contact, db)
    assignment_options = _load_crm_agent_team_options(db)
    from app.logic import private_note_logic
    return templates.TemplateResponse(
        "admin/crm/_contact_details.html",
        {
            "request": request,
            "contact": contact_details,
            "conversation_id": str(result.conversation.id),
            "agents": assignment_options["agents"],
            "teams": assignment_options["teams"],
            "agent_labels": assignment_options["agent_labels"],
            "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
        },
    )


@router.post("/inbox/conversation/{conversation_id}/message", response_class=HTMLResponse)
async def send_message(
    request: Request,
    conversation_id: str,
    message: str | None = Form(None),
    attachments: str | None = Form(None),
    reply_to_message_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Send a message in a conversation."""
    from app.web.admin import get_current_user
    from app.services.crm.inbox.admin_ui import send_conversation_message
    current_user = get_current_user(request)

    author_id = current_user.get("person_id") if current_user.get("person_id") else None
    result = send_conversation_message(
        db=db,
        conversation_id=conversation_id,
        message_text=message,
        attachments_json=attachments,
        reply_to_message_id=reply_to_message_id,
        author_id=author_id,
    )

    if result.kind == "not_found":
        return HTMLResponse(
            "<div class='p-8 text-center text-red-500'>Conversation not found</div>"
        )
    if result.kind == "validation_error":
        detail = result.error_detail or "Message or attachment is required."
        return HTMLResponse(
            f"<div class='p-4 text-sm text-red-500'>{detail}</div>",
            status_code=422,
        )
    if result.kind == "send_failed":
        detail = quote(result.error_detail or "Meta rejected the outbound message.", safe="")
        url = f"/admin/crm/inbox?conversation_id={conversation_id}&reply_error=1&reply_error_detail={detail}"
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=204, headers={"HX-Redirect": url})
        return RedirectResponse(url=url, status_code=303)

    try:
        conversation = _format_conversation_for_template(
            result.conversation, db, include_inbox_label=True
        )
        # Fetch latest 100 messages then reverse for chronological display.
        messages_raw = conversation_service.Messages.list(
            db=db,
            conversation_id=conversation_id,
            channel_type=None,
            direction=None,
            status=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        messages_raw = list(reversed(messages_raw))
        messages = [_format_message_for_template(m, db) for m in messages_raw]
        current_roles = _get_current_roles(request)
        messages = _filter_messages_for_user(
            messages,
            author_id,
            current_roles,
        )
        from app.logic import private_note_logic

        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(
                url=f"/admin/crm/inbox?conversation_id={conversation_id}",
                status_code=303,
            )

        return templates.TemplateResponse(
            "admin/crm/_message_thread.html",
            {
                "request": request,
                "conversation": conversation,
                "messages": messages,
                "current_user": current_user,
                "current_roles": current_roles,
                "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
            },
        )
    except Exception as exc:
        detail = quote(str(exc) or "Reply failed", safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox?conversation_id={conversation_id}&reply_error=1&reply_error_detail={detail}",
            status_code=303,
        )



@router.post("/inbox/conversation/{conversation_id}/note")
def create_private_note(
    request: Request,
    conversation_id: str,
    payload: PrivateNoteCreate,
    db: Session = Depends(get_db),
):
    """Create an internal-only private note for a conversation."""
    from fastapi import HTTPException
    from app.logic import private_note_logic
    from app.web.admin import get_current_user
    from app.services.crm.inbox.private_notes_admin import create_private_note

    if not private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    try:
        current_user = get_current_user(request) or {}
        author_id = current_user.get("person_id")
        note = create_private_note(
            db,
            conversation_id=conversation_id,
            author_id=author_id,
            body=payload.body,
            requested_visibility=payload.requested_visibility,
        )
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    metadata = note.metadata_ if isinstance(note.metadata_, dict) else {}
    return JSONResponse(
        {
            "id": str(note.id),
            "conversation_id": str(note.conversation_id),
            "author_id": str(note.author_id) if note.author_id else None,
            "body": note.body,
            "visibility": metadata.get("visibility"),
            "type": metadata.get("type"),
            "created_at": note.created_at.isoformat() if note.created_at else None,
        }
    )


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


@router.post("/inbox/{conversation_id}/private_note")
def create_private_note_api(
    request: Request,
    conversation_id: str,
    payload: PrivateNoteRequest,
    db: Session = Depends(get_db),
):
    """Create a private note via JSON and return note metadata."""
    from fastapi import HTTPException
    from app.web.admin import get_current_user
    from app.services.crm.inbox.private_notes_admin import create_private_note_with_attachments

    if not payload.body or not payload.body.strip():
        return JSONResponse({"detail": "Private note body is empty"}, status_code=400)

    try:
        current_user = get_current_user(request) or {}
        author_id = current_user.get("person_id")
        attachments = payload.attachments or []
        note = create_private_note_with_attachments(
            db,
            conversation_id=conversation_id,
            author_id=author_id,
            body=payload.body,
            requested_visibility=payload.visibility,
            attachments=attachments,
        )
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    message = _format_message_for_template(note, db)
    current_roles = _get_current_roles(request)
    visible = _filter_messages_for_user([message], author_id, current_roles)
    if not visible:
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    message = visible[0]

    accept = request.headers.get("accept", "")
    if "text/html" in accept or request.headers.get("HX-Request") == "true":
        return templates.TemplateResponse(
            "admin/crm/_private_note_item.html",
            {
                "request": request,
                "msg": message,
            },
        )

    return JSONResponse(
        {
            "id": message["id"],
            "conversation_id": str(note.conversation_id),
            "author_id": message.get("author_id"),
            "body": message["content"],
            "visibility": message.get("visibility"),
            "type": "private_note",
            "received_at": note.received_at.isoformat() if note.received_at else None,
            "created_at": note.created_at.isoformat() if note.created_at else None,
            "timestamp": message["timestamp"].isoformat() if message.get("timestamp") else None,
            "attachments": message.get("attachments") or [],
        }
    )


@router.delete("/inbox/conversation/{conversation_id}/private_note/{note_id}")
def delete_private_note_api(
    request: Request,
    conversation_id: str,
    note_id: str,
    db: Session = Depends(get_db),
):
    """Delete a private note in a conversation."""
    from fastapi import HTTPException
    from app.web.admin import get_current_user
    from app.services.crm.inbox.private_notes_admin import delete_private_note

    current_user = get_current_user(request) or {}
    author_id = current_user.get("person_id")

    try:
        delete_private_note(
            db,
            conversation_id=conversation_id,
            note_id=note_id,
            actor_id=author_id,
        )
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return Response(status_code=204)


@router.post("/inbox/conversation/{conversation_id}/attachments")
async def upload_conversation_attachments(
    conversation_id: str,
    files: UploadFile | list[UploadFile] | tuple[UploadFile, ...] | None = File(None),
    db: Session = Depends(get_db),
):
    """Upload attachments for a conversation message/private note."""
    from app.services.crm.inbox.attachments_upload import save_conversation_attachments

    try:
        saved = await save_conversation_attachments(
            db,
            conversation_id=conversation_id,
            files=files,
        )
    except ValueError as exc:
        message = str(exc) or "No attachments provided"
        status_code = 404 if "Conversation not found" in message else 400
        return JSONResponse({"detail": message}, status_code=status_code)
    return JSONResponse({"attachments": saved})






@router.post("/inbox/conversation/{conversation_id}/status", response_class=HTMLResponse)
async def update_conversation_status(
    request: Request,
    conversation_id: str,
    new_status: str = Query(...),
    db: Session = Depends(get_db),
):
    """Update conversation status."""
    from app.services.crm.inbox.conversation_status import update_conversation_status
    _ = update_conversation_status(db, conversation_id=conversation_id, new_status=new_status)

    if request.headers.get("HX-Target") == "message-thread":
        from app.web.admin import get_current_user
        from app.services.crm.inbox.thread import load_conversation_thread
        current_user = get_current_user(request)
        thread = load_conversation_thread(
            db,
            conversation_id,
            actor_person_id=current_user.get("person_id"),
            mark_read=False,
        )
        if thread.kind != "success":
            return HTMLResponse(
                "<div class='p-8 text-center text-slate-500'>Conversation not found</div>"
            )

        conversation = _format_conversation_for_template(
            thread.conversation, db, include_inbox_label=True
        )
        messages = [_format_message_for_template(m, db) for m in (thread.messages or [])]
        current_roles = _get_current_roles(request)
        messages = _filter_messages_for_user(
            messages,
            current_user.get("person_id"),
            current_roles,
        )
        from app.logic import private_note_logic

        return templates.TemplateResponse(
            "admin/crm/_message_thread.html",
            {
                "request": request,
                "conversation": conversation,
                "messages": messages,
                "current_user": current_user,
                "current_roles": current_roles,
                "private_note_enabled": private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE,
            },
        )

    return await inbox_conversations_partial(request, db)


@router.post("/inbox/conversation/new", response_class=HTMLResponse)
async def start_new_conversation(
    request: Request,
    channel_type: str = Form(...),
    channel_target_id: str | None = Form(None),
    contact_address: str = Form(...),
    contact_name: str | None = Form(None),
    subject: str | None = Form(None),
    message: str = Form(...),
    db: Session = Depends(get_db),
):
    """Start a new outbound conversation."""
    from app.web.admin import get_current_user
    from app.services.crm.inbox.admin_ui import start_new_conversation
    current_user = get_current_user(request)

    result = start_new_conversation(
        db,
        channel_type=channel_type,
        channel_target_id=channel_target_id,
        contact_address=contact_address,
        contact_name=contact_name,
        subject=subject,
        message_text=message,
        author_person_id=current_user.get("person_id") if current_user else None,
    )

    if result.kind != "success":
        detail = quote(result.error_detail or "Failed to send message", safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox?new_error=1&new_error_detail={detail}",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/admin/crm/inbox?conversation_id={result.conversation_id}",
        status_code=303,
    )


@router.get("/inbox/email-connector", response_class=HTMLResponse)
def email_connector_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


def _inbox_settings_redirect(next_url: str | None = None):
    if next_url and _is_safe_url(next_url):
        return RedirectResponse(url=next_url, status_code=303)
    return RedirectResponse(url="/admin/crm/inbox/settings", status_code=303)


@router.get("/inbox/whatsapp-connector", response_class=HTMLResponse)
def whatsapp_connector_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/email-poll", response_class=HTMLResponse)
def email_poll_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/email-check", response_class=HTMLResponse)
def email_check_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/email-reset-cursor", response_class=HTMLResponse)
def email_reset_cursor_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/email-polling/reset", response_class=HTMLResponse)
def email_polling_reset_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/email-delete", response_class=HTMLResponse)
def email_delete_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/email-activate", response_class=HTMLResponse)
def email_activate_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/teams", response_class=HTMLResponse)
def inbox_teams_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/agents", response_class=HTMLResponse)
def inbox_agents_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.get("/inbox/agent-teams", response_class=HTMLResponse)
def inbox_agent_teams_redirect(next: str | None = None):
    return _inbox_settings_redirect(next)


@router.post("/inbox/email-connector", response_class=HTMLResponse)
async def configure_email_connector(
    request: Request,
    name: str = Form("CRM Email"),
    target_id: str | None = Form(None),
    connector_id: str | None = Form(None),
    create_new: str | None = Form(None),
    username: str | None = Form(None),
    password: str | None = Form(None),
    from_email: str | None = Form(None),
    from_name: str | None = Form(None),
    smtp_host: str | None = Form(None),
    smtp_port: str | None = Form(None),
    smtp_use_tls: str | None = Form(None),
    smtp_use_ssl: str | None = Form(None),
    skip_smtp_test: str | None = Form(None),
    polling_enabled: str | None = Form(None),
    smtp_enabled: str | None = Form(None),
    imap_host: str | None = Form(None),
    imap_port: str | None = Form(None),
    imap_use_ssl: str | None = Form(None),
    imap_mailbox: str | None = Form(None),
    imap_search_all: str | None = Form(None),
    pop3_host: str | None = Form(None),
    pop3_port: str | None = Form(None),
    pop3_use_ssl: str | None = Form(None),
    poll_interval_seconds: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.connectors_admin import configure_email_connector

    form = await request.form()
    result = configure_email_connector(
        db,
        form=form,
        defaults={
            "name": name,
            "target_id": target_id,
            "connector_id": connector_id,
            "create_new": create_new,
            "username": username,
            "password": password,
            "from_email": from_email,
            "from_name": from_name,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_use_tls": smtp_use_tls,
            "smtp_use_ssl": smtp_use_ssl,
            "skip_smtp_test": skip_smtp_test,
            "polling_enabled": polling_enabled,
            "smtp_enabled": smtp_enabled,
            "imap_host": imap_host,
            "imap_port": imap_port,
            "imap_use_ssl": imap_use_ssl,
            "imap_mailbox": imap_mailbox,
            "imap_search_all": imap_search_all,
            "pop3_host": pop3_host,
            "pop3_port": pop3_port,
            "pop3_use_ssl": pop3_use_ssl,
            "poll_interval_seconds": poll_interval_seconds,
        },
        next_url=request.query_params.get("next"),
    )
    url = f"{result.next_url}?{result.query_key}=1"
    if result.error_detail and result.query_key == "email_error":
        detail = quote(result.error_detail or "Email validation failed", safe="")
        url = f"{result.next_url}?email_error=1&email_error_detail={detail}"
    return RedirectResponse(url=url, status_code=303)


@router.post("/inbox/whatsapp-connector", response_class=HTMLResponse)
async def configure_whatsapp_connector(
    request: Request,
    name: str = Form("CRM WhatsApp"),
    target_id: str | None = Form(None),
    connector_id: str | None = Form(None),
    create_new: str | None = Form(None),
    access_token: str | None = Form(None),
    phone_number_id: str | None = Form(None),
    base_url: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.services.crm.inbox.connectors_admin import configure_whatsapp_connector

    form = await request.form()
    result = configure_whatsapp_connector(
        db,
        form=form,
        defaults={
            "name": name,
            "target_id": target_id,
            "connector_id": connector_id,
            "create_new": create_new,
            "access_token": access_token,
            "phone_number_id": phone_number_id,
            "base_url": base_url,
        },
        next_url=request.query_params.get("next"),
    )
    url = f"{result.next_url}?{result.query_key}=1"
    return RedirectResponse(url=url, status_code=303)


@router.post("/inbox/email-poll", response_class=HTMLResponse)
async def poll_email_channel(db: Session = Depends(get_db)):
    from app.services.crm.inbox.email_actions import poll_email_channel
    result = poll_email_channel(db)
    return HTMLResponse(result.message, status_code=result.status_code)


@router.post("/inbox/email-check", response_class=HTMLResponse)
async def check_email_inbox(
    request: Request,
    target_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    form = await request.form()
    target_id_value = _as_str(form.get("target_id")) if "target_id" in form else target_id
    from app.services.crm.inbox.email_actions import check_email_inbox
    result = check_email_inbox(db, target_id_value)
    return HTMLResponse(result.message, status_code=result.status_code)


@router.post("/inbox/email-reset-cursor", response_class=HTMLResponse)
async def reset_email_imap_cursor(
    request: Request,
    target_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    form = await request.form()
    target_id_value = _as_str(form.get("target_id")) if "target_id" in form else target_id
    from app.services.crm.inbox.email_actions import reset_email_imap_cursor
    result = reset_email_imap_cursor(db, target_id_value)
    return HTMLResponse(result.message, status_code=result.status_code)


@router.post("/inbox/email-polling/reset", response_class=HTMLResponse)
async def reset_email_polling_runs(
    request: Request,
    target_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    form = await request.form()
    target_id_value = _as_str(form.get("target_id")) if "target_id" in form else target_id
    from app.services.crm.inbox.email_actions import reset_email_polling_runs
    result = reset_email_polling_runs(db, target_id_value, request.query_params.get("next"))
    return RedirectResponse(url=f"{result.next_url}?{result.query_key}=1", status_code=303)


@router.post("/inbox/email-delete", response_class=HTMLResponse)
async def delete_email_inbox(
    request: Request,
    target_id: str | None = Form(None),
    connector_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    form = await request.form()
    target_id_value = _as_str(form.get("target_id")) if "target_id" in form else target_id
    connector_id_value = _as_str(form.get("connector_id")) if "connector_id" in form else connector_id
    from app.services.crm.inbox.email_actions import delete_email_inbox
    result = delete_email_inbox(
        db,
        target_id_value,
        connector_id_value,
        request.query_params.get("next"),
    )
    if not result.ok and result.error_detail == "Inbox target is required.":
        return HTMLResponse(
            "<p class='text-xs text-red-400'>Inbox target is required.</p>",
            status_code=400,
        )
    if not result.ok and result.error_detail and result.query_key == "email_error":
        detail = quote(result.error_detail or "Failed to delete inbox", safe="")
        return RedirectResponse(
            url=f"{result.next_url}?email_error=1&email_error_detail={detail}",
            status_code=303,
        )
    return RedirectResponse(url=f"{result.next_url}?{result.query_key}=1", status_code=303)


@router.post("/inbox/email-activate", response_class=HTMLResponse)
async def activate_email_inbox(
    request: Request,
    target_id: str | None = Form(None),
    connector_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    form = await request.form()
    target_id_value = _as_str(form.get("target_id")) if "target_id" in form else target_id
    connector_id_value = _as_str(form.get("connector_id")) if "connector_id" in form else connector_id
    from app.services.crm.inbox.email_actions import activate_email_inbox
    result = activate_email_inbox(
        db,
        target_id_value,
        connector_id_value,
        request.query_params.get("next"),
    )
    if not result.ok and result.error_detail == "Inbox target is required.":
        return HTMLResponse(
            "<p class='text-xs text-red-400'>Inbox target is required.</p>",
            status_code=400,
        )
    if not result.ok and result.error_detail and result.query_key == "email_error":
        detail = quote(result.error_detail or "Failed to activate inbox", safe="")
        return RedirectResponse(
            url=f"{result.next_url}?email_error=1&email_error_detail={detail}",
            status_code=303,
        )
    return RedirectResponse(url=f"{result.next_url}?{result.query_key}=1", status_code=303)


@router.post("/inbox/comments/{comment_id}/reply", response_class=HTMLResponse)
async def reply_to_social_comment(
    request: Request,
    comment_id: str,
    message: str = Form(...),
    db: Session = Depends(get_db),
):
    next_url = request.query_params.get("next")
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/admin/crm/inbox"

    from app.services.crm.inbox.comment_replies import reply_to_social_comment
    result = await reply_to_social_comment(db, comment_id=comment_id, message=message)
    if result.kind == "not_found":
        return RedirectResponse(
            url=f"{next_url}?channel=comments&comment_id={comment_id}&reply_error=1",
            status_code=303,
        )
    if result.kind == "error":
        logger.exception(
            "social_comment_reply_failed comment_id=%s error=%s",
            comment_id,
            result.error_detail,
        )
        detail = quote(result.error_detail or "Reply failed", safe="")
        return RedirectResponse(
            url=f"{next_url}?channel=comments&comment_id={comment_id}&reply_error=1&reply_error_detail={detail}",
            status_code=303,
        )

    return RedirectResponse(
        url=f"{next_url}?channel=comments&comment_id={comment_id}&reply_sent=1",
        status_code=303,
    )


@router.get("/inbox/comments/{comment_id}/reply", response_class=HTMLResponse)
def reply_to_social_comment_get(
    request: Request,
    comment_id: str,
    next: str | None = None,
):
    next_url = next or "/admin/crm/inbox"
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/admin/crm/inbox"
    detail = quote("Session expired. Please re-submit your reply.", safe="")
    return RedirectResponse(
        url=f"{next_url}?channel=comments&comment_id={comment_id}&reply_error=1&reply_error_detail={detail}",
        status_code=303,
    )


@router.get("/contacts", response_class=HTMLResponse)
def crm_contacts_list(
    request: Request,
    search: str | None = None,
    party_status: str | None = None,
    is_active: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=10, le=100),
    db: Session = Depends(get_db),
):
    offset = (page - 1) * per_page
    active_filter = None
    if is_active is not None and is_active != "":
        active_filter = str(is_active).lower() in {"1", "true", "yes", "on"}
    contacts = crm_service.contacts.list(
        db=db,
        person_id=None,
        organization_id=None,
        party_status=party_status,
        is_active=active_filter,
        search=search,
        order_by="created_at",
        order_dir="desc",
        limit=per_page,
        offset=offset,
    )
    all_contacts = crm_service.contacts.list(
        db=db,
        person_id=None,
        organization_id=None,
        party_status=party_status,
        is_active=active_filter,
        search=search,
        order_by="created_at",
        order_dir="desc",
        limit=10000,
        offset=0,
    )
    total = len(all_contacts)
    total_pages = (total + per_page - 1) // per_page if total else 1
    people_map = {}
    org_map = {}
    for contact in contacts:
        if contact.person_id and str(contact.person_id) not in people_map:
            person = db.get(Person, contact.person_id)
            if person:
                people_map[str(contact.person_id)] = person
        if contact.organization_id and str(contact.organization_id) not in org_map:
            org = db.get(Organization, contact.organization_id)
            if org:
                org_map[str(contact.organization_id)] = org

    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "contacts": contacts,
            "people_map": people_map,
            "org_map": org_map,
            "search": search or "",
            "party_status": party_status or "",
            "party_statuses": [item.value for item in PartyStatus],
            "is_active": "" if is_active is None else str(is_active),
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
            "recent_activities": recent_activity_for_paths(db, ["/admin/crm"]),
        }
    )
    return templates.TemplateResponse("admin/crm/contacts.html", context)




@router.get("/contacts/new", response_class=HTMLResponse)
def crm_contact_new(request: Request, db: Session = Depends(get_db)):
    contact = {
        "id": "",
        "display_name": "",
        "email": "",
        "phone": "",
        "address_line1": "",
        "address_line2": "",
        "city": "",
        "region": "",
        "postal_code": "",
        "country_code": "",
        "person_id": "",
        "organization_id": "",
        "notes": "",
        "party_status": PartyStatus.contact.value,
        "is_active": True,
    }
    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "contact": contact,
            "organization_label": None,
            "party_statuses": [item.value for item in PartyStatus],
            "form_title": "New Contact",
            "submit_label": "Create Contact",
            "action_url": "/admin/crm/contacts",
        }
    )
    return templates.TemplateResponse("admin/crm/contact_form.html", context)


@router.post("/contacts", response_class=HTMLResponse)
def crm_contact_create(
    request: Request,
    display_name: str | None = Form(None),
    email: str | None = Form(None),
    phone: str | None = Form(None),
    address_line1: str | None = Form(None),
    address_line2: str | None = Form(None),
    city: str | None = Form(None),
    region: str | None = Form(None),
    postal_code: str | None = Form(None),
    country_code: str | None = Form(None),
    person_id: str | None = Form(None),
    organization_id: str | None = Form(None),
    notes: str | None = Form(None),
    party_status: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    error = None
    contact: dict[str, str | bool] = {
        "display_name": (display_name or "").strip(),
        "email": (email or "").strip(),
        "phone": (phone or "").strip(),
        "address_line1": (address_line1 or "").strip(),
        "address_line2": (address_line2 or "").strip(),
        "city": (city or "").strip(),
        "region": (region or "").strip(),
        "postal_code": (postal_code or "").strip(),
        "country_code": (country_code or "").strip(),
        "person_id": (person_id or "").strip(),
        "organization_id": (organization_id or "").strip(),
        "notes": (notes or "").strip(),
        "party_status": (party_status or "").strip(),
        "is_active": is_active == "true",
    }
    try:
        display_name_value = contact["display_name"] if isinstance(contact["display_name"], str) else ""
        email_value_raw = contact["email"] if isinstance(contact["email"], str) else ""
        phone_value = contact["phone"] if isinstance(contact["phone"], str) else ""
        address_line1_value = contact["address_line1"] if isinstance(contact["address_line1"], str) else ""
        address_line2_value = contact["address_line2"] if isinstance(contact["address_line2"], str) else ""
        city_value = contact["city"] if isinstance(contact["city"], str) else ""
        region_value = contact["region"] if isinstance(contact["region"], str) else ""
        postal_code_value = contact["postal_code"] if isinstance(contact["postal_code"], str) else ""
        country_code_value = contact["country_code"] if isinstance(contact["country_code"], str) else ""
        organization_id_value = contact["organization_id"] if isinstance(contact["organization_id"], str) else ""
        notes_value = contact["notes"] if isinstance(contact["notes"], str) else ""
        name_parts = display_name_value.split() if display_name_value else []
        first_name = name_parts[0] if name_parts else "Unknown"
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "Unknown"
        email_value = email_value_raw or f"contact-{uuid.uuid4().hex}@placeholder.local"
        party_status_value = None
        if isinstance(contact.get("party_status"), str) and contact["party_status"]:
            try:
                party_status_value = PartyStatusEnum(contact["party_status"])
            except ValueError:
                party_status_value = None
        payload = ContactCreate(
            first_name=first_name,
            last_name=last_name,
            display_name=display_name_value or None,
            email=email_value,
            phone=phone_value or None,
            address_line1=address_line1_value or None,
            address_line2=address_line2_value or None,
            city=city_value or None,
            region=region_value or None,
            postal_code=postal_code_value or None,
            country_code=country_code_value or None,
            organization_id=_coerce_uuid_optional(organization_id_value),
            party_status=party_status_value or PartyStatusEnum.contact,
            notes=notes_value or None,
            is_active=bool(contact["is_active"]),
        )
        crm_service.contacts.create(db=db, payload=payload)
        return RedirectResponse(url="/admin/crm/contacts", status_code=303)
    except (ValidationError, ValueError) as exc:
        db.rollback()
        error = str(exc)
    except Exception as exc:
        db.rollback()
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    # Get organization label for typeahead if organization_id was submitted
    organization_label = None
    if isinstance(contact.get("organization_id"), str) and contact["organization_id"]:
        org = db.get(Organization, coerce_uuid(contact["organization_id"]))
        if org:
            organization_label = org.name

    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "contact": contact,
            "organization_label": organization_label,
            "party_statuses": [item.value for item in PartyStatus],
            "form_title": "New Contact",
            "submit_label": "Create Contact",
            "action_url": "/admin/crm/contacts",
            "error": error,
        }
    )
    return templates.TemplateResponse("admin/crm/contact_form.html", context, status_code=400)


@router.get("/contacts/{contact_id}/edit", response_class=HTMLResponse)
def crm_contact_edit(request: Request, contact_id: str, db: Session = Depends(get_db)):
    contact_obj = crm_service.contacts.get(db=db, contact_id=contact_id)
    contact = {
        "id": str(contact_obj.id),
        "display_name": contact_obj.display_name or "",
        "email": contact_obj.email or "",
        "phone": contact_obj.phone or "",
        "address_line1": contact_obj.address_line1 or "",
        "address_line2": contact_obj.address_line2 or "",
        "city": contact_obj.city or "",
        "region": contact_obj.region or "",
        "postal_code": contact_obj.postal_code or "",
        "country_code": contact_obj.country_code or "",
        "person_id": str(contact_obj.person_id) if contact_obj.person_id else "",
        "organization_id": str(contact_obj.organization_id) if contact_obj.organization_id else "",
        "notes": contact_obj.notes or "",
        "party_status": contact_obj.party_status.value if contact_obj.party_status else PartyStatus.contact.value,
        "is_active": contact_obj.is_active,
    }

    # Get organization label for typeahead
    organization_label = None
    if contact_obj.organization:
        organization_label = contact_obj.organization.name

    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "contact": contact,
            "organization_label": organization_label,
            "party_statuses": [item.value for item in PartyStatus],
            "form_title": "Edit Contact",
            "submit_label": "Save Contact",
            "action_url": f"/admin/crm/contacts/{contact_id}/edit",
        }
    )
    return templates.TemplateResponse("admin/crm/contact_form.html", context)


@router.post("/contacts/{contact_id}/edit", response_class=HTMLResponse)
def crm_contact_update(
    request: Request,
    contact_id: str,
    display_name: str | None = Form(None),
    email: str | None = Form(None),
    phone: str | None = Form(None),
    address_line1: str | None = Form(None),
    address_line2: str | None = Form(None),
    city: str | None = Form(None),
    region: str | None = Form(None),
    postal_code: str | None = Form(None),
    country_code: str | None = Form(None),
    person_id: str | None = Form(None),
    organization_id: str | None = Form(None),
    notes: str | None = Form(None),
    party_status: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    error = None
    contact: dict[str, str | bool] = {
        "id": contact_id,
        "display_name": (display_name or "").strip(),
        "email": (email or "").strip(),
        "phone": (phone or "").strip(),
        "address_line1": (address_line1 or "").strip(),
        "address_line2": (address_line2 or "").strip(),
        "city": (city or "").strip(),
        "region": (region or "").strip(),
        "postal_code": (postal_code or "").strip(),
        "country_code": (country_code or "").strip(),
        "person_id": (person_id or "").strip(),
        "organization_id": (organization_id or "").strip(),
        "notes": (notes or "").strip(),
        "party_status": (party_status or "").strip(),
        "is_active": is_active == "true",
    }
    try:
        display_name_value = contact["display_name"] if isinstance(contact["display_name"], str) else ""
        email_value = contact["email"] if isinstance(contact["email"], str) else ""
        phone_value = contact["phone"] if isinstance(contact["phone"], str) else ""
        address_line1_value = contact["address_line1"] if isinstance(contact["address_line1"], str) else ""
        address_line2_value = contact["address_line2"] if isinstance(contact["address_line2"], str) else ""
        city_value = contact["city"] if isinstance(contact["city"], str) else ""
        region_value = contact["region"] if isinstance(contact["region"], str) else ""
        postal_code_value = contact["postal_code"] if isinstance(contact["postal_code"], str) else ""
        country_code_value = contact["country_code"] if isinstance(contact["country_code"], str) else ""
        organization_id_value = contact["organization_id"] if isinstance(contact["organization_id"], str) else ""
        notes_value = contact["notes"] if isinstance(contact["notes"], str) else ""
        party_status_value = None
        if isinstance(contact.get("party_status"), str) and contact["party_status"]:
            try:
                party_status_value = PartyStatusEnum(contact["party_status"])
            except ValueError:
                party_status_value = None
        payload = ContactUpdate(
            display_name=display_name_value or None,
            email=email_value or None,
            phone=phone_value or None,
            address_line1=address_line1_value or None,
            address_line2=address_line2_value or None,
            city=city_value or None,
            region=region_value or None,
            postal_code=postal_code_value or None,
            country_code=country_code_value or None,
            organization_id=_coerce_uuid_optional(organization_id_value),
            party_status=party_status_value,
            notes=notes_value or None,
            is_active=bool(contact["is_active"]),
        )
        crm_service.contacts.update(db=db, contact_id=contact_id, payload=payload)
        return RedirectResponse(url="/admin/crm/contacts", status_code=303)
    except (ValidationError, ValueError) as exc:
        error = str(exc)
    except Exception as exc:
        error = exc.detail if hasattr(exc, "detail") else str(exc)

    # Get organization label for typeahead
    organization_label = None
    if isinstance(contact.get("organization_id"), str) and contact["organization_id"]:
        org = db.get(Organization, coerce_uuid(contact["organization_id"]))
        if org:
            organization_label = org.name

    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "contact": contact,
            "organization_label": organization_label,
            "party_statuses": [item.value for item in PartyStatus],
            "form_title": "Edit Contact",
            "submit_label": "Save Contact",
            "action_url": f"/admin/crm/contacts/{contact_id}/edit",
            "error": error,
        }
    )
    return templates.TemplateResponse("admin/crm/contact_form.html", context, status_code=400)


@router.post("/contacts/{contact_id}/delete", response_class=HTMLResponse)
def crm_contact_delete(request: Request, contact_id: str, db: Session = Depends(get_db)):
    _ = request
    crm_service.contacts.delete(db=db, contact_id=contact_id)
    return RedirectResponse(url="/admin/crm/contacts", status_code=303)


@router.post("/contacts/{person_id}/convert", response_class=HTMLResponse)
def crm_contact_convert(
    person_id: UUID,
    subscriber_type: str = Form("person"),
    account_status: str = Form("active"),
    db: Session = Depends(get_db),
):
    person = db.get(Person, person_id)
    if not person:
        return RedirectResponse(url="/admin/crm/contacts", status_code=303)

    status_map = {
        "active": SubscriberStatus.active,
        "canceled": SubscriberStatus.terminated,
        "delinquent": SubscriberStatus.pending,
    }
    status = status_map.get(account_status, SubscriberStatus.active)

    subscriber = subscriber_service.create(
        db,
        {
            "person_id": person.id,
            "status": status,
        },
    )
    try:
        People.transition_status(db, str(person.id), PartyStatus.subscriber)
    except InvalidTransitionError:
        pass
    db.commit()

    return RedirectResponse(
        url=f"/admin/subscribers/{subscriber.id}",
        status_code=303,
    )


@router.get("/contacts/{contact_id}", response_class=HTMLResponse)
async def contact_detail_page(
    request: Request,
    contact_id: str,
    db: Session = Depends(get_db),
    next: str | None = Query(default=None),
):
    """Full page contact details view."""
    from app.web.admin import get_current_user, get_sidebar_stats
    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)

    contact = None
    try:
        contact_service.Contacts.get(db, contact_id)
        contact = contact_service.get_person_with_relationships(db, contact_id)
    except Exception:
        contact = None

    if not contact:
        return templates.TemplateResponse(
            "admin/errors/404.html",
            {"request": request, "message": "Contact not found"},
            status_code=404,
        )

    contact_details = _format_contact_for_template(contact, db)
    back_url = "/admin/crm/inbox"
    if next and _is_safe_url(next):
        back_url = next
    assignment_options = _load_crm_agent_team_options(db)

    return templates.TemplateResponse(
        "admin/crm/contact_detail.html",
        {
            "request": request,
            "contact": contact_details,
            "back_url": back_url,
            "active_page": "inbox",
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "agents": assignment_options["agents"],
            "teams": assignment_options["teams"],
            "agent_labels": assignment_options["agent_labels"],
        },
    )


@router.get("/leads", response_class=HTMLResponse)
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
        lead_contacts = (
            db.query(Person)
            .filter(Person.id.in_(lead_person_ids))
            .all()
        )
        contacts_map = {str(contact.id): contact for contact in lead_contacts}
    else:
        contacts_map = {}
    pipeline_map = {str(pipeline.id): pipeline for pipeline in options["pipelines"]}
    stage_map = {str(stage.id): stage for stage in options["stages"]}

    context = _crm_base_context(request, db, "contacts")
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
        }
    )
    return templates.TemplateResponse("admin/crm/leads.html", context)


@router.get("/leads/new", response_class=HTMLResponse)
def crm_lead_new(request: Request, db: Session = Depends(get_db)):
    person_id = request.query_params.get("person_id", "").strip()
    contact_id = request.query_params.get("contact_id", "").strip()  # Legacy support
    pipeline_id = request.query_params.get("pipeline_id", "").strip()
    if not person_id and contact_id:
        person_id = contact_id
    options = _load_crm_sales_options(db)
    if person_id:
        from app.services.person import people as person_svc
        if not any(str(person.id) == person_id for person in options["people"]):
            try:
                person = person_svc.get(db, person_id)
                options["people"] = [person] + options["people"]
            except Exception:
                pass
    lead = {
        "id": "",
        "person_id": person_id,
        "contact_id": contact_id,
        "pipeline_id": pipeline_id,
        "stage_id": "",
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
    context = _crm_base_context(request, db, "contacts")
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


@router.get("/leads/{lead_id}", response_class=HTMLResponse)
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
    owner_label = (
        options["agent_labels"].get(str(lead.owner_agent_id))
        if lead.owner_agent_id
        else "—"
    )
    status_val = lead.status.value if lead.status else LeadStatus.new.value

    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "lead": lead,
            "contact": contact,
            "pipeline": pipeline,
            "stage": stage,
            "owner_label": owner_label,
            "status_val": status_val,
        }
    )
    return templates.TemplateResponse("admin/crm/lead_detail.html", context)


@router.post("/leads", response_class=HTMLResponse)
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
        expected_close_date_value = (
            lead["expected_close_date"] if isinstance(lead["expected_close_date"], str) else ""
        )
        lost_reason_value = lead["lost_reason"] if isinstance(lead["lost_reason"], str) else ""
        region_value = lead["region"] if isinstance(lead["region"], str) else ""
        address_value = lead["address"] if isinstance(lead["address"], str) else ""
        notes_value = lead["notes"] if isinstance(lead["notes"], str) else ""
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
    context = _crm_base_context(request, db, "contacts")
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


@router.get("/leads/{lead_id}/edit", response_class=HTMLResponse)
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
    if lead_obj.person_id and not any(str(person.id) == str(lead_obj.person_id) for person in options["people"]):
        from app.services.person import people as person_svc
        try:
            person = person_svc.get(db, str(lead_obj.person_id))
            options["people"] = [person] + options["people"]
        except Exception:
            pass
    context = _crm_base_context(request, db, "contacts")
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


@router.post("/leads/{lead_id}/edit", response_class=HTMLResponse)
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
        expected_close_date_value = (
            lead["expected_close_date"] if isinstance(lead["expected_close_date"], str) else ""
        )
        lost_reason_value = lead["lost_reason"] if isinstance(lead["lost_reason"], str) else ""
        region_value = lead["region"] if isinstance(lead["region"], str) else ""
        address_value = lead["address"] if isinstance(lead["address"], str) else ""
        notes_value = lead["notes"] if isinstance(lead["notes"], str) else ""
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
    context = _crm_base_context(request, db, "contacts")
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


@router.post("/leads/{lead_id}/delete", response_class=HTMLResponse)
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
    per_page: int = Query(25, ge=10, le=100),
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
    inventory_items = inventory_service.inventory_items.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
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
        company_name_raw.strip()
        if isinstance(company_name_raw, str) and company_name_raw.strip()
        else "Dotmac"
    )
    summary_text = _format_project_summary(quote, lead, contact, company_name)

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
        }
    )
    return templates.TemplateResponse("admin/crm/quote_detail.html", context)


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

    person_channel = conversation_service.resolve_person_channel(
        db, str(contact.id), channel_enum
    )
    if not person_channel:
        return RedirectResponse(
            url=f"/admin/crm/quotes/{quote_id}?send_error=1",
            status_code=303,
        )

    company_name_raw = resolve_value(db, SettingDomain.comms, "company_name")
    company_name = (
        company_name_raw.strip()
        if isinstance(company_name_raw, str) and company_name_raw.strip()
        else "Dotmac"
    )
    body = (message or "").strip()
    if not body:
        body = _format_project_summary(quote, lead, contact, company_name)

    quote_label = None
    if isinstance(quote.metadata_, dict):
        quote_label = quote.metadata_.get("quote_name")
    subject = None
    attachments_payload: list[dict] | None = None
    if channel_enum == ChannelType.email:
        subject = "Installation Quote"
        stored_name = None
        try:
            pdf_bytes = _build_quote_pdf_bytes(
                request=request,
                quote=quote,
                items=items,
                lead=lead,
                contact=contact,
                quote_name=quote_label,
                branding=getattr(request.state, "branding", None),
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

    conversation = conversation_service.resolve_open_conversation_for_channel(
        db, str(contact.id), channel_enum
    )
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
    branding = getattr(request.state, "branding", None)
    html = template.render(
        {
            "request": request,
            "quote": quote,
            "items": items,
            "lead": lead,
            "contact": contact,
            "quote_name": quote_name or "",
            "branding": branding or {},
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
  <div>Status: {(quote.status.value if quote.status else 'draft')}</div>
  <div>Currency: {html_escape(currency)}</div>
  <table border="1" cellpadding="6" cellspacing="0" width="100%%">
    <thead>
      <tr><th>Description</th><th>Qty</th><th>Unit Price</th><th>Amount</th></tr>
    </thead>
    <tbody>
      {''.join(plain_rows)}
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
            tmp_html = f"/tmp/quote_{quote.id}.html"
            tmp_pdf = f"/tmp/quote_{quote.id}.pdf"
            with open(tmp_html, "w", encoding="utf-8") as handle:
                handle.write(html)
            with open(tmp_pdf, "wb") as handle:
                handle.write(pdf_bytes)
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
    cache_bust = int(datetime.now(timezone.utc).timestamp())
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
    inventory_items = inventory_service.inventory_items.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
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
        "expires_at": quote_obj.expires_at.strftime("%Y-%m-%dT%H:%M")
        if quote_obj.expires_at
        else "",
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
    project_types = [item.value for item in ProjectType]
    context = _crm_base_context(request, db, "quotes")
    context.update(
        {
            "quote": quote,
            "quote_statuses": [item.value for item in QuoteStatus],
            "project_types": project_types,
            "leads": leads,
            "contacts": options["contacts"],
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
    try:
        lead_id_value = quote["lead_id"] if isinstance(quote["lead_id"], str) else ""
        contact_id_value = quote["contact_id"] if isinstance(quote["contact_id"], str) else ""
        status_value = quote["status"] if isinstance(quote["status"], str) else ""
        project_type_value = quote["project_type"] if isinstance(quote["project_type"], str) else ""
        currency_value = quote["currency"] if isinstance(quote["currency"], str) else ""
        subtotal_value = quote["subtotal"] if isinstance(quote["subtotal"], str) else ""
        tax_total_value = quote["tax_total"] if isinstance(quote["tax_total"], str) else ""
        total_value = quote["total"] if isinstance(quote["total"], str) else ""
        expires_at_value = quote["expires_at"] if isinstance(quote["expires_at"], str) else ""
        notes_value = quote["notes"] if isinstance(quote["notes"], str) else ""
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
        person_id_value = (
            coerce_uuid(resolved_person_id) if resolved_person_id else quote_obj.person_id
        )
        if not person_id_value:
            raise ValueError("Quote must be linked to a person.")
        payload = QuoteUpdate(
            person_id=person_id_value,
            status=status_enum,
            currency=currency_value or None,
            subtotal=_parse_decimal(subtotal_value, "subtotal"),
            tax_total=_parse_decimal(tax_total_value, "tax_total"),
            total=_parse_decimal(total_value, "total"),
            expires_at=_parse_optional_datetime(expires_at_value),
            notes=notes_value or None,
            metadata_=metadata if metadata else None,
            is_active=bool(quote["is_active"]),
        )
        before = quote_obj
        updated = crm_service.quotes.update(db=db, quote_id=quote_id, payload=payload)
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
    project_types = [item.value for item in ProjectType]
    context = _crm_base_context(request, db, "quotes")
    context.update(
        {
            "quote": quote,
            "quote_statuses": [item.value for item in QuoteStatus],
            "project_types": project_types,
            "leads": leads,
            "contacts": options["contacts"],
            "form_title": "Edit Quote",
            "submit_label": "Save Quote",
            "action_url": f"/admin/crm/quotes/{quote_id}/edit",
            "error": error,
        }
    )
    return templates.TemplateResponse("admin/crm/quote_form.html", context, status_code=400)


@router.post("/quotes/{quote_id}/delete", response_class=HTMLResponse)
def crm_quote_delete(request: Request, quote_id: str, db: Session = Depends(get_db)):
    _ = request
    crm_service.quotes.delete(db=db, quote_id=quote_id)
    return RedirectResponse(url="/admin/crm/quotes", status_code=303)


@router.post("/quotes/bulk/status")
def crm_quotes_bulk_status(
    request: Request,
    db: Session = Depends(get_db),
):
    """Bulk update quote status."""
    import json

    _ = request
    try:
        body = json.loads(request._body.decode() if hasattr(request, "_body") else "{}")
    except Exception:
        body = {}
    quote_ids = body.get("quote_ids", [])
    new_status = body.get("status", "")
    if not quote_ids or not new_status:
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": "Missing quote_ids or status"}, status_code=400)
    for quote_id in quote_ids:
        try:
            from app.schemas.crm import QuoteUpdate

            crm_service.quotes.update(db, quote_id, QuoteUpdate(status=new_status))
        except Exception:
            pass
    from fastapi.responses import JSONResponse

    return JSONResponse({"success": True, "updated": len(quote_ids)})


@router.post("/quotes/bulk/delete")
def crm_quotes_bulk_delete(
    request: Request,
    db: Session = Depends(get_db),
):
    """Bulk delete quotes."""
    import json

    _ = request
    try:
        body = json.loads(request._body.decode() if hasattr(request, "_body") else "{}")
    except Exception:
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
            pass
    from fastapi.responses import JSONResponse

    return JSONResponse({"success": True, "deleted": deleted})


# ---------------------------------------------------------------------------
# Sales Dashboard and Pipeline Board Routes
# ---------------------------------------------------------------------------


@router.get("/sales", response_class=HTMLResponse)
def crm_sales_dashboard(
    request: Request,
    pipeline_id: str | None = None,
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

    # Get pipeline metrics
    metrics = reports_service.sales_pipeline_metrics(
        db,
        pipeline_id=pipeline_id,
        start_at=None,
        end_at=None,
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
        start_at=None,
        end_at=None,
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
    context.update({
        "pipelines": pipelines,
        "selected_pipeline_id": pipeline_id or "",
        "metrics": metrics,
        "forecast": forecast,
        "agent_performance": agent_performance[:10],  # Top 10
        "recent_leads": recent_leads,
        "person_map": person_map,
    })
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
    context.update({
        "pipelines": pipelines,
        "selected_pipeline_id": pipeline_id or "",
    })
    return templates.TemplateResponse("admin/crm/sales_pipeline.html", context)


# ---------------------------------------------------------------------------
# Pipeline Settings
# ---------------------------------------------------------------------------


@router.get("/settings/pipelines/new", response_class=HTMLResponse)
def crm_pipeline_new(
    request: Request,
    db: Session = Depends(get_db),
):
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
                default_probability = (
                    int(probability_value)
                    if isinstance(probability_value, (int, str))
                    else 0
                )
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

    widgets = (
        db.query(ChatWidgetConfig)
        .order_by(ChatWidgetConfig.created_at.desc())
        .all()
    )

    context = _crm_base_context(request, db, "widget")
    context.update({
        "widgets": widgets,
        "success_message": request.query_params.get("success"),
        "error_message": request.query_params.get("error"),
    })
    return templates.TemplateResponse("admin/crm/widget_list.html", context)


@router.get("/widget/new", response_class=HTMLResponse)
def crm_widget_new(
    request: Request,
    db: Session = Depends(get_db),
):
    """Show widget creation form."""
    context = _crm_base_context(request, db, "widget")
    context.update({
        "widget": None,
    })
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
        allowed_domains = [
            d.strip() for d in allowed_domains_str.split(",") if d.strip()
        ] if allowed_domains_str else []

        payload = ChatWidgetConfigCreate(
            name=_form_str(form, "name"),
            allowed_domains=allowed_domains,
            primary_color=_form_str(form, "primary_color", "#3B82F6"),
            bubble_position=_coerce_bubble_position(_form_str_opt(form, "bubble_position")),
            widget_title=_form_str(form, "widget_title", "Chat with us"),
            welcome_message=_form_str_opt(form, "welcome_message"),
            placeholder_text=_form_str(form, "placeholder_text", "Type a message..."),
            rate_limit_messages_per_minute=_as_int(
                _form_str_opt(form, "rate_limit_messages_per_minute"), 10
            ) or 10,
            rate_limit_sessions_per_ip=_as_int(
                _form_str_opt(form, "rate_limit_sessions_per_ip"), 5
            ) or 5,
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
        context.update({
            "widget": None,
            "error_message": str(e),
        })
        return templates.TemplateResponse("admin/crm/widget_detail.html", context)


@router.get("/widget/{widget_id}", response_class=HTMLResponse)
def crm_widget_detail(
    widget_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Widget detail with settings and embed code."""
    from app.models.crm.chat_widget import ChatWidgetConfig, WidgetVisitorSession
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
    session_count = (
        db.query(WidgetVisitorSession)
        .filter(WidgetVisitorSession.widget_config_id == widget.id)
        .count()
    )
    conversation_count = (
        db.query(WidgetVisitorSession)
        .filter(WidgetVisitorSession.widget_config_id == widget.id)
        .filter(WidgetVisitorSession.conversation_id.isnot(None))
        .count()
    )

    context = _crm_base_context(request, db, "widget")
    context.update({
        "widget": widget,
        "embed_code": embed_code,
        "session_count": session_count,
        "conversation_count": conversation_count,
        "success_message": request.query_params.get("success"),
        "error_message": request.query_params.get("error"),
    })
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
        allowed_domains = [
            d.strip() for d in allowed_domains_str.split(",") if d.strip()
        ] if allowed_domains_str else []

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
            rate_limit_messages_per_minute=_as_int(
                _form_str_opt(form, "rate_limit_messages_per_minute"), 10
            ) or 10,
            rate_limit_sessions_per_ip=_as_int(
                _form_str_opt(form, "rate_limit_sessions_per_ip"), 5
            ) or 5,
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
            url=f"/admin/crm/widget/{widget_id}?error={str(e)}",
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

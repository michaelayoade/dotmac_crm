"""CRM web routes - Omni-channel Inbox."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import html
import json
import uuid
from html.parser import HTMLParser
from urllib.parse import quote, urlparse, urlencode

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, selectinload, aliased

from app.db import SessionLocal
from app.models.connector import ConnectorConfig, ConnectorType
from app.models.domain_settings import SettingDomain, SettingValueType
from app.models.crm.sales import Lead
from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.team import CrmAgent, CrmAgentTeam
from app.models.crm.enums import (
    ChannelType,
    ConversationStatus,
    LeadStatus,
    MessageDirection,
    MessageStatus,
    QuoteStatus,
)
from app.models.crm.comments import SocialComment, SocialCommentPlatform
from app.models.integration import (
    IntegrationJob,
    IntegrationJobType,
    IntegrationRun,
    IntegrationRunStatus,
    IntegrationTarget,
    IntegrationTargetType,
)
from app.models.person import Person, PersonChannel, ChannelType as PersonChannelType
from app.models.projects import Project, ProjectStatus, ProjectTask, ProjectType
from app.models.subscriber import Organization, Subscriber


from app.models.tickets import Ticket
from app.schemas.connector import ConnectorConfigUpdate
from app.schemas.crm.contact import ContactCreate, ContactUpdate
from app.schemas.crm.conversation import ConversationAssignmentCreate
from app.schemas.crm.conversation import (
    ConversationCreate,
    ConversationUpdate,
    MessageAttachmentCreate,
    MessageCreate,
)
from app.schemas.crm.inbox import InboxSendRequest
from app.schemas.settings import DomainSettingUpdate
from app.schemas.crm.sales import LeadCreate, LeadUpdate, QuoteCreate, QuoteLineItemCreate, QuoteUpdate
from app.schemas.integration import IntegrationTargetUpdate
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
from app.services import email as email_service
from app.services import integration as integration_service
from app.services import person as person_service
from app.services import inventory as inventory_service
from app.config import settings
from app.services.audit_helpers import (
    build_changes_metadata,
    log_audit_event,
    recent_activity_for_paths,
)
from app.services import domain_settings as domain_settings_service
from app.services.settings_spec import resolve_value

_COMMENT_CACHE_TTL_SECONDS = 300
_COMMENT_CACHE_MAX_ITEMS = 64
_comment_list_cache: dict[str, dict] = {}
_comment_thread_cache: dict[str, dict] = {}


def _cache_get(cache: dict, key: str):
    entry = cache.get(key)
    if not entry:
        return None
    if entry["expires_at"] < datetime.now(timezone.utc):
        cache.pop(key, None)
        return None
    return entry["value"]


def _cache_set(cache: dict, key: str, value):
    cache[key] = {
        "value": value,
        "expires_at": datetime.now(timezone.utc)
        + timedelta(seconds=_COMMENT_CACHE_TTL_SECONDS),
    }
    if len(cache) <= _COMMENT_CACHE_MAX_ITEMS:
        return
    oldest_key = None
    oldest_expiry = None
    for cache_key, entry in cache.items():
        if oldest_expiry is None or entry["expires_at"] < oldest_expiry:
            oldest_key = cache_key
            oldest_expiry = entry["expires_at"]
    if oldest_key:
        cache.pop(oldest_key, None)


def _comment_cache_key(prefix: str, value: str | None) -> str:
    return f"{prefix}:{value or ''}"


def _group_comment_authors(comments: list[SocialComment]) -> list[dict]:
    grouped: list[dict] = []
    seen: dict[str, dict] = {}
    for comment in comments:
        author_key = comment.author_id or (comment.author_name or "").strip().lower()
        if not author_key:
            author_key = f"comment:{comment.external_id}"
        key = f"{comment.platform.value}:{author_key}"
        entry = seen.get(key)
        if not entry:
            entry = {
                "comment": comment,
                "comment_ids": [str(comment.id)],
                "comments": [comment],
                "count": 1,
            }
            seen[key] = entry
            grouped.append(entry)
        else:
            entry["count"] += 1
            entry["comment_ids"].append(str(comment.id))
            entry["comments"].append(comment)
    return grouped
from app.services.common import coerce_uuid
from app.services.crm import contact as contact_service
from app.services.crm import conversation as conversation_service
from app.services.crm import inbox as inbox_service
from app.services.crm import comments as comments_service
from app.services import meta_oauth
from app.logging import get_logger

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
            return ProjectType.radio_installation.value
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
    }


def _load_crm_sales_options(db: Session) -> dict:
    contacts = crm_service.contacts.list(
        db=db,
        person_id=None,
        organization_id=None,
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


def _mark_conversation_read(
    db: Session,
    conversation_id: str,
    actor_person_id: str | None,
    last_seen_at: datetime | None,
) -> None:
    from app.services.crm.conversation import mark_conversation_read
    mark_conversation_read(db, conversation_id, actor_person_id, last_seen_at)


def _disable_email_polling_job(db: Session, target_id: str) -> None:
    integration_service.IntegrationJobs.disable_import_jobs_for_target(db, target_id)


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


def _get_email_channel_state(db: Session) -> dict | None:
    state = integration_service.integration_targets.get_channel_state(
        db, IntegrationTargetType.crm, ConnectorType.email
    )
    if state:
        smtp = state.get("smtp")
        imap = state.get("imap")
        pop3 = state.get("pop3")
        logger.info(
            "crm_inbox_email_state %s",
            _safe_log_json({
                "target_id": state.get("target_id"),
                "connector_id": state.get("connector_id"),
                "smtp": {
                    "host": smtp.get("host") if isinstance(smtp, dict) else None,
                    "port": smtp.get("port") if isinstance(smtp, dict) else None,
                },
                "imap": {
                    "host": imap.get("host") if isinstance(imap, dict) else None,
                    "port": imap.get("port") if isinstance(imap, dict) else None,
                },
                "pop3": {
                    "host": pop3.get("host") if isinstance(pop3, dict) else None,
                    "port": pop3.get("port") if isinstance(pop3, dict) else None,
                },
                "poll_interval_seconds": state.get("poll_interval_seconds"),
            }),
        )
    return state


def _get_whatsapp_channel_state(db: Session) -> dict | None:
    return integration_service.integration_targets.get_channel_state(
        db, IntegrationTargetType.crm, ConnectorType.whatsapp
    )


def _format_conversation_for_template(
    conv: Conversation,
    db: Session,
    latest_message: dict | Message | None = None,
    unread_count: int | None = None,
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
    if latest_message:
        channel_type = (
            latest_message.get("channel_type")
            if isinstance(latest_message, dict)
            else latest_message.channel_type
        )
        if channel_type:
            channel = channel_type.value
    elif contact and contact.channels:
        channel = contact.channels[0].channel_type.value

    assigned_to = None
    if conv.assignments:
        active_assignment = next((a for a in conv.assignments if a.is_active), None)
        if active_assignment and active_assignment.agent:
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

    company = None
    if contact and contact.organization_id:
        org = db.get(Organization, contact.organization_id)
        if org:
            company = org.name

    preview = "No messages yet"
    if latest_message:
        body = (
            latest_message.get("body")
            if isinstance(latest_message, dict)
            else latest_message.body
        )
        if body:
            preview = body[:100] + "..." if len(body) > 100 else body
    latest_message_at = None
    if isinstance(latest_message, dict):
        latest_message_at = latest_message.get("last_message_at")
    elif latest_message:
        latest_message_at = (
            latest_message.received_at
            or latest_message.sent_at
            or latest_message.created_at
        )

    return {
        "id": str(conv.id),
        "contact": {
            "id": str(contact.id) if contact else "",
            "name": contact.display_name or contact.email or "Unknown" if contact else "Unknown",
            "email": contact.email if contact else "",
            "phone": contact.phone if contact else "",
            "avatar_initials": _get_initials(contact.display_name or contact.email) if contact else "?",
            "company": company,
        },
        "channel": channel,
        "status": conv.status.value if conv.status else "open",
        "subject": conv.subject,
        "preview": preview,
        "unread_count": unread_count or 0,
        "last_message_at": conv.last_message_at or latest_message_at or conv.updated_at,
        "assigned_to": assigned_to,
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
    if isinstance(meta_attachments, list):
        for idx, meta_attachment in enumerate(meta_attachments):
            if not isinstance(meta_attachment, dict):
                continue
            payload_value = meta_attachment.get("payload")
            payload: dict = payload_value if isinstance(payload_value, dict) else {}
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

    return {
        "id": str(msg.id),
        "direction": msg.direction.value,
        "content": content,
        "content_html": _sanitize_message_html(content),
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

    # Get social comments via service method (returns raw SocialComment objects)
    social_comments = contact_service.get_contact_social_comments(db, resolved_person_id, limit=10)
    comment_summaries = []
    if social_comments:
        # Group comments by author for display
        grouped_comments = _group_comment_authors(social_comments)
        for entry in grouped_comments:
            comment = entry["comment"]
            created_at = comment.created_time or comment.created_at
            platform_label = (
                "Facebook" if comment.platform == SocialCommentPlatform.facebook else "Instagram"
            )
            comment_summaries.append({
                "id": str(comment.id),
                "subject": f"{platform_label} comment",
                "status": "comment",
                "updated_at": created_at.strftime("%Y-%m-%d %H:%M") if created_at else "N/A",
                "preview": comment.message or "No message text",
                "channel": "comments",
                "platform_label": platform_label,
                "comment_count": entry.get("count", 1),
                "older_comments": [
                    {
                        "id": str(older.id),
                        "label": older.created_time.strftime("%b %d, %H:%M") if older.created_time else "View",
                        "href": f"/admin/crm/inbox?channel=comments&comment_id={older.id}",
                    }
                    for older in (entry.get("comments") or [])[1:4]
                ],
                "older_more": max((entry.get("count") or 0) - 4, 0),
                "sort_at": created_at,
                "href": f"/admin/crm/inbox?channel=comments&comment_id={comment.id}",
            })

    if comment_summaries:
        merged_recent = recent_conversations + comment_summaries
        merged_recent.sort(
            key=lambda item: item.get("sort_at") or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        recent_conversations = merged_recent[:5]

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

    return {
        "id": str(contact.id),
        "name": contact.display_name or contact.email or "Unknown",
        "email": contact.email,
        "phone": contact.phone,
        "company": company,
        "is_active": contact.is_active,
        "avatar_initials": _get_initials(contact.display_name or contact.email),
        "channels": channels,
        "tags": list(tags)[:5],
        "subscriber": None,  # Subscriber info removed
        "recent_tickets": recent_tickets,
        "recent_projects": recent_projects,
        "recent_tasks": recent_tasks,
        "notes": contact.notes,
        "total_conversations": total_conversations,
        "conversations": conversations_summary,
        "recent_conversations": recent_conversations,
        "resolved_conversations": resolved_conversations,
        "avg_response_time": "N/A",
    }


async def _load_comments_context(
    db: Session,
    search: str | None,
    comment_id: str | None,
    fetch: bool = True,
) -> tuple[list[dict], SocialComment | None, list]:
    comments = []
    selected_comment = None
    comment_replies = []
    did_sync = False

    if fetch:
        last_sync_raw = resolve_value(db, SettingDomain.comms, "comments_last_sync_at")
        should_sync = True
        if isinstance(last_sync_raw, str) and last_sync_raw.strip():
            try:
                last_sync = datetime.fromisoformat(last_sync_raw.strip())
                if last_sync.tzinfo is None:
                    last_sync = last_sync.replace(tzinfo=timezone.utc)
                should_sync = (datetime.now(timezone.utc) - last_sync).total_seconds() > 120
            except ValueError:
                should_sync = True
        if should_sync:
            try:
                await comments_service.fetch_and_store_social_comments(db)
                did_sync = True
                domain_settings_service.DomainSettings(SettingDomain.comms).upsert_by_key(
                    db,
                    "comments_last_sync_at",
                    DomainSettingUpdate(
                        value_type=SettingValueType.string,
                        value_text=datetime.now(timezone.utc).isoformat(),
                    ),
                )
            except Exception as exc:
                logger.info("crm_inbox_comments_fetch_failed %s", exc)
    if did_sync:
        _comment_list_cache.clear()
        _comment_thread_cache.clear()

    list_cache_key = _comment_cache_key("comments_list", search)
    cached_comments = _cache_get(_comment_list_cache, list_cache_key)
    if cached_comments is not None:
        comments = cached_comments
    else:
        comments = comments_service.list_social_comments(db, search=search, limit=50)
        _cache_set(_comment_list_cache, list_cache_key, comments)
    grouped_comments = _group_comment_authors(comments)
    if comment_id:
        selected_comment = next(
            (comment for comment in comments if str(comment.id) == str(comment_id)),
            None,
        )
        if not selected_comment:
            selected_comment = comments_service.get_social_comment(db, comment_id)
    if not selected_comment and comments:
        selected_comment = comments[0]
    if selected_comment:
        thread_cache_key = _comment_cache_key(
            "comment_thread", str(selected_comment.id)
        )
        cached_replies = _cache_get(_comment_thread_cache, thread_cache_key)
        if cached_replies is not None:
            comment_replies = cached_replies
        else:
            comment_replies = comments_service.list_social_comment_replies(
                db, str(selected_comment.id)
            )
            _cache_set(_comment_thread_cache, thread_cache_key, comment_replies)
    return grouped_comments, selected_comment, comment_replies


@router.get("/inbox", response_class=HTMLResponse)
async def inbox(
    request: Request,
    db: Session = Depends(get_db),
    channel: str | None = None,
    status: str | None = None,
    search: str | None = None,
    conversation_id: str | None = None,
    comment_id: str | None = None,
):
    """Omni-channel inbox view."""
    from app.web.admin import get_current_user, get_sidebar_stats
    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)

    comments_mode = channel == "comments"
    comments: list[dict] = []
    selected_comment = None
    comment_replies: list[dict] = []
    conversations: list[dict] = []
    selected_conversation = None
    messages: list[dict] = []
    contact_details = None

    if comments_mode:
        comments, selected_comment, comment_replies = await _load_comments_context(
            db,
            search=search,
            comment_id=comment_id,
            fetch=False,
        )
        if conversation_id:
            try:
                conv = conversation_service.Conversations.get(db, conversation_id)
                selected_conversation = _format_conversation_for_template(conv, db)
                if conv.contact:
                    contact_details = _format_contact_for_template(conv.contact, db)
            except Exception:
                pass

    # Use service layer methods for inbox queries
    if not comments_mode:
        # Parse channel and status enums
        channel_enum = None
        status_enum = None
        if channel:
            try:
                channel_enum = ChannelType(channel)
            except ValueError:
                pass
        if status:
            try:
                status_enum = ConversationStatus(status)
            except ValueError:
                pass

        # Use service method for listing conversations
        exclude_superseded = status != ConversationStatus.resolved.value if status else True
        conversations_raw = inbox_service.list_inbox_conversations(
            db,
            channel=channel_enum,
            status=status_enum,
            search=search,
            exclude_superseded_resolved=exclude_superseded,
            limit=50,
        )
        conversations = [
            _format_conversation_for_template(
                conv,
                db,
                latest_message=latest_message,
                unread_count=unread_count,
            )
            for conv, latest_message, unread_count in conversations_raw
        ]

        target_conv_id = conversation_id
        if not target_conv_id and conversations:
            target_conv_id = conversations[0]["id"]

        if target_conv_id:
            try:
                conv = conversation_service.Conversations.get(db, target_conv_id)
                selected_conversation = _format_conversation_for_template(conv, db)
            except Exception:
                pass

    # Use service methods for stats
    stats = inbox_service.get_inbox_stats(db)
    channel_stats = inbox_service.get_channel_stats(db)

    email_channel = _get_email_channel_state(db)
    email_setup = request.query_params.get("email_setup")
    email_error = request.query_params.get("email_error")
    email_error_detail = request.query_params.get("email_error_detail")
    new_error = request.query_params.get("new_error")
    new_error_detail = request.query_params.get("new_error_detail")
    reply_error = request.query_params.get("reply_error")
    reply_error_detail = request.query_params.get("reply_error_detail")

    assignment_options = _load_crm_agent_team_options(db)
    from app.logic import private_note_logic

    return templates.TemplateResponse(
        "admin/crm/inbox.html",
        {
            "request": request,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "active_page": "inbox",
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
            "search": search,
            "email_channel": email_channel,
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
        },
    )


@router.get("/inbox/comments/list", response_class=HTMLResponse)
async def inbox_comments_list(
    request: Request,
    db: Session = Depends(get_db),
    search: str | None = None,
    comment_id: str | None = None,
):
    comments, selected_comment, _ = await _load_comments_context(
        db,
        search=search,
        comment_id=comment_id,
        fetch=True,
    )
    return templates.TemplateResponse(
        "admin/crm/_comment_list.html",
        {
            "request": request,
            "comments": comments,
            "selected_comment": selected_comment,
            "selected_comment_id": str(selected_comment.id) if selected_comment else None,
            "search": search,
        },
    )


@router.get("/inbox/comments/thread", response_class=HTMLResponse)
async def inbox_comments_thread(
    request: Request,
    db: Session = Depends(get_db),
    search: str | None = None,
    comment_id: str | None = None,
):
    _, selected_comment, comment_replies = await _load_comments_context(
        db,
        search=search,
        comment_id=comment_id,
        fetch=False,
    )
    return templates.TemplateResponse(
        "admin/crm/_comment_thread.html",
        {
            "request": request,
            "selected_comment": selected_comment,
            "comment_replies": comment_replies,
        },
    )


@router.get("/inbox/settings", response_class=HTMLResponse)
async def inbox_settings(
    request: Request,
    db: Session = Depends(get_db),
):
    """Connector settings for CRM inbox channels."""
    from app.web.admin import get_current_user, get_sidebar_stats
    from app.services import crm as crm_service
    from app.services import person as person_service
    current_user = get_current_user(request)
    sidebar_stats = get_sidebar_stats(db)

    email_channel = _get_email_channel_state(db)
    whatsapp_channel = _get_whatsapp_channel_state(db)
    email_setup = request.query_params.get("email_setup")
    email_error = request.query_params.get("email_error")
    email_error_detail = request.query_params.get("email_error_detail")
    email_warning = request.query_params.get("email_warning")
    email_warning_detail = request.query_params.get("email_warning_detail")
    whatsapp_setup = request.query_params.get("whatsapp_setup")
    whatsapp_error = request.query_params.get("whatsapp_error")
    team_setup = request.query_params.get("team_setup")
    team_error = request.query_params.get("team_error")
    team_error_detail = request.query_params.get("team_error_detail")
    agent_setup = request.query_params.get("agent_setup")
    agent_error = request.query_params.get("agent_error")
    agent_error_detail = request.query_params.get("agent_error_detail")
    assignment_setup = request.query_params.get("assignment_setup")
    assignment_error = request.query_params.get("assignment_error")
    assignment_error_detail = request.query_params.get("assignment_error_detail")

    # Meta (Facebook/Instagram) status
    meta_setup = request.query_params.get("meta_setup")
    meta_error = request.query_params.get("meta_error")
    meta_error_detail = request.query_params.get("meta_error_detail")
    meta_disconnected = request.query_params.get("meta_disconnected")
    meta_pages = request.query_params.get("pages")  # from OAuth callback
    meta_instagram = request.query_params.get("instagram")  # from OAuth callback

    # Get Meta connection status
    from app.web.admin.meta_oauth import get_meta_connection_status
    meta_status = get_meta_connection_status(db)

    teams = crm_service.teams.list(
        db=db,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    agents = crm_service.agents.list(
        db=db,
        person_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    agent_teams = crm_service.agent_teams.list(
        db=db,
        agent_id=None,
        team_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )
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
    people_by_id = {str(person.id): person for person in people}
    for agent in agents:
        person_key = str(agent.person_id)
        if person_key not in people_by_id:
            person = db.get(Person, agent.person_id)
            if person:
                people_by_id[person_key] = person
    teams_by_id = {str(team.id): team for team in teams}

    return templates.TemplateResponse(
        "admin/crm/inbox_settings.html",
        {
            "request": request,
            "current_user": current_user,
            "sidebar_stats": sidebar_stats,
            "active_page": "inbox",
            "email_channel": email_channel,
            "whatsapp_channel": whatsapp_channel,
            "email_setup": email_setup,
            "email_error": email_error,
            "email_error_detail": email_error_detail,
            "email_warning": email_warning,
            "email_warning_detail": email_warning_detail,
            "whatsapp_setup": whatsapp_setup,
            "whatsapp_error": whatsapp_error,
            "team_setup": team_setup,
            "team_error": team_error,
            "team_error_detail": team_error_detail,
            "agent_setup": agent_setup,
            "agent_error": agent_error,
            "agent_error_detail": agent_error_detail,
            "assignment_setup": assignment_setup,
            "assignment_error": assignment_error,
            "assignment_error_detail": assignment_error_detail,
            "meta_setup": meta_setup,
            "meta_error": meta_error,
            "meta_error_detail": meta_error_detail,
            "meta_disconnected": meta_disconnected,
            "meta_pages": meta_pages,
            "meta_instagram": meta_instagram,
            "meta_status": meta_status,
            "teams": teams,
            "agents": agents,
            "agent_teams": agent_teams,
            "people": people,
            "people_by_id": people_by_id,
            "teams_by_id": teams_by_id,
        },
    )


@router.post("/inbox/teams", response_class=HTMLResponse)
async def create_crm_team(
    request: Request,
    name: str = Form(...),
    notes: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.schemas.crm.team import TeamCreate
    from app.services import crm as crm_service

    try:
        payload = TeamCreate(
            name=name.strip(),
            notes=notes.strip() if notes else None,
        )
        crm_service.teams.create(db, payload)
        return RedirectResponse(
            url="/admin/crm/inbox/settings?team_setup=1", status_code=303
        )
    except Exception as exc:
        detail = quote(str(exc) or "Failed to create team", safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox/settings?team_error=1&team_error_detail={detail}",
            status_code=303,
        )


@router.post("/inbox/agents", response_class=HTMLResponse)
async def create_crm_agent(
    request: Request,
    person_id: str = Form(...),
    title: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.schemas.crm.team import AgentCreate
    from app.services import crm as crm_service

    try:
        existing = crm_service.agents.list(
            db=db,
            person_id=person_id,
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=1,
            offset=0,
        )
        if existing:
            raise ValueError("Agent already exists for that person")
        payload = AgentCreate(
            person_id=coerce_uuid(person_id),
            title=title.strip() if title else None,
        )
        crm_service.agents.create(db, payload)
        return RedirectResponse(
            url="/admin/crm/inbox/settings?agent_setup=1", status_code=303
        )
    except Exception as exc:
        detail = quote(str(exc) or "Failed to create agent", safe="")
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
    from app.schemas.crm.team import AgentTeamCreate
    from app.services import crm as crm_service

    try:
        payload = AgentTeamCreate(
            agent_id=coerce_uuid(agent_id),
            team_id=coerce_uuid(team_id),
        )
        crm_service.agent_teams.create(db, payload)
        return RedirectResponse(
            url="/admin/crm/inbox/settings?assignment_setup=1", status_code=303
        )
    except Exception as exc:
        detail = quote(str(exc) or "Failed to assign agent to team", safe="")
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
):
    """Partial template for conversation list (HTMX)."""
    # Parse enums
    channel_enum = None
    status_enum = None
    if channel:
        try:
            channel_enum = ChannelType(channel)
        except ValueError:
            pass
    if status:
        try:
            status_enum = ConversationStatus(status)
        except ValueError:
            pass

    # Use service method
    exclude_superseded = status != ConversationStatus.resolved.value if status else True
    conversations_raw = inbox_service.list_inbox_conversations(
        db,
        channel=channel_enum,
        status=status_enum,
        search=search,
        exclude_superseded_resolved=exclude_superseded,
        limit=50,
    )
    conversations = [
        _format_conversation_for_template(
            conv,
            db,
            latest_message=latest_message,
            unread_count=unread_count,
        )
        for conv, latest_message, unread_count in conversations_raw
    ]

    return templates.TemplateResponse(
        "admin/crm/_conversation_list.html",
        {
            "request": request,
            "conversations": conversations,
            "current_channel": channel,
            "current_status": status,
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
    try:
        conv = conversation_service.Conversations.get(db, conversation_id)
    except Exception:
        return HTMLResponse(
            "<div class='p-8 text-center text-slate-500'>Conversation not found</div>"
        )

    conversation = _format_conversation_for_template(conv, db)

    messages_raw = conversation_service.Messages.list(
        db=db,
        conversation_id=conversation_id,
        channel_type=None,
        direction=None,
        status=None,
        order_by="created_at",
        order_dir="asc",
        limit=100,
        offset=0,
    )
    messages = [_format_message_for_template(m, db) for m in messages_raw]
    last_seen_at = None
    if messages_raw:
        last_seen_at = max(
            [
                msg.received_at or msg.sent_at or msg.created_at
                for msg in messages_raw
                if msg.received_at or msg.sent_at or msg.created_at
            ],
            default=None,
        )
    from app.web.admin import get_current_user
    current_user = get_current_user(request)
    current_roles = _get_current_roles(request)
    _mark_conversation_read(db, conversation_id, current_user.get("person_id"), last_seen_at)
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


@router.get("/inbox/attachment/{message_id}/{attachment_index}")
def inbox_attachment(
    message_id: str,
    attachment_index: int,
    db: Session = Depends(get_db),
):
    message = conversation_service.Messages.get(db, message_id)
    metadata = message.metadata_ if isinstance(message.metadata_, dict) else {}
    attachments = metadata.get("attachments")
    if not isinstance(attachments, list) or attachment_index < 0 or attachment_index >= len(attachments):
        return Response(status_code=404)
    meta_attachment = attachments[attachment_index]
    if not isinstance(meta_attachment, dict):
        return Response(status_code=404)
    attachment_id, url = _extract_meta_attachment(meta_attachment)
    if url:
        return RedirectResponse(url)

    token = None
    if message.channel_type == ChannelType.instagram_dm:
        ig_account_id = metadata.get("instagram_account_id")
        if ig_account_id:
            token = meta_oauth.get_token_for_instagram(db, str(ig_account_id))
    elif message.channel_type == ChannelType.facebook_messenger:
        page_id = metadata.get("page_id")
        if page_id:
            token = meta_oauth.get_token_for_page(db, str(page_id))

    if not token or not token.access_token or not attachment_id:
        return Response(status_code=404)

    try:
        version = resolve_value(db, SettingDomain.comms, "meta_graph_api_version")
        if not version:
            version = settings.meta_graph_api_version
        base_url = f"https://graph.facebook.com/{version}"
        response = httpx.get(
            f"{base_url.rstrip('/')}/{attachment_id}",
            params={"access_token": token.access_token, "fields": "url,media_url"},
            timeout=10,
        )
        if response.status_code >= 400:
            logger.warning(
                "crm_inbox_attachment_fetch_failed message_id=%s attachment_id=%s status=%s",
                message_id,
                attachment_id,
                response.status_code,
            )
            return Response(status_code=404)
        payload = response.json() if response.content else {}
    except httpx.HTTPError:
        return Response(status_code=404)

    resolved_url = payload.get("url") or payload.get("media_url")
    if not resolved_url:
        return Response(status_code=404)
    return RedirectResponse(resolved_url)


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

    conversation = db.get(Conversation, coerce_uuid(conversation_id))
    if not conversation:
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Conversation not found</div>",
            status_code=404,
        )

    # Use service method for assignment
    assigned_by_id = get_current_user(request).get("person_id") or None
    conversation_service.assign_conversation(
        db,
        conversation_id=conversation_id,
        agent_id=agent_id,
        team_id=team_id,
        assigned_by_id=assigned_by_id,
        update_lead_owner=True,
    )

    # Get contact with relationships
    contact = contact_service.get_person_with_relationships(db, str(conversation.contact_id))
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
                "conversation_id": str(conversation.id),
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
    conversation = db.get(Conversation, coerce_uuid(conversation_id))
    if not conversation:
        return HTMLResponse(
            "<div class='p-6 text-center text-slate-500'>Conversation not found</div>",
            status_code=404,
        )

    person_value = person_id.strip()
    channel_value = (channel_type or "").strip() or None
    address_value = (channel_address or "").strip() or None
    source_person_id = conversation.person_id

    resolved_channel = None
    if channel_value:
        try:
            resolved_channel = ChannelType(channel_value)
        except ValueError:
            return HTMLResponse(
                "<div class='p-6 text-center text-slate-500'>Invalid channel type</div>",
                status_code=400,
            )

    try:
        conversation_service.resolve_conversation_contact(
            db,
            conversation_id=conversation_id,
            person_id=person_value,
            channel_type=resolved_channel,
            address=address_value,
        )
        target_person_id = coerce_uuid(person_value)
        if source_person_id and source_person_id != target_person_id:
            from app.web.admin import get_current_user
            current_user = get_current_user(request)
            merged_by_id = None
            if current_user and current_user.get("person_id"):
                merged_by_id = coerce_uuid(current_user["person_id"])
            person_service.people.merge(
                db,
                source_id=source_person_id,
                target_id=target_person_id,
                merged_by_id=merged_by_id,
            )
    except Exception as exc:
        return HTMLResponse(
            f"<div class='p-6 text-center text-slate-500'>Resolve failed: {exc}</div>",
            status_code=400,
        )

    # Use service method for eager-loaded person
    contact = contact_service.get_person_with_relationships(db, str(conversation.person_id))
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
            "conversation_id": str(conversation.id),
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
    message: str = Form(...),
    attachments: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Send a message in a conversation."""
    from app.web.admin import get_current_user
    current_user = get_current_user(request)

    try:
        conv = conversation_service.Conversations.get(db, conversation_id)
    except Exception:
        return HTMLResponse(
            "<div class='p-8 text-center text-red-500'>Conversation not found</div>"
        )

    # Use service method for channel type detection with fallback
    channel_type = conversation_service.get_reply_channel_type(db, conversation_id)
    if not channel_type:
        # Fallback: use first contact channel or default to email
        if conv.contact and conv.contact.channels:
            channel_type = ChannelType(conv.contact.channels[0].channel_type.value)
        else:
            channel_type = ChannelType.email

    author_id = current_user.get("person_id") if current_user.get("person_id") else None

    result_msg = None
    attachments_payload: list[dict] = []
    if attachments:
        try:
            attachments_payload = json.loads(attachments)
        except Exception:
            attachments_payload = []

    try:
        result_msg = inbox_service.send_message(
            db,
            InboxSendRequest(
                conversation_id=conv.id,
                channel_type=channel_type,
                body=message,
            ),
            author_id=author_id,
        )
    except Exception:
        result_msg = conversation_service.Messages.create(
            db,
            MessageCreate(
                conversation_id=conv.id,
                channel_type=channel_type,
                direction=MessageDirection.outbound,
                status=MessageStatus.failed,
                body=message,
                sent_at=datetime.now(timezone.utc),
            ),
        )
    if result_msg:
        _apply_message_attachments(db, result_msg, attachments_payload)

    if result_msg and result_msg.status == MessageStatus.failed:
        detail = quote("Meta rejected the outbound message. Check logs.", safe="")
        url = f"/admin/crm/inbox?conversation_id={conversation_id}&reply_error=1&reply_error_detail={detail}"
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=204, headers={"HX-Redirect": url})
        return RedirectResponse(url=url, status_code=303)

    try:
        conversation = _format_conversation_for_template(conv, db)
        messages_raw = conversation_service.Messages.list(
            db=db,
            conversation_id=conversation_id,
            channel_type=None,
            direction=None,
            status=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
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
    from app.services.crm import private_notes as private_notes_service

    if not private_note_logic.USE_PRIVATE_NOTE_LOGIC_SERVICE:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    try:
        conversation_service.Conversations.get(db, conversation_id)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    current_user = get_current_user(request) or {}
    author_id = current_user.get("person_id")

    try:
        note = private_notes_service.send_private_note(
            db=db,
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
        conversation_service.MessageAttachments.create(
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
    from app.services.crm import private_notes as private_notes_service

    if not payload.body or not payload.body.strip():
        return JSONResponse({"detail": "Private note body is empty"}, status_code=400)

    try:
        conversation_service.Conversations.get(db, conversation_id)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    current_user = get_current_user(request) or {}
    author_id = current_user.get("person_id")

    attachments = payload.attachments or []

    try:
        note = private_notes_service.send_private_note(
            db=db,
            conversation_id=conversation_id,
            author_id=author_id,
            body=payload.body,
            requested_visibility=payload.visibility,
        )
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    _apply_message_attachments(db, note, attachments)
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


@router.post("/inbox/conversation/{conversation_id}/attachments")
async def upload_conversation_attachments(
    conversation_id: str,
    files: UploadFile | list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
):
    """Upload attachments for a conversation message/private note."""
    import app.services.crm.message_attachments as message_attachment_service

    conversation = db.get(Conversation, coerce_uuid(conversation_id))
    if not conversation:
        return JSONResponse({"detail": "Conversation not found"}, status_code=404)

    prepared = message_attachment_service.prepare_message_attachments(files)
    if not prepared:
        return JSONResponse({"detail": "No attachments provided"}, status_code=400)
    saved = message_attachment_service.save_message_attachments(prepared)
    return JSONResponse({"attachments": saved})






@router.post("/inbox/conversation/{conversation_id}/status", response_class=HTMLResponse)
async def update_conversation_status(
    request: Request,
    conversation_id: str,
    new_status: str = Query(...),
    db: Session = Depends(get_db),
):
    """Update conversation status."""
    try:
        status_enum = ConversationStatus(new_status)
        conversation_service.Conversations.update(
            db,
            conversation_id,
            ConversationUpdate(status=status_enum),
        )
    except (ValueError, Exception):
        pass

    if request.headers.get("HX-Target") == "message-thread":
        try:
            conv = conversation_service.Conversations.get(db, conversation_id)
        except Exception:
            return HTMLResponse(
                "<div class='p-8 text-center text-slate-500'>Conversation not found</div>"
            )

        conversation = _format_conversation_for_template(conv, db)
        messages_raw = conversation_service.Messages.list(
            db=db,
            conversation_id=conversation_id,
            channel_type=None,
            direction=None,
            status=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        messages = [_format_message_for_template(m, db) for m in messages_raw]
        from app.web.admin import get_current_user
        current_user = get_current_user(request)
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
    contact_address: str = Form(...),
    contact_name: str | None = Form(None),
    subject: str | None = Form(None),
    message: str = Form(...),
    db: Session = Depends(get_db),
):
    """Start a new outbound conversation."""
    from app.web.admin import get_current_user
    current_user = get_current_user(request)

    try:
        channel_enum = ChannelType(channel_type)
    except ValueError:
        return RedirectResponse(
            url="/admin/crm/inbox?new_error=1&new_error_detail=Invalid%20channel",
            status_code=303,
        )

    address = contact_address.strip()
    if not address:
        return RedirectResponse(
            url="/admin/crm/inbox?new_error=1&new_error_detail=Recipient%20is%20required",
            status_code=303,
        )

    body = message.strip()
    if not body:
        return RedirectResponse(
            url="/admin/crm/inbox?new_error=1&new_error_detail=Message%20body%20is%20required",
            status_code=303,
        )

    contact, _ = contact_service.get_or_create_contact_by_channel(
        db,
        channel_enum,
        address,
        contact_name.strip() if contact_name else None,
    )
    conversation = conversation_service.resolve_open_conversation_for_channel(
        db, str(contact.id), channel_enum
    )
    if not conversation:
        conversation = conversation_service.Conversations.create(
            db,
            ConversationCreate(
                person_id=contact.id,
                subject=subject.strip() if subject and channel_enum == ChannelType.email else None,
            ),
        )

    try:
        inbox_service.send_message(
            db,
            InboxSendRequest(
                conversation_id=conversation.id,
                channel_type=channel_enum,
                subject=subject.strip() if subject and channel_enum == ChannelType.email else None,
                body=body,
            ),
            author_id=current_user.get("person_id") if current_user else None,
        )
    except Exception as exc:
        detail = quote(str(exc) or "Failed to send message", safe="")
        return RedirectResponse(
            url=f"/admin/crm/inbox?new_error=1&new_error_detail={detail}",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/admin/crm/inbox?conversation_id={conversation.id}",
        status_code=303,
    )


@router.post("/inbox/email-connector", response_class=HTMLResponse)
async def configure_email_connector(
    request: Request,
    name: str = Form("CRM Email"),
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
    pop3_host: str | None = Form(None),
    pop3_port: str | None = Form(None),
    pop3_use_ssl: str | None = Form(None),
    poll_interval_seconds: str | None = Form(None),
    db: Session = Depends(get_db),
):
    form = await request.form()
    polling_enabled_values = (
        form.getlist("polling_enabled") if "polling_enabled" in form else []
    )
    polling_enabled_value = (
        _as_str(polling_enabled_values[-1])
        if polling_enabled_values
        else polling_enabled
    )
    smtp_port_value = _as_str(form.get("smtp_port")) if "smtp_port" in form else smtp_port
    imap_port_value = _as_str(form.get("imap_port")) if "imap_port" in form else imap_port
    pop3_port_value = _as_str(form.get("pop3_port")) if "pop3_port" in form else pop3_port
    smtp_host_value = _as_str(form.get("smtp_host")) if "smtp_host" in form else smtp_host
    imap_host_value = _as_str(form.get("imap_host")) if "imap_host" in form else imap_host
    pop3_host_value = _as_str(form.get("pop3_host")) if "pop3_host" in form else pop3_host
    # Track explicit form submissions so clearing fields removes stored metadata.
    imap_host_provided = "imap_host" in form
    pop3_host_provided = "pop3_host" in form
    logger.info(
        "crm_inbox_email_form_ports %s",
        _safe_log_json(
            {
                "smtp_port": _as_str(form.get("smtp_port")),
                "imap_port": _as_str(form.get("imap_port")),
                "pop3_port": _as_str(form.get("pop3_port")),
                "smtp_host": _as_str(form.get("smtp_host")),
                "imap_host": _as_str(form.get("imap_host")),
                "pop3_host": _as_str(form.get("pop3_host")),
            }
        ),
    )
    email_channel = _get_email_channel_state(db)
    smtp = None
    smtp_warning = None
    smtp_on = _as_bool(smtp_enabled) if smtp_enabled is not None else bool(smtp_host_value)
    if smtp_on and smtp_host_value:
        existing_smtp = email_channel.get("smtp") if email_channel else None
        smtp = {
            "host": smtp_host_value.strip(),
            "port": _as_int(
                smtp_port_value,
                existing_smtp.get("port") if isinstance(existing_smtp, dict) else 587,
            ),
            "use_tls": _as_bool(smtp_use_tls)
            if smtp_use_tls is not None
            else bool(existing_smtp.get("use_tls")) if isinstance(existing_smtp, dict) else False,
            "use_ssl": _as_bool(smtp_use_ssl)
            if smtp_use_ssl is not None
            else bool(existing_smtp.get("use_ssl")) if isinstance(existing_smtp, dict) else False,
        }

    imap = None
    if imap_host_value:
        existing_imap = email_channel.get("imap") if email_channel else None
        imap = {
            "host": imap_host_value.strip(),
            "port": _as_int(
                imap_port_value,
                existing_imap.get("port") if isinstance(existing_imap, dict) else 993,
            ),
            "use_ssl": _as_bool(imap_use_ssl)
            if imap_use_ssl is not None
            else bool(existing_imap.get("use_ssl")) if isinstance(existing_imap, dict) else True,
            "mailbox": imap_mailbox.strip()
            if imap_mailbox
            else existing_imap.get("mailbox") if isinstance(existing_imap, dict) else "INBOX",
        }

    pop3 = None
    if pop3_host_value:
        existing_pop3 = email_channel.get("pop3") if email_channel else None
        pop3 = {
            "host": pop3_host_value.strip(),
            "port": _as_int(
                pop3_port_value,
                existing_pop3.get("port") if isinstance(existing_pop3, dict) else 995,
            ),
            "use_ssl": _as_bool(pop3_use_ssl)
            if pop3_use_ssl is not None
            else bool(existing_pop3.get("use_ssl")) if isinstance(existing_pop3, dict) else True,
        }

    logger.info(
        "crm_inbox_email_save_request %s",
        _safe_log_json(
            {
                "smtp_on": smtp_on,
                "smtp_host": smtp_host_value.strip() if smtp_host_value else None,
                "smtp_port": _as_int(smtp_port_value, 587) if smtp_port_value else None,
                "imap_host": imap_host_value.strip() if imap_host_value else None,
                "imap_port": _as_int(imap_port_value, 993) if imap_port_value else None,
                "pop3_host": pop3_host_value.strip() if pop3_host_value else None,
                "pop3_port": _as_int(pop3_port_value, 995) if pop3_port_value else None,
            }
        ),
    )

    if smtp and not _as_bool(skip_smtp_test):
        smtp_username = username
        smtp_password = password
        if email_channel and email_channel.get("auth_config"):
            auth_config = email_channel["auth_config"]
            if not smtp_username:
                smtp_username = auth_config.get("username")
            if not smtp_password:
                smtp_password = auth_config.get("password")
        smtp_test_config = dict(smtp)
        if smtp_username:
            smtp_test_config["username"] = smtp_username
        if smtp_password:
            smtp_test_config["password"] = smtp_password
        ok, error = email_service.test_smtp_connection(smtp_test_config)
        if not ok:
            smtp_warning = error or "SMTP test failed"

    try:
        if email_channel and email_channel.get("connector_id"):
            config = connector_service.connector_configs.get(
                db, email_channel["connector_id"]
            )
            metadata = dict(config.metadata_ or {}) if isinstance(config.metadata_, dict) else {}
            if smtp_on and smtp:
                metadata["smtp"] = smtp
            elif smtp_enabled is not None and not smtp_on:
                metadata.pop("smtp", None)

            if imap:
                metadata["imap"] = imap
            elif imap_host_provided and not imap_host_value:
                metadata.pop("imap", None)

            if pop3:
                metadata["pop3"] = pop3
            elif pop3_host_provided and not pop3_host_value:
                metadata.pop("pop3", None)

            auth_config = dict(config.auth_config or {}) if isinstance(config.auth_config, dict) else {}
            if username:
                auth_config["username"] = username.strip()
            if password:
                auth_config["password"] = password
            if from_email:
                auth_config["from_email"] = from_email.strip()
            if from_name:
                auth_config["from_name"] = from_name.strip()

            connector_service.connector_configs.update(
                db,
                email_channel["connector_id"],
                ConnectorConfigUpdate(
                    name=name.strip() if name else config.name,
                    connector_type=ConnectorType.email,
                    auth_config=auth_config,
                    metadata_=metadata,
                ),
            )
            logger.info(
                "crm_inbox_email_save_update %s",
                _safe_log_json(
                    {
                        "connector_id": email_channel["connector_id"],
                        "metadata": metadata,
                    }
                ),
            )
            integration_service.integration_targets.update(
                db,
                email_channel["target_id"],
                IntegrationTargetUpdate(name=name.strip() if name else None),
            )
            target_id = email_channel["target_id"]
        else:
            auth_config = {}
            if username:
                auth_config["username"] = username.strip()
            if password:
                auth_config["password"] = password
            if from_email:
                auth_config["from_email"] = from_email.strip()
            if from_name:
                auth_config["from_name"] = from_name.strip()
            metadata = {}
            if smtp_on and smtp:
                metadata["smtp"] = smtp
            if imap:
                metadata["imap"] = imap
            if pop3:
                metadata["pop3"] = pop3
            target = inbox_service.create_email_connector_target(
                db,
                name=name.strip() if name else "CRM Email",
                smtp=smtp,
                imap=imap,
                pop3=pop3,
                auth_config=auth_config or None,
            )
            target_id = str(target.id)
            logger.info(
                "crm_inbox_email_save_create %s",
                _safe_log_json(
                    {
                        "target_id": target_id,
                        "metadata": {"smtp": smtp, "imap": imap, "pop3": pop3},
                    }
                ),
            )

        interval_seconds = _as_int(poll_interval_seconds)
        polling_flag_set = bool(polling_enabled_values) or polling_enabled is not None
        polling_on = (
            _as_bool(polling_enabled_value)
            if polling_flag_set
            else bool(interval_seconds)
            or bool(email_channel and email_channel.get("polling_active"))
            or bool(email_channel and email_channel.get("poll_interval_seconds"))
        )
        if polling_on and not interval_seconds:
            # Default to existing interval or 5 minutes so saving does not disable polling.
            interval_seconds = (
                email_channel.get("poll_interval_seconds") if email_channel else None
            ) or 300
        if polling_flag_set and not polling_on:
            _disable_email_polling_job(db, target_id)
        elif polling_on and (imap or pop3):
            inbox_service.ensure_email_polling_job(
                db,
                target_id=target_id,
                interval_seconds=interval_seconds or 300,
                name=f"{name.strip() if name else 'CRM Email'} Polling",
            )
    except Exception:
        next_url = request.query_params.get("next")
        if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/admin/crm/inbox"
        return RedirectResponse(url=f"{next_url}?email_error=1", status_code=303)

    next_url = request.query_params.get("next")
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/admin/crm/inbox"
    params = ["email_setup=1"]
    if smtp_warning:
        params.append("email_warning=1")
        params.append(f"email_warning_detail={quote(smtp_warning, safe='')}")
    return RedirectResponse(url=f"{next_url}?{'&'.join(params)}", status_code=303)


@router.post("/inbox/whatsapp-connector", response_class=HTMLResponse)
async def configure_whatsapp_connector(
    request: Request,
    name: str = Form("CRM WhatsApp"),
    access_token: str | None = Form(None),
    phone_number_id: str | None = Form(None),
    base_url: str | None = Form(None),
    db: Session = Depends(get_db),
):
    whatsapp_channel = _get_whatsapp_channel_state(db)
    metadata = {}
    if phone_number_id:
        metadata["phone_number_id"] = phone_number_id.strip()

    try:
        if whatsapp_channel and whatsapp_channel.get("connector_id"):
            config = connector_service.connector_configs.get(
                db, whatsapp_channel["connector_id"]
            )
            merged_metadata = dict(config.metadata_ or {}) if isinstance(config.metadata_, dict) else {}
            if phone_number_id:
                merged_metadata["phone_number_id"] = phone_number_id.strip()
            auth_config = dict(config.auth_config or {}) if isinstance(config.auth_config, dict) else {}
            if access_token:
                auth_config["access_token"] = access_token.strip()

            update_payload = ConnectorConfigUpdate(
                name=name.strip() if name else None,
                connector_type=ConnectorType.whatsapp,
                auth_config=auth_config,
                base_url=base_url.strip() if base_url else config.base_url,
                metadata_=merged_metadata or None,
            )
            connector_service.connector_configs.update(
                db,
                whatsapp_channel["connector_id"],
                update_payload,
            )
            integration_service.integration_targets.update(
                db,
                whatsapp_channel["target_id"],
                IntegrationTargetUpdate(name=name.strip() if name else None),
            )
        else:
            auth_config = None
            if access_token:
                auth_config = {"access_token": access_token.strip()}
            target = inbox_service.create_whatsapp_connector_target(
                db,
                name=name.strip() if name else "CRM WhatsApp",
                phone_number_id=phone_number_id.strip() if phone_number_id else None,
                auth_config=auth_config,
                base_url=base_url.strip() if base_url else None,
            )
            _ = target
    except Exception:
        next_url = request.query_params.get("next")
        if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/admin/crm/inbox/settings"
        return RedirectResponse(url=f"{next_url}?whatsapp_error=1", status_code=303)

    next_url = request.query_params.get("next")
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/admin/crm/inbox/settings"
    return RedirectResponse(url=f"{next_url}?whatsapp_setup=1", status_code=303)


@router.post("/inbox/email-poll", response_class=HTMLResponse)
async def poll_email_channel(db: Session = Depends(get_db)):
    try:
        result = inbox_service.poll_email_targets(db)
        processed = int(result.get("processed") or 0)
        return HTMLResponse(
            f"<p class='text-xs text-emerald-400'>Checked inbox: {processed} new message(s).</p>"
        )
    except Exception as exc:
        return HTMLResponse(
            f"<p class='text-xs text-red-400'>Email poll failed: {exc}</p>",
            status_code=400,
        )


@router.post("/inbox/email-polling/reset", response_class=HTMLResponse)
async def reset_email_polling_runs(
    request: Request,
    db: Session = Depends(get_db),
):
    email_channel = _get_email_channel_state(db)
    if email_channel and email_channel.get("target_id"):
        # Use service method to reset stuck runs
        integration_service.reset_stuck_runs(db, email_channel["target_id"])

    next_url = request.query_params.get("next")
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/admin/crm/inbox/settings"
    return RedirectResponse(url=f"{next_url}?email_setup=1", status_code=303)


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

    comment = comments_service.get_social_comment(db, comment_id)
    if not comment:
        return RedirectResponse(
            url=f"{next_url}?channel=comments&comment_id={comment_id}&reply_error=1",
            status_code=303,
        )

    try:
        await comments_service.reply_to_social_comment(db, comment, message.strip())
    except Exception as exc:
        logger.exception("social_comment_reply_failed comment_id=%s error=%s", comment_id, exc)
        detail = quote(str(exc) or "Reply failed", safe="")
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
        "person_id": "",
        "organization_id": "",
        "notes": "",
        "is_active": True,
    }
    context = _crm_base_context(request, db, "contacts")
    context.update(
        {
            "contact": contact,
            "organization_label": None,
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
    person_id: str | None = Form(None),
    organization_id: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    error = None
    contact: dict[str, str | bool] = {
        "display_name": (display_name or "").strip(),
        "email": (email or "").strip(),
        "phone": (phone or "").strip(),
        "person_id": (person_id or "").strip(),
        "organization_id": (organization_id or "").strip(),
        "notes": (notes or "").strip(),
        "is_active": is_active == "true",
    }
    try:
        display_name_value = contact["display_name"] if isinstance(contact["display_name"], str) else ""
        email_value_raw = contact["email"] if isinstance(contact["email"], str) else ""
        phone_value = contact["phone"] if isinstance(contact["phone"], str) else ""
        organization_id_value = contact["organization_id"] if isinstance(contact["organization_id"], str) else ""
        notes_value = contact["notes"] if isinstance(contact["notes"], str) else ""
        name_parts = display_name_value.split() if display_name_value else []
        first_name = name_parts[0] if name_parts else "Unknown"
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "Unknown"
        email_value = email_value_raw or f"contact-{uuid.uuid4().hex}@placeholder.local"
        payload = ContactCreate(
            first_name=first_name,
            last_name=last_name,
            display_name=display_name_value or None,
            email=email_value,
            phone=phone_value or None,
            organization_id=_coerce_uuid_optional(organization_id_value),
            notes=notes_value or None,
            is_active=bool(contact["is_active"]),
        )
        crm_service.contacts.create(db=db, payload=payload)
        return RedirectResponse(url="/admin/crm/contacts", status_code=303)
    except (ValidationError, ValueError) as exc:
        error = str(exc)
    except Exception as exc:
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
        "person_id": str(contact_obj.person_id) if contact_obj.person_id else "",
        "organization_id": str(contact_obj.organization_id) if contact_obj.organization_id else "",
        "notes": contact_obj.notes or "",
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
    person_id: str | None = Form(None),
    organization_id: str | None = Form(None),
    notes: str | None = Form(None),
    is_active: str | None = Form(None),
    db: Session = Depends(get_db),
):
    error = None
    contact: dict[str, str | bool] = {
        "id": contact_id,
        "display_name": (display_name or "").strip(),
        "email": (email or "").strip(),
        "phone": (phone or "").strip(),
        "person_id": (person_id or "").strip(),
        "organization_id": (organization_id or "").strip(),
        "notes": (notes or "").strip(),
        "is_active": is_active == "true",
    }
    try:
        display_name_value = contact["display_name"] if isinstance(contact["display_name"], str) else ""
        email_value = contact["email"] if isinstance(contact["email"], str) else ""
        phone_value = contact["phone"] if isinstance(contact["phone"], str) else ""
        organization_id_value = contact["organization_id"] if isinstance(contact["organization_id"], str) else ""
        notes_value = contact["notes"] if isinstance(contact["notes"], str) else ""
        payload = ContactUpdate(
            display_name=display_name_value or None,
            email=email_value or None,
            phone=phone_value or None,
            organization_id=_coerce_uuid_optional(organization_id_value),
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
    options = _load_crm_sales_options(db)
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
        "notes": lead_obj.notes or "",
        "is_active": lead_obj.is_active,
    }
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
        contact = db.get(Person, quote.person_id)

    context = _crm_base_context(request, db, "quotes")
    context.update(
        {
            "quote": quote,
            "items": items,
            "lead": lead,
            "contact": contact,
        }
    )
    return templates.TemplateResponse("admin/crm/quote_detail.html", context)


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
    item_description: str | list[str] | None = Form(None),
    item_quantity: str | list[str] | None = Form(None),
    item_unit_price: str | list[str] | None = Form(None),
    item_inventory_item_id: str | list[str] | None = Form(None),
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
        _as_list(item_description),
        _as_list(item_quantity),
        _as_list(item_unit_price),
        _as_list(item_inventory_item_id),
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
        payload = QuoteUpdate(
            person_id=coerce_uuid(resolved_person_id),
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
        error = str(exc)
    except Exception as exc:
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

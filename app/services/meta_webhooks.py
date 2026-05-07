"""Meta webhook processing service.

Handles incoming webhooks from Facebook (Messenger), Instagram (DMs), and
WhatsApp Business API (delivery status updates).
Processes webhook payloads and creates/updates messages in the CRM system.

Environment Variables:
    META_APP_SECRET: Required for webhook signature verification
    META_WEBHOOK_VERIFY_TOKEN: Token for webhook verification challenge
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import String, cast, func, or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.config import settings
from app.logging import get_logger
from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.comments import SocialCommentPlatform
from app.models.crm.conversation import Conversation, ConversationTag, Message
from app.models.crm.enums import (
    ChannelType,
    ConversationStatus,
    MessageDirection,
    MessageStatus,
)
from app.models.crm.sales import Lead
from app.models.domain_settings import SettingDomain
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.models.oauth_token import OAuthToken
from app.models.person import ChannelType as PersonChannelType
from app.models.person import PartyStatus, Person, PersonChannel
from app.schemas.crm.conversation import ConversationCreate, MessageCreate
from app.schemas.crm.inbox import (
    FacebookCommentPayload,
    FacebookMessengerWebhookPayload,
    InstagramCommentPayload,
    InstagramDMWebhookPayload,
    MetaWebhookPayload,
    WhatsAppStatusValue,
    _attachments_have_story_mention,
)
from app.schemas.crm.sales import LeadCreate
from app.services.crm import conversation as conversation_service
from app.services.crm.conversations import comments as comments_service
from app.services.crm.inbox.handlers.utils import post_process_inbound_message
from app.services.crm.inbox_dedup import (
    _build_inbound_dedupe_id,
    _find_duplicate_inbound_message,
)
from app.services.crm.sales.service import leads as leads_service
from app.services.person_identity import _is_meta_placeholder_name
from app.services.person_identity import ensure_person_channel as _ensure_person_channel_unified
from app.services.settings_spec import resolve_value
from app.services.webhook_dead_letter import write_dead_letter

logger = get_logger(__name__)

_META_IDENTITY_KEYS = {
    "account_id",
    "subscriber_id",
    "account_number",
    "subscriber_number",
    "customer_id",
    "email",
    "phone",
}

_META_ATTRIBUTION_KEYS = {
    "source",
    "type",
    "ref",
    "referer_uri",
    "ad_id",
    "adgroup_id",
    "adset_id",
    "campaign_id",
    "post_id",
    "product_id",
    "ctwa_clid",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
}

_META_ATTRIBUTION_NESTED_KEYS = {
    "referral",
    "ads_context_data",
    "ad",
    "campaign",
    "utm",
}


def _get_meta_graph_base_url(db: Session | None) -> str:
    if db:
        version = resolve_value(db, SettingDomain.comms, "meta_graph_api_version")
    else:
        version = None
    if not version:
        version = settings.meta_graph_api_version
    return f"https://graph.facebook.com/{version}"


def _get_facebook_access_token_override(db: Session) -> str | None:
    token = resolve_value(db, SettingDomain.comms, "meta_facebook_access_token_override")
    if isinstance(token, str):
        token = token.strip()
        return token or None
    return None


def _normalize_external_id(raw_id: str | None) -> tuple[str | None, str | None]:
    if not raw_id:
        return None, None
    if len(raw_id) <= 120:
        return raw_id, None
    digest = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()
    return digest, raw_id


def _normalize_external_ref(raw_id: str | None) -> str | None:
    if not raw_id:
        return None
    return raw_id if len(raw_id) <= 255 else None


def _normalize_phone_address(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return None
    return f"+{digits}"


def _mark_whatsapp_channel_invalid_from_status(
    person_channel: PersonChannel | None,
    errors: list[dict] | None,
) -> None:
    if person_channel is None or not errors:
        return
    error_code = None
    for item in errors:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if code is None:
            error_code = None
        else:
            try:
                error_code = int(code)
            except (TypeError, ValueError):
                error_code = None
        if error_code == 131026:
            metadata = person_channel.metadata_ if isinstance(person_channel.metadata_, dict) else {}
            validation = metadata.get("whatsapp_validation")
            payload = validation if isinstance(validation, dict) else {}
            payload["status"] = "invalid"
            payload["source"] = "meta_status_failed"
            payload["reason"] = "Meta later marked this WhatsApp number undeliverable"
            payload["error_code"] = 131026
            payload["updated_at"] = datetime.now(UTC).isoformat()
            metadata["whatsapp_validation"] = payload
            person_channel.metadata_ = metadata
            return


def _fetch_profile_name(
    access_token: str | None,
    user_id: str,
    fields: str,
    base_url: str,
) -> str | None:
    if not access_token:
        return None
    try:
        with httpx.Client(timeout=5) as client:
            response = client.get(
                f"{base_url.rstrip('/')}/{user_id}",
                params={"fields": fields, "access_token": access_token},
            )
        if response.status_code >= 400:
            if response.status_code in (401, 403):
                logger.warning(
                    "meta_profile_lookup_auth_failed user_id=%s status=%s body=%s",
                    user_id,
                    response.status_code,
                    response.text,
                )
            else:
                logger.debug(
                    "meta_profile_lookup_failed user_id=%s status=%s body=%s",
                    user_id,
                    response.status_code,
                    response.text,
                )
            return None
        data = response.json()
        return data.get("username") or data.get("name")
    except Exception as exc:
        logger.warning("meta_profile_lookup_exception user_id=%s error=%s", user_id, exc)
        return None


def _normalize_meta_message_attachments(raw_attachments: object) -> list[dict]:
    """Normalize Meta attachment payloads to a list format used by inbox metadata."""
    if isinstance(raw_attachments, list):
        return [item for item in raw_attachments if isinstance(item, dict)]
    if not isinstance(raw_attachments, dict):
        return []
    data = raw_attachments.get("data")
    if not isinstance(data, list):
        return []

    normalized: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        payload: dict = {}
        image_raw = item.get("image_data")
        video_raw = item.get("video_data")
        image_data = image_raw if isinstance(image_raw, dict) else {}
        video_data = video_raw if isinstance(video_raw, dict) else {}
        if image_data.get("url"):
            payload["url"] = image_data.get("url")
        elif video_data.get("url"):
            payload["url"] = video_data.get("url")
        elif item.get("file_url"):
            payload["url"] = item.get("file_url")
        elif item.get("url"):
            payload["url"] = item.get("url")

        attachment_type = item.get("type")
        if not attachment_type:
            if image_data:
                attachment_type = "image"
            elif video_data:
                attachment_type = "video"
            elif payload.get("url"):
                attachment_type = "file"

        normalized_item: dict = {}
        if attachment_type:
            normalized_item["type"] = attachment_type
        if payload:
            normalized_item["payload"] = payload
            if payload.get("url"):
                normalized_item["url"] = payload.get("url")
        if item.get("id"):
            normalized_item["id"] = item.get("id")
        if item.get("title"):
            normalized_item["title"] = item.get("title")
        if normalized_item:
            normalized.append(normalized_item)
    return normalized


def _fetch_instagram_message_attachments(
    access_token: str | None,
    message_id: str | None,
    base_url: str,
) -> list[dict]:
    """Fetch Instagram message details and extract attachment URLs."""
    if not access_token or not message_id:
        return []
    try:
        with httpx.Client(timeout=8) as client:
            response = client.get(
                f"{base_url.rstrip('/')}/{message_id}",
                params={
                    "fields": "id,attachments{type,payload,url,title,image_data,video_data,file_url}",
                    "access_token": access_token,
                },
            )
        if response.status_code >= 400:
            logger.debug(
                "instagram_message_lookup_failed message_id=%s status=%s body=%s",
                message_id,
                response.status_code,
                response.text,
            )
            return []
        data = response.json()
        return _normalize_meta_message_attachments(data.get("attachments"))
    except Exception as exc:
        logger.debug("instagram_message_lookup_exception message_id=%s error=%s", message_id, exc)
        return []


def _coerce_identity_dict(value: object) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _extract_identity_metadata(*values: object) -> dict:
    identity: dict = {}
    for value in values:
        data = _coerce_identity_dict(value)
        if not data:
            continue
        for key in _META_IDENTITY_KEYS:
            candidate = data.get(key)
            if candidate is None:
                continue
            if isinstance(candidate, str):
                candidate = candidate.strip()
            if candidate:
                identity[key] = candidate
    return identity


def _collect_meta_attribution(data: dict, attribution: dict) -> None:
    for key in _META_ATTRIBUTION_KEYS:
        candidate = data.get(key)
        if candidate is None:
            continue
        if isinstance(candidate, str):
            candidate = candidate.strip()
        if candidate not in (None, ""):
            attribution[key] = candidate
    for key in _META_ATTRIBUTION_NESTED_KEYS:
        nested = _coerce_identity_dict(data.get(key))
        if nested:
            _collect_meta_attribution(nested, attribution)


def _extract_meta_attribution(*values: object) -> dict:
    attribution: dict = {}
    for value in values:
        data = _coerce_identity_dict(value)
        if not data:
            continue
        _collect_meta_attribution(data, attribution)
    return attribution


def _build_instagram_sender_identity_metadata(
    *,
    sender_id: str,
    sender_username: str | None,
    sender_name: str | None,
) -> dict[str, str]:
    identity: dict[str, str] = {"sender_id": sender_id}
    if isinstance(sender_username, str) and sender_username.strip():
        identity["sender_username"] = sender_username.strip()
    if isinstance(sender_name, str) and sender_name.strip():
        identity["sender_name"] = sender_name.strip()
    return identity


def _persist_instagram_sender_identity(
    *,
    person: Person,
    channel: PersonChannel,
    metadata: dict | None,
) -> None:
    if channel.channel_type != PersonChannelType.instagram_dm or not isinstance(metadata, dict):
        return
    sender_id = metadata.get("sender_id")
    if not isinstance(sender_id, str) or not sender_id.strip():
        return
    sender_username = metadata.get("sender_username")
    sender_name = metadata.get("sender_name")
    identity = _build_instagram_sender_identity_metadata(
        sender_id=sender_id.strip(),
        sender_username=sender_username if isinstance(sender_username, str) else None,
        sender_name=sender_name if isinstance(sender_name, str) else None,
    )
    if not identity:
        return
    channel_meta = dict(channel.metadata_) if isinstance(channel.metadata_, dict) else {}
    existing_channel_identity_raw = channel_meta.get("instagram_profile")
    existing_channel_identity = (
        dict(existing_channel_identity_raw) if isinstance(existing_channel_identity_raw, dict) else {}
    )
    existing_channel_identity.update(identity)
    channel_meta["instagram_profile"] = existing_channel_identity
    channel.metadata_ = channel_meta

    person_meta = dict(person.metadata_) if isinstance(person.metadata_, dict) else {}
    existing_person_identity_raw = person_meta.get("instagram_profile")
    existing_person_identity = (
        dict(existing_person_identity_raw) if isinstance(existing_person_identity_raw, dict) else {}
    )
    existing_person_identity.update(identity)
    person_meta["instagram_profile"] = existing_person_identity
    person.metadata_ = person_meta

    preferred_name = identity.get("sender_username") or identity.get("sender_name")
    if preferred_name and (not person.display_name or _is_meta_placeholder_name(person.display_name)):
        person.display_name = preferred_name


def _is_meta_ad_attribution_capture_enabled(db: Session) -> bool:
    enabled = resolve_value(db, SettingDomain.comms, "meta_capture_ad_attribution")
    if isinstance(enabled, bool):
        return enabled
    if isinstance(enabled, str):
        return enabled.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _upsert_entity_attribution_metadata(entity, *, attribution: dict, channel: ChannelType) -> None:
    if not attribution:
        return
    existing_meta = dict(entity.metadata_) if isinstance(entity.metadata_, dict) else {}
    existing_attr = existing_meta.get("attribution")
    merged_attr = dict(existing_attr) if isinstance(existing_attr, dict) else {}
    merged_attr.update(attribution)
    merged_attr["last_channel"] = channel.value
    merged_attr["last_seen_at"] = datetime.now(UTC).isoformat()
    existing_meta["attribution"] = merged_attr
    entity.metadata_ = existing_meta


def _persist_meta_attribution_to_person_and_lead(
    db: Session,
    *,
    person: Person,
    channel: ChannelType,
    attribution: dict | None,
) -> None:
    if not attribution:
        return
    if not _is_meta_ad_attribution_capture_enabled(db):
        return
    _upsert_entity_attribution_metadata(person, attribution=attribution, channel=channel)
    lead = (
        db.query(Lead)
        .filter(Lead.person_id == person.id)
        .filter(Lead.is_active.is_(True))
        .order_by(Lead.created_at.desc())
        .first()
    )
    if lead:
        _upsert_entity_attribution_metadata(lead, attribution=attribution, channel=channel)


def _is_meta_ad_attribution(attribution: dict | None) -> bool:
    if not isinstance(attribution, dict) or not attribution:
        return False
    source = str(attribution.get("source") or "").strip().upper()
    if source == "ADS":
        return True
    return any(attribution.get(key) for key in ("ad_id", "adset_id", "adgroup_id", "campaign_id", "ctwa_clid"))


def _ensure_conversation_tag(db: Session, *, conversation_id, tag: str) -> None:
    clean_tag = str(tag or "").strip()
    if not clean_tag:
        return
    existing = (
        db.query(ConversationTag)
        .filter(
            ConversationTag.conversation_id == conversation_id,
            ConversationTag.tag == clean_tag,
        )
        .first()
    )
    if not existing:
        db.add(ConversationTag(conversation_id=conversation_id, tag=clean_tag))


def _persist_meta_attribution_to_conversation(
    db: Session,
    *,
    conversation: Conversation,
    channel: ChannelType,
    attribution: dict | None,
) -> None:
    if not _is_meta_ad_attribution(attribution):
        return
    clean_attribution = attribution if isinstance(attribution, dict) else None
    if not clean_attribution:
        return
    _upsert_entity_attribution_metadata(conversation, attribution=clean_attribution, channel=channel)
    _ensure_conversation_tag(db, conversation_id=conversation.id, tag="Meta Ad")
    if channel == ChannelType.instagram_dm:
        _ensure_conversation_tag(db, conversation_id=conversation.id, tag="Instagram Ad")
    elif channel == ChannelType.facebook_messenger:
        _ensure_conversation_tag(db, conversation_id=conversation.id, tag="Facebook Ad")


def _capture_pending_messenger_attribution(
    db: Session,
    *,
    page_id: str,
    sender_id: str,
    contact_name: str | None,
    attribution: dict | None,
) -> bool:
    if not _is_meta_ad_attribution(attribution):
        return False
    person, channel = _resolve_meta_person_and_channel(
        db,
        ChannelType.facebook_messenger,
        sender_id,
        contact_name,
        None,
    )
    channel_meta = dict(channel.metadata_ or {}) if isinstance(channel.metadata_, dict) else {}
    channel_meta["pending_meta_attribution"] = {
        "page_id": page_id,
        "attribution": attribution,
        "captured_at": datetime.now(UTC).isoformat(),
    }
    channel.metadata_ = channel_meta
    if contact_name and (not person.display_name or person.display_name.startswith("Facebook User")):
        person.display_name = contact_name
    db.commit()
    return True


def _consume_pending_messenger_attribution(
    db: Session,
    *,
    channel: PersonChannel,
    page_id: str,
) -> dict | None:
    channel_meta = dict(channel.metadata_ or {}) if isinstance(channel.metadata_, dict) else {}
    pending = channel_meta.get("pending_meta_attribution")
    if not isinstance(pending, dict):
        return None
    pending_page_id = str(pending.get("page_id") or "").strip()
    if pending_page_id and pending_page_id != str(page_id).strip():
        return None
    captured_at_raw = pending.get("captured_at")
    if isinstance(captured_at_raw, str) and captured_at_raw.strip():
        try:
            captured_at = datetime.fromisoformat(captured_at_raw.replace("Z", "+00:00"))
        except ValueError:
            captured_at = None
        if captured_at and captured_at.tzinfo is not None and captured_at < datetime.now(UTC) - timedelta(days=3):
            channel_meta.pop("pending_meta_attribution", None)
            channel.metadata_ = channel_meta or None
            db.commit()
            return None
    attribution = pending.get("attribution")
    channel_meta.pop("pending_meta_attribution", None)
    channel.metadata_ = channel_meta or None
    db.commit()
    return attribution if isinstance(attribution, dict) else None


def _get_meta_api_timeout_seconds(db: Session) -> int:
    value = resolve_value(db, SettingDomain.comms, "meta_api_timeout_seconds")
    if isinstance(value, int):
        return max(1, value)
    if isinstance(value, str) and value.isdigit():
        return max(1, int(value))
    return 30


def _normalize_meta_lead_field_name(name: object) -> str:
    if not isinstance(name, str):
        return ""
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_meta_lead_fields(field_data: object) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    if not isinstance(field_data, list):
        return normalized
    for item in field_data:
        if not isinstance(item, dict):
            continue
        key = _normalize_meta_lead_field_name(item.get("name"))
        if not key:
            continue
        raw_values = item.get("values")
        values: list[str] = []
        if isinstance(raw_values, list):
            for raw in raw_values:
                if raw is None:
                    continue
                candidate = raw.strip() if isinstance(raw, str) else str(raw).strip()
                if candidate:
                    values.append(candidate)
        elif raw_values is not None:
            candidate = raw_values.strip() if isinstance(raw_values, str) else str(raw_values).strip()
            if candidate:
                values.append(candidate)
        if values:
            normalized[key] = values
    return normalized


def _first_meta_lead_field_value(fields: dict[str, list[str]], *keys: str) -> str | None:
    for key in keys:
        values = fields.get(_normalize_meta_lead_field_name(key))
        if values:
            return values[0]
    return None


def _split_display_name(display_name: str | None) -> tuple[str, str, str | None]:
    if not display_name:
        return "Unknown", "Unknown", None
    cleaned = display_name.strip()
    if not cleaned:
        return "Unknown", "Unknown", None
    parts = cleaned.split()
    first_name = parts[0][:80] if parts else "Unknown"
    last_name = " ".join(parts[1:])[:80] if len(parts) > 1 else "Unknown"
    return first_name or "Unknown", last_name or "Unknown", cleaned[:120]


def _build_meta_lead_identity(detail: dict, fallback_name: str | None = None) -> dict[str, str | None]:
    fields = _normalize_meta_lead_fields(detail.get("field_data"))
    email = _first_meta_lead_field_value(fields, "email", "email_address")
    phone = _normalize_phone_address(_first_meta_lead_field_value(fields, "phone_number", "phone", "mobile_phone"))
    first_name = _first_meta_lead_field_value(fields, "first_name", "first")
    last_name = _first_meta_lead_field_value(fields, "last_name", "last")
    full_name = _first_meta_lead_field_value(fields, "full_name", "full name", "name") or fallback_name
    if not full_name and (first_name or last_name):
        full_name = " ".join(part for part in [first_name, last_name] if part).strip()
    city = _first_meta_lead_field_value(fields, "city")
    region = _first_meta_lead_field_value(fields, "state", "province", "region")
    address_parts = [
        _first_meta_lead_field_value(fields, "street_address", "address", "full_address"),
        city,
        region,
        _first_meta_lead_field_value(fields, "postal_code", "zip_code", "zip"),
        _first_meta_lead_field_value(fields, "country"),
    ]
    address = ", ".join(part for part in address_parts if part)
    return {
        "email": email.strip().lower() if isinstance(email, str) and email.strip() else None,
        "phone": phone,
        "first_name": first_name,
        "last_name": last_name,
        "full_name": full_name,
        "city": city,
        "region": region,
        "address": address or None,
    }


def _lead_source_from_meta_lead_detail(detail: dict) -> str:
    platform = detail.get("platform")
    if isinstance(platform, str) and "instagram" in platform.strip().lower():
        return "Instagram Ads"
    return "Facebook Ads"


def _build_meta_lead_metadata(
    *,
    leadgen_id: str,
    page_id: str,
    detail: dict,
    field_answers: dict[str, list[str]],
    change_value: dict,
) -> dict:
    attribution: dict[str, object] = {
        "source": "meta_lead_form",
        "platform": detail.get("platform") or "facebook",
        "page_id": page_id,
    }
    for source in (detail, change_value):
        for key in ("ad_id", "adset_id", "adgroup_id", "campaign_id", "form_id"):
            value = source.get(key) if isinstance(source, dict) else None
            if value:
                attribution[key] = value
    metadata: dict[str, object] = {
        "meta_leadgen_id": leadgen_id,
        "meta_page_id": page_id,
        "meta_form_id": detail.get("form_id") or change_value.get("form_id"),
        "meta_platform": detail.get("platform"),
        "meta_created_time": detail.get("created_time") or change_value.get("created_time"),
        "meta_ad_id": detail.get("ad_id") or change_value.get("ad_id"),
        "meta_adset_id": detail.get("adset_id") or change_value.get("adset_id"),
        "meta_adgroup_id": detail.get("adgroup_id") or change_value.get("adgroup_id"),
        "meta_campaign_id": detail.get("campaign_id") or change_value.get("campaign_id"),
        "meta_is_organic": detail.get("is_organic"),
        "meta_field_data": detail.get("field_data"),
        "meta_field_answers": field_answers,
        "attribution": attribution,
    }
    return {key: value for key, value in metadata.items() if value is not None}


def _find_existing_meta_lead_by_leadgen_id(
    db: Session,
    *,
    leadgen_id: str,
    person_id=None,
) -> Lead | None:
    if not leadgen_id:
        return None

    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        query = db.query(Lead).filter(Lead.is_active.is_(True))
        if person_id is not None:
            query = query.filter(Lead.person_id == person_id)
        lead = (
            query.filter(cast(Lead.metadata_, JSONB).contains({"meta_leadgen_id": leadgen_id}))
            .order_by(Lead.created_at.desc())
            .first()
        )
        if lead:
            return lead

    query = db.query(Lead).filter(Lead.is_active.is_(True))
    if person_id is not None:
        query = query.filter(Lead.person_id == person_id)
    else:
        query = query.filter(
            or_(
                Lead.lead_source == "Facebook Ads",
                Lead.lead_source == "Instagram Ads",
                cast(Lead.metadata_, String).contains("meta_leadgen_id"),
            )
        )
    for lead in query.order_by(Lead.created_at.desc()).limit(500).all():
        metadata = lead.metadata_ if isinstance(lead.metadata_, dict) else {}
        if metadata.get("meta_leadgen_id") == leadgen_id:
            return lead
    return None


def _ensure_meta_lead_person(
    db: Session,
    *,
    identity: dict[str, str | None],
    leadgen_id: str,
) -> Person:
    from app.services.person_identity import _find_by_person_email, _find_by_person_phone

    person = None
    if identity.get("email"):
        person = _find_by_person_email(db, identity["email"])
    if not person and identity.get("phone"):
        person = _find_by_person_phone(db, identity["phone"])
    first_name, last_name, display_name = _split_display_name(identity.get("full_name"))
    if person:
        if display_name and not person.display_name:
            person.display_name = display_name
        first_name_val = identity.get("first_name")
        if first_name_val and person.first_name == "Unknown":
            person.first_name = first_name_val[:80]
        last_name_val = identity.get("last_name")
        if last_name_val and person.last_name == "Unknown":
            person.last_name = last_name_val[:80]
        email_val = identity.get("email")
        if email_val and not person.email.endswith("@local.invalid"):
            person.email = person.email or email_val
        phone_val = identity.get("phone")
        if phone_val and not person.phone:
            person.phone = phone_val
        city_val = identity.get("city")
        if city_val and not person.city:
            person.city = city_val[:80]
        region_val = identity.get("region")
        if region_val and not person.region:
            person.region = region_val[:80]
        address_val = identity.get("address")
        if address_val and not person.address_line1:
            person.address_line1 = address_val[:120]
    else:
        email = identity.get("email") or f"meta-lead-{leadgen_id}@local.invalid"
        person = Person(
            first_name=(identity.get("first_name") or first_name)[:80],
            last_name=(identity.get("last_name") or last_name)[:80],
            display_name=display_name,
            email=email[:255],
            phone=identity.get("phone"),
            city=(identity.get("city") or None),
            region=(identity.get("region") or None),
            address_line1=(identity.get("address") or None),
            party_status=PartyStatus.lead,
        )
        db.add(person)
        db.flush()

    email_val = identity.get("email")
    phone_val = identity.get("phone")
    if email_val and person.email != email_val and person.email.endswith("@local.invalid"):
        person.email = email_val[:255]
    if email_val:
        _ensure_person_channel_unified(db, person, PersonChannelType.email, email_val)
    if phone_val:
        _ensure_person_channel_unified(db, person, PersonChannelType.whatsapp, phone_val)

    db.commit()
    db.refresh(person)
    return person


def _fetch_meta_lead_details(
    db: Session,
    *,
    access_token: str | None,
    leadgen_id: str,
    base_url: str,
) -> dict:
    if not access_token:
        raise RuntimeError("No active Meta page token available for lead retrieval")
    fields = ",".join(
        [
            "id",
            "created_time",
            "field_data",
            "ad_id",
            "adset_id",
            "campaign_id",
            "form_id",
            "is_organic",
            "platform",
        ]
    )
    timeout = _get_meta_api_timeout_seconds(db)
    with httpx.Client(timeout=timeout) as client:
        response = client.get(
            f"{base_url.rstrip('/')}/{leadgen_id}",
            params={"fields": fields, "access_token": access_token},
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Meta lead retrieval failed status={response.status_code} body={response.text[:300]}")
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Meta lead retrieval returned an unexpected payload")
    return data


def _token_has_scope(token: OAuthToken | None, scope: str) -> bool:
    if not token or not isinstance(token.scopes, list):
        return False
    normalized = {str(item).strip().lower() for item in token.scopes if item is not None}
    return scope.strip().lower() in normalized


def _store_meta_lead_submission(
    db: Session,
    *,
    page_id: str,
    leadgen_id: str,
    detail: dict,
    change_value: dict,
) -> Lead:
    field_answers = _normalize_meta_lead_fields(detail.get("field_data"))
    identity = _build_meta_lead_identity(detail)
    person = _ensure_meta_lead_person(db, identity=identity, leadgen_id=leadgen_id)

    existing = _find_existing_meta_lead_by_leadgen_id(db, leadgen_id=leadgen_id, person_id=person.id)
    metadata = _build_meta_lead_metadata(
        leadgen_id=leadgen_id,
        page_id=page_id,
        detail=detail,
        field_answers=field_answers,
        change_value=change_value,
    )

    if existing:
        existing_meta = dict(existing.metadata_ or {}) if isinstance(existing.metadata_, dict) else {}
        existing_meta.update(metadata)
        existing.metadata_ = existing_meta
        if not existing.lead_source:
            existing.lead_source = _lead_source_from_meta_lead_detail(detail)
        if not existing.region and identity.get("region"):
            existing.region = identity["region"]
        if not existing.address and identity.get("address"):
            existing.address = identity["address"]
        db.commit()
        db.refresh(existing)
        return existing

    lead = leads_service.create(
        db,
        LeadCreate(
            person_id=person.id,
            title=identity.get("full_name"),
            lead_source=_lead_source_from_meta_lead_detail(detail),
            region=identity.get("region"),
            address=identity.get("address"),
            metadata_=metadata,
        ),
    )
    return lead


def _extract_location_from_attachments(attachments: object) -> dict | None:
    if not isinstance(attachments, list):
        return None
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        if attachment.get("type") != "location":
            continue
        payload = attachment.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        coords = payload.get("coordinates") or {}
        if not isinstance(coords, dict):
            coords = {}
        latitude = coords.get("lat") or coords.get("latitude")
        longitude = coords.get("long") or coords.get("lng") or coords.get("longitude")
        name = payload.get("title") or payload.get("name") or attachment.get("title")
        address = payload.get("address") or payload.get("label")
        label = name or address
        return {
            "type": "location",
            "latitude": latitude,
            "longitude": longitude,
            "address": address,
            "name": name,
            "label": label,
            "location": payload,
        }
    return None


def _resolve_meta_person_and_channel(
    db: Session,
    channel_type: ChannelType,
    sender_id: str,
    contact_name: str | None,
    metadata: dict | None,
):
    """Resolve person for Meta webhook using unified identity resolution."""
    from app.services.person_identity import resolve_person

    email_hint = None
    phone_hint = None
    if metadata and isinstance(metadata, dict):
        raw_email = metadata.get("email")
        if isinstance(raw_email, str) and raw_email.strip():
            email_hint = raw_email.strip().lower()
        raw_phone = metadata.get("phone")
        if isinstance(raw_phone, str) and raw_phone.strip():
            phone_hint = raw_phone.strip()

    result = resolve_person(
        db,
        channel_type=channel_type,
        address=sender_id,
        display_name=contact_name,
        email=email_hint,
        phone=phone_hint,
    )
    db.commit()
    db.refresh(result.person)
    db.refresh(result.channel)
    return result.person, result.channel


def _apply_meta_read_receipt(
    db: Session,
    channel_type: ChannelType,
    contact_id: str | None,
    watermark: int | float | None,
) -> None:
    if not contact_id or watermark is None:
        return
    timestamp = float(watermark)
    if timestamp > 1_000_000_000_000:
        timestamp = timestamp / 1000
    read_at = datetime.fromtimestamp(timestamp, tz=UTC)
    person_channel_type = PersonChannelType(channel_type.value)
    channel = (
        db.query(PersonChannel)
        .filter(PersonChannel.channel_type == person_channel_type)
        .filter(PersonChannel.address == contact_id)
        .first()
    )
    if not channel:
        return
    db.query(Message).filter(
        Message.person_channel_id == channel.id,
        Message.channel_type == channel_type,
        Message.direction == MessageDirection.inbound,
        Message.status == MessageStatus.received,
        Message.read_at.is_(None),
        func.coalesce(Message.received_at, Message.created_at) <= read_at,
    ).update({"read_at": read_at})
    db.commit()


def verify_webhook_signature(
    payload_body: bytes,
    signature_header: str | None,
    app_secret: str,
    *,
    suppress_mismatch_log: bool = False,
) -> bool:
    """Verify Meta webhook signature (X-Hub-Signature-256).

    Meta signs all webhook payloads with the app secret. This function
    verifies the signature to ensure the webhook is authentic.

    Args:
        payload_body: Raw request body bytes
        signature_header: Value of X-Hub-Signature-256 header
        app_secret: Facebook App Secret

    Returns:
        True if signature is valid, False otherwise
    """
    if not signature_header or not signature_header.startswith("sha256="):
        logger.info("webhook_signature_missing_or_invalid")
        return False

    expected_signature = signature_header[7:]  # Remove "sha256=" prefix
    computed_signature = hmac.new(
        app_secret.encode(),
        payload_body,
        hashlib.sha256,
    ).hexdigest()

    is_valid = hmac.compare_digest(expected_signature, computed_signature)
    if not is_valid and not suppress_mismatch_log:
        logger.info("webhook_signature_mismatch")
    return is_valid


def _resolve_meta_connector(
    db: Session,
    connector_type: ConnectorType,
) -> tuple[IntegrationTarget | None, ConnectorConfig | None]:
    """Find active Meta connector and integration target.

    Args:
        db: Database session
        connector_type: ConnectorType.facebook or ConnectorType.instagram

    Returns:
        Tuple of (IntegrationTarget, ConnectorConfig) or (None, None)
    """
    target = (
        db.query(IntegrationTarget)
        .join(ConnectorConfig, ConnectorConfig.id == IntegrationTarget.connector_config_id)
        .filter(IntegrationTarget.target_type == IntegrationTargetType.crm)
        .filter(IntegrationTarget.is_active.is_(True))
        .filter(ConnectorConfig.connector_type == connector_type)
        .filter(ConnectorConfig.is_active.is_(True))
        .order_by(IntegrationTarget.created_at.desc())
        .first()
    )
    if not target:
        return None, None
    return target, target.connector_config


def _find_token_for_account(
    db: Session,
    connector_config_id,
    account_type: str,
    account_id: str,
) -> OAuthToken | None:
    """Find OAuth token for a specific account.

    Args:
        db: Database session
        connector_config_id: UUID of ConnectorConfig
        account_type: "page" or "instagram_business"
        account_id: External account ID

    Returns:
        OAuthToken or None if not found
    """
    return (
        db.query(OAuthToken)
        .filter(OAuthToken.connector_config_id == connector_config_id)
        .filter(OAuthToken.provider == "meta")
        .filter(OAuthToken.account_type == account_type)
        .filter(OAuthToken.external_account_id == account_id)
        .filter(OAuthToken.is_active.is_(True))
        .first()
    )


def _resolve_whatsapp_target_by_phone_number_id(
    db: Session,
    phone_number_id: str | None,
) -> IntegrationTarget | None:
    if not phone_number_id:
        return None

    targets = (
        db.query(IntegrationTarget)
        .join(ConnectorConfig, ConnectorConfig.id == IntegrationTarget.connector_config_id)
        .filter(IntegrationTarget.target_type == IntegrationTargetType.crm)
        .filter(IntegrationTarget.is_active.is_(True))
        .filter(ConnectorConfig.connector_type == ConnectorType.whatsapp)
        .filter(ConnectorConfig.is_active.is_(True))
        .order_by(IntegrationTarget.created_at.desc())
        .all()
    )
    for target in targets:
        config = target.connector_config
        if not config:
            continue
        metadata = config.metadata_ if isinstance(config.metadata_, dict) else {}
        auth_config = config.auth_config if isinstance(config.auth_config, dict) else {}
        candidate = metadata.get("phone_number_id") or auth_config.get("phone_number_id")
        if candidate is not None and str(candidate) == str(phone_number_id):
            return target
    return None


def process_whatsapp_webhook(
    db: Session,
    payload: MetaWebhookPayload,
) -> list[dict]:
    """Process WhatsApp Business API webhook payload.

    Handles delivery status updates (sent, delivered, read, failed) for
    outbound messages. Updates the corresponding message record in the DB
    and broadcasts the status change via WebSocket.

    Args:
        db: Database session
        payload: Validated MetaWebhookPayload with object=whatsapp_business_account

    Returns:
        List of result dicts with message_id and status
    """
    results = []

    # WhatsApp status precedence: sent < delivered < read (never go backwards)
    _STATUS_RANK = {"sent": 1, "delivered": 2, "read": 3, "failed": 0}

    for entry in payload.entry:
        for change in entry.changes or []:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue

            value_data = change.get("value", {})
            value_metadata = value_data.get("metadata") if isinstance(value_data, dict) else None
            phone_number_id = value_metadata.get("phone_number_id") if isinstance(value_metadata, dict) else None
            target = _resolve_whatsapp_target_by_phone_number_id(db, phone_number_id)
            try:
                value = WhatsAppStatusValue(**value_data)
            except Exception:
                logger.warning(
                    "whatsapp_webhook_value_parse_failed entry_id=%s",
                    entry.id,
                )
                continue

            for status_update in value.statuses or []:
                wa_message_id = status_update.id
                new_status_str = status_update.status
                if phone_number_id and not target:
                    logger.warning(
                        "whatsapp_status_target_not_found phone_number_id=%s wamid=%s",
                        phone_number_id,
                        wa_message_id,
                    )
                    results.append({"wamid": wa_message_id, "status": "skipped"})
                    continue

                message_query = (
                    db.query(Message)
                    .filter(Message.external_id == wa_message_id)
                    .filter(Message.channel_type == ChannelType.whatsapp)
                    .filter(Message.direction == MessageDirection.outbound)
                )
                if target is not None:
                    message_query = message_query.filter(Message.channel_target_id == target.id)
                message = message_query.first() if target is not None else None
                if target is None:
                    matches = message_query.limit(2).all()
                    if len(matches) > 1:
                        target_ids = [str(m.channel_target_id) for m in matches]
                        logger.warning(
                            "whatsapp_status_ambiguous_message wamid=%s status=%s "
                            "phone_number_id=%s match_target_ids=%s",
                            wa_message_id,
                            new_status_str,
                            phone_number_id,
                            target_ids,
                        )
                        results.append({"wamid": wa_message_id, "status": "skipped"})
                        continue
                    message = matches[0] if matches else None
                if not message:
                    logger.debug(
                        "whatsapp_status_message_not_found wamid=%s status=%s",
                        wa_message_id,
                        new_status_str,
                    )
                    results.append({"wamid": wa_message_id, "status": "skipped"})
                    continue

                # Don't regress status (e.g. don't go from delivered back to sent)
                current_rank = _STATUS_RANK.get(message.status.value, -1)
                new_rank = _STATUS_RANK.get(new_status_str, -1)

                if new_rank <= current_rank and new_status_str != "failed":
                    results.append({"wamid": wa_message_id, "status": "no_change"})
                    continue

                # Map to MessageStatus enum
                try:
                    new_status = MessageStatus(new_status_str)
                except ValueError:
                    logger.warning(
                        "whatsapp_status_unknown status=%s wamid=%s",
                        new_status_str,
                        wa_message_id,
                    )
                    results.append({"wamid": wa_message_id, "status": "unknown"})
                    continue

                message.status = new_status

                # Set read_at timestamp for read receipts
                if new_status_str == "read":
                    try:
                        message.read_at = datetime.fromtimestamp(int(status_update.timestamp), tz=UTC)
                    except (ValueError, OSError):
                        message.read_at = datetime.now(UTC)

                # Store error details for failed messages
                if new_status_str == "failed" and status_update.errors:
                    meta = message.metadata_ if isinstance(message.metadata_, dict) else {}
                    meta["whatsapp_errors"] = status_update.errors
                    message.metadata_ = meta
                    _mark_whatsapp_channel_invalid_from_status(message.person_channel, status_update.errors)

                db.commit()
                try:
                    from app.services.crm.campaigns import reconcile_outreach_message_status

                    reconcile_outreach_message_status(db, message_id=str(message.id))
                except Exception:
                    logger.debug("whatsapp_status_outreach_reconcile_failed message_id=%s", message.id, exc_info=True)

                # Broadcast status change to UI via WebSocket
                try:
                    from app.websocket.broadcaster import broadcast_message_status

                    broadcast_message_status(
                        str(message.id),
                        str(message.conversation_id),
                        message.status.value,
                    )
                except Exception:
                    logger.debug("whatsapp_status_broadcast_failed message_id=%s", message.id, exc_info=True)

                logger.info(
                    "whatsapp_status_updated wamid=%s message_id=%s status=%s",
                    wa_message_id,
                    message.id,
                    new_status_str,
                )
                results.append({"wamid": wa_message_id, "status": "stored"})

    return results


def process_messenger_webhook(
    db: Session,
    payload: MetaWebhookPayload,
) -> list[dict]:
    """Process Facebook Messenger webhook payload.

    Args:
        db: Database session
        payload: Validated MetaWebhookPayload

    Returns:
        List of result dicts with message_id and status
    """
    results = []

    _target, config = _resolve_meta_connector(db, ConnectorType.facebook)
    base_url = _get_meta_graph_base_url(db)
    facebook_override_token = _get_facebook_access_token_override(db)

    for entry in payload.entry:
        page_id = entry.id
        page_token = None
        page_oauth_token = None
        if config:
            page_oauth_token = _find_token_for_account(db, config.id, "page", page_id)
            page_token = page_oauth_token.access_token if page_oauth_token else None
        leadgen_token = facebook_override_token or page_token

        if entry.changes:
            if (
                any(change.get("field") == "leadgen" for change in entry.changes or [])
                and not facebook_override_token
                and not _token_has_scope(
                    page_oauth_token,
                    "leads_retrieval",
                )
            ):
                logger.warning(
                    "facebook_leadgen_scope_missing page_id=%s connector_id=%s",
                    page_id,
                    config.id if config else None,
                )
            results.extend(
                _process_facebook_leadgen_changes(
                    db,
                    entry=entry,
                    page_token=leadgen_token,
                    base_url=base_url,
                )
            )
            results.extend(_process_facebook_comment_changes(db, entry))

        if not entry.messaging:
            continue

        for messaging_event in entry.messaging:
            sender = messaging_event.sender or {}
            sender_id = sender.get("id")
            sender_id_str = str(sender_id).strip() if isinstance(sender_id, str) and sender_id.strip() else None
            contact_name = (
                sender.get("name")
                or (_fetch_profile_name(page_token, sender_id_str, "name", base_url) if sender_id_str else None)
                or (f"Facebook User {sender_id_str}" if sender_id_str else None)
            )
            event_attribution = _extract_meta_attribution(
                messaging_event.referral,
                messaging_event.postback,
                messaging_event.postback.get("payload") if messaging_event.postback else None,
            )
            if messaging_event.postback and not messaging_event.message:
                if sender_id_str and _capture_pending_messenger_attribution(
                    db,
                    page_id=page_id,
                    sender_id=sender_id_str,
                    contact_name=contact_name,
                    attribution=event_attribution,
                ):
                    logger.info(
                        "messenger_webhook_postback_attribution_captured page_id=%s sender_id=%s",
                        page_id,
                        sender_id_str,
                    )
                else:
                    logger.info(
                        "messenger_webhook_postback_ignored page_id=%s sender_id=%s",
                        page_id,
                        sender_id_str,
                    )
                continue
            if messaging_event.referral and not messaging_event.message:
                if sender_id_str and _capture_pending_messenger_attribution(
                    db,
                    page_id=page_id,
                    sender_id=sender_id_str,
                    contact_name=contact_name,
                    attribution=event_attribution,
                ):
                    logger.info(
                        "messenger_webhook_referral_attribution_captured page_id=%s sender_id=%s",
                        page_id,
                        sender_id_str,
                    )
                continue
            if messaging_event.delivery and not messaging_event.message:
                logger.info(
                    "messenger_webhook_delivery_ignored page_id=%s sender_id=%s",
                    page_id,
                    sender_id_str,
                )
                continue
            if messaging_event.read and not messaging_event.message:
                recipient_id = (messaging_event.recipient or {}).get("id")
                contact_id = sender_id_str
                if sender_id_str == page_id:
                    contact_id = recipient_id
                _apply_meta_read_receipt(
                    db,
                    ChannelType.facebook_messenger,
                    contact_id,
                    (messaging_event.read or {}).get("watermark"),
                )
                continue
            # Skip non-message events (typing indicators, etc.)
            if not messaging_event.message:
                continue

            message = messaging_event.message

            # Skip echo messages (messages sent by the page)
            if message.get("is_echo"):
                continue

            if not sender_id_str:
                logger.warning("messenger_webhook_missing_sender page_id=%s", page_id)
                continue
            if sender_id_str == page_id:
                logger.info(
                    "messenger_webhook_skip_self page_id=%s sender_id=%s",
                    page_id,
                    sender_id_str,
                )
                continue

            attachments = message.get("attachments", [])
            if attachments:
                logger.info(
                    "instagram_webhook_attachments message_id=%s attachments=%s",
                    message.get("mid"),
                    attachments,
                )
            # Get message text
            text = message.get("text")
            if not text:
                location_metadata = _extract_location_from_attachments(attachments)
                if location_metadata:
                    loc_label = (
                        location_metadata.get("label")
                        or location_metadata.get("name")
                        or location_metadata.get("address")
                    )
                    lat = location_metadata.get("latitude")
                    lng = location_metadata.get("longitude")
                    if loc_label:
                        text = f"📍 {loc_label}"
                    elif lat is not None and lng is not None:
                        text = f"📍 https://maps.google.com/?q={lat},{lng}"
                    else:
                        text = "📍 Location shared"
                elif attachments:
                    text = "(attachment)"
                else:
                    continue

            # Parse timestamp
            received_at = None
            if messaging_event.timestamp:
                received_at = datetime.fromtimestamp(
                    messaging_event.timestamp / 1000,
                    tz=UTC,
                )

            external_id, external_ref = _normalize_external_id(message.get("mid"))
            metadata = {
                "attachments": message.get("attachments"),
                "reply_to": message.get("reply_to"),
            }
            location_metadata = _extract_location_from_attachments(attachments)
            if location_metadata:
                metadata.update(location_metadata)
            identity_metadata = _extract_identity_metadata(
                messaging_event.referral,
                message.get("metadata"),
                message.get("referral"),
                (message.get("referral") or {}).get("ref") if isinstance(message.get("referral"), dict) else None,
                messaging_event.postback.get("payload") if messaging_event.postback else None,
            )
            if identity_metadata:
                metadata.update(identity_metadata)
            attribution_metadata = _extract_meta_attribution(
                messaging_event.referral,
                message.get("referral"),
                message.get("metadata"),
                messaging_event.postback,
                messaging_event.postback.get("payload") if messaging_event.postback else None,
            )
            if not attribution_metadata:
                pending_attribution = None
                _, channel = _resolve_meta_person_and_channel(
                    db,
                    ChannelType.facebook_messenger,
                    sender_id_str,
                    contact_name,
                    metadata if isinstance(metadata, dict) else None,
                )
                pending_attribution = _consume_pending_messenger_attribution(
                    db,
                    channel=channel,
                    page_id=page_id,
                )
                if pending_attribution:
                    attribution_metadata = pending_attribution
            if attribution_metadata:
                metadata["attribution"] = attribution_metadata
            if external_ref:
                metadata["provider_message_id"] = external_ref
            contact_name = (
                contact_name
                or (message.get("from", {}) if isinstance(message.get("from"), dict) else {}).get("name")
                or f"Facebook User {sender_id_str}"
            )
            parsed = FacebookMessengerWebhookPayload(
                contact_address=sender_id_str,
                contact_name=contact_name,
                message_id=external_id,
                page_id=page_id,
                body=text,
                received_at=received_at,
                metadata=metadata,
            )

            try:
                result_msg = receive_facebook_message(db, parsed)
                results.append(
                    {
                        "message_id": str(result_msg.id),
                        "status": "received",
                    }
                )
            except Exception as exc:
                logger.exception(
                    "messenger_webhook_processing_failed page_id=%s error=%s",
                    page_id,
                    exc,
                )
                write_dead_letter(
                    channel="facebook_messenger",
                    raw_payload={"sender": sender, "message": message, "page_id": page_id},
                    error=exc,
                    message_id=message.get("mid"),
                )
                results.append(
                    {
                        "message_id": None,
                        "status": "failed",
                        "error": str(exc),
                    }
                )

    return results


def process_instagram_webhook(
    db: Session,
    payload: MetaWebhookPayload,
) -> list[dict]:
    """Process Instagram webhook payload.

    Args:
        db: Database session
        payload: Validated MetaWebhookPayload

    Returns:
        List of result dicts with message_id and status
    """
    results = []

    _target, config = _resolve_meta_connector(db, ConnectorType.facebook)
    base_url = _get_meta_graph_base_url(db)

    for entry in payload.entry:
        ig_account_id = entry.id
        ig_token = None
        if config:
            token = _find_token_for_account(db, config.id, "instagram_business", ig_account_id)
            ig_token = token.access_token if token else None

        if entry.changes:
            results.extend(_process_instagram_comment_changes(db, entry))

        if not entry.messaging:
            continue

        for messaging_event in entry.messaging:
            if messaging_event.postback and not messaging_event.message:
                logger.info(
                    "instagram_webhook_postback_ignored ig_account_id=%s sender_id=%s",
                    ig_account_id,
                    (messaging_event.sender or {}).get("id"),
                )
                continue
            if messaging_event.delivery and not messaging_event.message:
                logger.info(
                    "instagram_webhook_delivery_ignored ig_account_id=%s sender_id=%s",
                    ig_account_id,
                    (messaging_event.sender or {}).get("id"),
                )
                continue
            if messaging_event.read and not messaging_event.message:
                sender_id = (messaging_event.sender or {}).get("id")
                recipient_id = (messaging_event.recipient or {}).get("id")
                contact_id = sender_id
                if sender_id == ig_account_id:
                    contact_id = recipient_id
                _apply_meta_read_receipt(
                    db,
                    ChannelType.instagram_dm,
                    contact_id,
                    (messaging_event.read or {}).get("watermark"),
                )
                continue
            if not messaging_event.message:
                continue

            message = messaging_event.message
            sender = messaging_event.sender or {}

            attachments = _normalize_meta_message_attachments(message.get("attachments"))
            if not attachments:
                fetched_attachments = _fetch_instagram_message_attachments(
                    ig_token,
                    message.get("mid"),
                    base_url,
                )
                if fetched_attachments:
                    attachments = fetched_attachments
            logger.info(
                "instagram_webhook_message_keys message_id=%s keys=%s attachments_count=%s",
                message.get("mid"),
                list(message.keys()),
                len(attachments),
            )
            if attachments:
                logger.info(
                    "instagram_webhook_attachments message_id=%s attachments=%s",
                    message.get("mid"),
                    attachments,
                )

            # Skip echo messages
            if message.get("is_echo"):
                continue

            sender_id = sender.get("id")
            if not sender_id:
                logger.warning(
                    "instagram_webhook_missing_sender ig_account_id=%s",
                    ig_account_id,
                )
                continue
            if sender_id == ig_account_id:
                logger.info(
                    "instagram_webhook_skip_self ig_account_id=%s sender_id=%s",
                    ig_account_id,
                    sender_id,
                )
                continue
            logger.info(
                "instagram_webhook_ids ig_account_id=%s sender_id=%s",
                ig_account_id,
                sender_id,
            )

            text = message.get("text")
            is_story_mention = _attachments_have_story_mention(attachments)
            if not text:
                location_metadata = _extract_location_from_attachments(attachments)
                if location_metadata and not is_story_mention:
                    loc_label = (
                        location_metadata.get("label")
                        or location_metadata.get("name")
                        or location_metadata.get("address")
                    )
                    lat = location_metadata.get("latitude")
                    lng = location_metadata.get("longitude")
                    if loc_label:
                        text = f"📍 {loc_label}"
                    elif lat is not None and lng is not None:
                        text = f"📍 https://maps.google.com/?q={lat},{lng}"
                    else:
                        text = "📍 Location shared"
                elif attachments and not is_story_mention:
                    text = "(attachment)"
                elif not is_story_mention:
                    continue

            received_at = None
            if messaging_event.timestamp:
                received_at = datetime.fromtimestamp(
                    messaging_event.timestamp / 1000,
                    tz=UTC,
                )

            external_id, external_ref = _normalize_external_id(message.get("mid"))
            metadata = {
                "attachments": message.get("attachments"),
            }
            location_metadata = _extract_location_from_attachments(attachments)
            if location_metadata:
                metadata.update(location_metadata)
            identity_metadata = _extract_identity_metadata(
                messaging_event.referral,
                message.get("metadata"),
                message.get("referral"),
                (message.get("referral") or {}).get("ref") if isinstance(message.get("referral"), dict) else None,
                messaging_event.postback.get("payload") if messaging_event.postback else None,
            )
            if identity_metadata:
                metadata.update(identity_metadata)
            attribution_metadata = _extract_meta_attribution(
                messaging_event.referral,
                message.get("referral"),
                message.get("metadata"),
                messaging_event.postback,
                messaging_event.postback.get("payload") if messaging_event.postback else None,
            )
            if attribution_metadata:
                metadata["attribution"] = attribution_metadata
            if external_ref:
                metadata["provider_message_id"] = external_ref
            sender_username = sender.get("username") or (
                (message.get("from", {}) if isinstance(message.get("from"), dict) else {}).get("username")
            )
            fetched_profile_name = _fetch_profile_name(ig_token, sender_id, "username,name", base_url)
            sender_name = sender.get("name") or fetched_profile_name
            sender_identity = _build_instagram_sender_identity_metadata(
                sender_id=sender_id,
                sender_username=sender_username if isinstance(sender_username, str) else None,
                sender_name=sender_name if isinstance(sender_name, str) else None,
            )
            metadata.update(sender_identity)
            contact_name = (
                sender_identity.get("sender_username")
                or sender_identity.get("sender_name")
                or f"Instagram User {sender_id}"
            )
            parsed = InstagramDMWebhookPayload(
                contact_address=sender_id,
                contact_name=contact_name,
                message_id=external_id,
                instagram_account_id=ig_account_id,
                body=text,
                received_at=received_at,
                metadata=metadata,
            )

            try:
                result_msg = receive_instagram_message(db, parsed)
                results.append(
                    {
                        "message_id": str(result_msg.id),
                        "status": "received",
                    }
                )
            except Exception as exc:
                logger.exception(
                    "instagram_webhook_processing_failed ig_account_id=%s error=%s",
                    ig_account_id,
                    exc,
                )
                write_dead_letter(
                    channel="instagram_dm",
                    raw_payload={"sender": sender, "message": message, "ig_account_id": ig_account_id},
                    error=exc,
                    message_id=message.get("mid"),
                )
                results.append(
                    {
                        "message_id": None,
                        "status": "failed",
                        "error": str(exc),
                    }
                )

    return results


def _parse_webhook_timestamp(value) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(float(value), tz=UTC)
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.endswith("Z"):
                candidate = candidate.replace("Z", "+00:00")
            if candidate.endswith("+0000"):
                candidate = candidate[:-5] + "+00:00"
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except Exception:
        return None
    return None


def _process_facebook_leadgen_changes(
    db: Session,
    *,
    entry,
    page_token: str | None,
    base_url: str,
) -> list[dict]:
    results = []
    for change in entry.changes or []:
        value = change.get("value") or {}
        if change.get("field") != "leadgen":
            continue
        leadgen_id = value.get("leadgen_id")
        if not leadgen_id:
            logger.warning("facebook_leadgen_missing_id page_id=%s payload=%s", entry.id, value)
            results.append({"leadgen_id": None, "status": "failed", "error": "missing leadgen_id"})
            continue
        try:
            detail = _fetch_meta_lead_details(
                db,
                access_token=page_token,
                leadgen_id=leadgen_id,
                base_url=base_url,
            )
            lead = _store_meta_lead_submission(
                db,
                page_id=entry.id,
                leadgen_id=leadgen_id,
                detail=detail,
                change_value=value,
            )
            results.append(
                {
                    "leadgen_id": leadgen_id,
                    "lead_id": str(lead.id),
                    "status": "stored",
                }
            )
        except Exception as exc:
            logger.exception(
                "facebook_leadgen_processing_failed page_id=%s leadgen_id=%s error=%s", entry.id, leadgen_id, exc
            )
            write_dead_letter(
                channel="facebook_leadgen",
                raw_payload={"page_id": entry.id, "change": change, "leadgen_id": leadgen_id},
                error=exc,
                message_id=leadgen_id,
            )
            results.append(
                {
                    "leadgen_id": leadgen_id,
                    "status": "failed",
                    "error": str(exc),
                }
            )
    return results


def _process_facebook_comment_changes(
    db: Session,
    entry,
) -> list[dict]:
    results = []
    for change in entry.changes or []:
        raw_value = change.get("value")
        value = raw_value if isinstance(raw_value, dict) else {}
        if change.get("field") != "feed":
            continue
        if value.get("item") != "comment":
            continue
        raw_from = value.get("from")
        from_data = raw_from if isinstance(raw_from, dict) else {}
        sender_id = value.get("sender_id") or value.get("from_id") or from_data.get("id")
        if sender_id and sender_id == entry.id:
            logger.info(
                "facebook_comment_skip_self page_id=%s sender_id=%s",
                entry.id,
                sender_id,
            )
            continue
        try:
            payload = FacebookCommentPayload(
                post_id=value.get("post_id") or "",
                comment_id=value.get("comment_id") or "",
                parent_id=value.get("parent_id"),
                from_id=sender_id or "",
                from_name=value.get("sender_name") or from_data.get("name"),
                message=value.get("message") or value.get("text") or "",
                created_time=_parse_webhook_timestamp(value.get("created_time")) or datetime.now(UTC),
                page_id=entry.id,
            )
        except Exception as exc:
            logger.warning("facebook_comment_payload_invalid %s", exc)
            write_dead_letter(
                channel="facebook_comment",
                raw_payload=value,
                error=exc,
                message_id=value.get("comment_id"),
            )
            continue

        try:
            if payload.parent_id and payload.parent_id != payload.post_id:
                reply = comments_service.upsert_social_comment_reply(
                    db=db,
                    platform=SocialCommentPlatform.facebook,
                    parent_external_id=payload.parent_id,
                    external_id=payload.comment_id,
                    message=payload.message,
                    created_time=payload.created_time,
                    raw_payload=value,
                    author_id=payload.from_id,
                    author_name=payload.from_name,
                )
                status = "stored" if reply else "skipped"
                results.append({"comment_id": payload.comment_id, "status": status})
            else:
                comments_service.upsert_social_comment(
                    db=db,
                    platform=SocialCommentPlatform.facebook,
                    external_id=payload.comment_id,
                    external_post_id=payload.post_id,
                    source_account_id=payload.page_id,
                    author_id=payload.from_id,
                    author_name=payload.from_name,
                    message=payload.message,
                    created_time=payload.created_time,
                    permalink_url=None,
                    raw_payload=value,
                )
                results.append({"comment_id": payload.comment_id, "status": "stored"})
        except Exception as exc:
            logger.warning("facebook_comment_store_failed %s", exc)
            write_dead_letter(
                channel="facebook_comment",
                raw_payload=value,
                error=exc,
                message_id=payload.comment_id,
            )
            results.append({"comment_id": payload.comment_id, "status": "failed"})
    return results


def _process_instagram_comment_changes(
    db: Session,
    entry,
) -> list[dict]:
    results = []
    for change in entry.changes or []:
        value = change.get("value") or {}
        if change.get("field") != "comments":
            continue
        sender_id = (value.get("from") or {}).get("id")
        if sender_id and sender_id == entry.id:
            logger.info(
                "instagram_comment_skip_self ig_account_id=%s sender_id=%s",
                entry.id,
                sender_id,
            )
            continue
        try:
            payload = InstagramCommentPayload(
                media_id=value.get("media_id") or "",
                comment_id=value.get("comment_id") or value.get("id") or "",
                from_id=(value.get("from") or {}).get("id") or "",
                from_username=(value.get("from") or {}).get("username"),
                text=value.get("text") or "",
                timestamp=_parse_webhook_timestamp(value.get("timestamp")) or datetime.now(UTC),
                instagram_account_id=entry.id,
            )
        except Exception as exc:
            logger.warning("instagram_comment_payload_invalid %s", exc)
            write_dead_letter(
                channel="instagram_comment",
                raw_payload=value,
                error=exc,
                message_id=value.get("comment_id") or value.get("id"),
            )
            continue

        try:
            parent_id = value.get("parent_id")
            if parent_id:
                reply = comments_service.upsert_social_comment_reply(
                    db=db,
                    platform=SocialCommentPlatform.instagram,
                    parent_external_id=parent_id,
                    external_id=payload.comment_id,
                    message=payload.text,
                    created_time=payload.timestamp,
                    raw_payload=value,
                    author_id=payload.from_id,
                    author_name=payload.from_username,
                )
                status = "stored" if reply else "skipped"
                results.append({"comment_id": payload.comment_id, "status": status})
            else:
                comments_service.upsert_social_comment(
                    db=db,
                    platform=SocialCommentPlatform.instagram,
                    external_id=payload.comment_id,
                    external_post_id=payload.media_id,
                    source_account_id=payload.instagram_account_id,
                    author_id=payload.from_id,
                    author_name=payload.from_username,
                    message=payload.text,
                    created_time=payload.timestamp,
                    permalink_url=None,
                    raw_payload=value,
                )
                results.append({"comment_id": payload.comment_id, "status": "stored"})
        except Exception as exc:
            logger.warning("instagram_comment_store_failed %s", exc)
            write_dead_letter(
                channel="instagram_comment",
                raw_payload=value,
                error=exc,
                message_id=payload.comment_id,
            )
            results.append({"comment_id": payload.comment_id, "status": "failed"})
    return results


def receive_facebook_message(
    db: Session,
    payload: FacebookMessengerWebhookPayload,
):
    """Process an inbound Facebook Messenger message.

    Creates or updates contact, conversation, and message records.

    Args:
        db: Database session
        payload: Parsed Facebook Messenger webhook payload

    Returns:
        Message record
    """
    received_at = payload.received_at or datetime.now(UTC)

    # Find Meta connector/target
    target, _config = _resolve_meta_connector(db, ConnectorType.facebook)

    # Create/get contact with Facebook Messenger channel
    contact, channel = _resolve_meta_person_and_channel(
        db,
        ChannelType.facebook_messenger,
        payload.contact_address,
        payload.contact_name,
        payload.metadata,
    )

    external_id = payload.message_id
    if not external_id:
        external_id = _build_inbound_dedupe_id(
            ChannelType.facebook_messenger,
            payload.contact_address,
            None,
            payload.body,
            payload.received_at,
            source_id=payload.page_id,
        )

    # Check for duplicate message
    existing = _find_duplicate_inbound_message(
        db,
        ChannelType.facebook_messenger,
        channel.id,
        target.id if target else None,
        external_id,
        None,  # No subject for Messenger
        payload.body,
        received_at,
        dedupe_across_targets=True,
    )
    if existing:
        logger.debug(
            "duplicate_messenger_message message_id=%s",
            external_id,
        )
        return existing

    # Resolve or create conversation
    conversation = conversation_service.resolve_open_conversation_for_channel(
        db,
        str(contact.id),
        ChannelType.facebook_messenger,
    )
    if not conversation:
        conversation = conversation_service.Conversations.create(
            db,
            ConversationCreate(
                person_id=contact.id,
                is_active=True,
            ),
        )
    elif not conversation.is_active:
        conversation.is_active = True
        conversation.status = ConversationStatus.open
        db.commit()
        db.refresh(conversation)

    metadata = dict(payload.metadata or {})
    metadata["page_id"] = payload.page_id
    external_ref = _normalize_external_ref(metadata.get("provider_message_id"))
    attribution = metadata.get("attribution") if isinstance(metadata.get("attribution"), dict) else None
    _persist_meta_attribution_to_person_and_lead(
        db,
        person=contact,
        channel=ChannelType.facebook_messenger,
        attribution=attribution,
    )
    _persist_meta_attribution_to_conversation(
        db,
        conversation=conversation,
        channel=ChannelType.facebook_messenger,
        attribution=attribution,
    )

    # Create message
    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=channel.id,
            channel_target_id=target.id if target else None,
            channel_type=ChannelType.facebook_messenger,
            direction=MessageDirection.inbound,
            status=MessageStatus.received,
            body=payload.body,
            external_id=external_id,
            external_ref=external_ref,
            received_at=received_at,
            metadata_=metadata,
        ),
    )

    logger.info(
        "received_facebook_message contact_id=%s message_id=%s page_id=%s",
        contact.id,
        message.id,
        payload.page_id,
    )
    # Ensure inbox websocket updates/notifications fire for Meta inbound messages.
    post_process_inbound_message(
        db,
        conversation_id=str(conversation.id),
        message_id=str(message.id),
        channel_target_id=str(target.id) if target else None,
    )

    return message


def receive_instagram_message(
    db: Session,
    payload: InstagramDMWebhookPayload,
):
    """Process an inbound Instagram DM.

    Creates or updates contact, conversation, and message records.

    Args:
        db: Database session
        payload: Parsed Instagram DM webhook payload

    Returns:
        Message record
    """
    received_at = payload.received_at or datetime.now(UTC)
    body = payload.body if payload.body is not None else "(story mention)"

    # Find Meta connector/target (Instagram uses same connector as Facebook)
    target, _config = _resolve_meta_connector(db, ConnectorType.facebook)

    # Create/get contact with Instagram DM channel
    contact, channel = _resolve_meta_person_and_channel(
        db,
        ChannelType.instagram_dm,
        payload.contact_address,
        payload.contact_name,
        payload.metadata,
    )
    _persist_instagram_sender_identity(
        person=contact,
        channel=channel,
        metadata=payload.metadata,
    )

    external_id = payload.message_id
    if not external_id:
        external_id = _build_inbound_dedupe_id(
            ChannelType.instagram_dm,
            payload.contact_address,
            None,
            body,
            payload.received_at,
            source_id=payload.instagram_account_id,
        )

    # Check for duplicate message
    existing = _find_duplicate_inbound_message(
        db,
        ChannelType.instagram_dm,
        channel.id,
        target.id if target else None,
        external_id,
        None,
        body,
        received_at,
        dedupe_across_targets=True,
    )
    if existing:
        logger.debug(
            "duplicate_instagram_message message_id=%s",
            external_id,
        )
        return existing

    # Resolve or create conversation
    conversation = conversation_service.resolve_open_conversation_for_channel(
        db,
        str(contact.id),
        ChannelType.instagram_dm,
    )
    if not conversation:
        conversation = conversation_service.Conversations.create(
            db,
            ConversationCreate(
                person_id=contact.id,
                is_active=True,
            ),
        )
    elif not conversation.is_active:
        conversation.is_active = True
        conversation.status = ConversationStatus.open
        db.commit()
        db.refresh(conversation)

    metadata = dict(payload.metadata or {})
    metadata["instagram_account_id"] = payload.instagram_account_id
    external_ref = _normalize_external_ref(metadata.get("provider_message_id"))
    attribution = metadata.get("attribution") if isinstance(metadata.get("attribution"), dict) else None
    _persist_meta_attribution_to_person_and_lead(
        db,
        person=contact,
        channel=ChannelType.instagram_dm,
        attribution=attribution,
    )
    _persist_meta_attribution_to_conversation(
        db,
        conversation=conversation,
        channel=ChannelType.instagram_dm,
        attribution=attribution,
    )

    # Create message
    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=channel.id,
            channel_target_id=target.id if target else None,
            channel_type=ChannelType.instagram_dm,
            direction=MessageDirection.inbound,
            status=MessageStatus.received,
            body=body,
            external_id=external_id,
            external_ref=external_ref,
            received_at=received_at,
            metadata_=metadata,
        ),
    )

    logger.info(
        "received_instagram_message contact_id=%s message_id=%s ig_account_id=%s",
        contact.id,
        message.id,
        payload.instagram_account_id,
    )
    # Ensure inbox websocket updates/notifications fire for Meta inbound messages.
    post_process_inbound_message(
        db,
        conversation_id=str(conversation.id),
        message_id=str(message.id),
        channel_target_id=str(target.id) if target else None,
    )

    return message
